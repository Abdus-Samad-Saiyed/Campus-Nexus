"""Views for the accounts app.

URL summary:
  /                           → landing (public)
  /dashboard/                 → dashboard (@login_required)
  /profile/<enrollment>/      → profile view (@login_required)
  /apply/                     → application form (public, throttled)
  /apply/success/             → success page (public)
  /applications/              → superuser: list applications
  /applications/<id>/approve/ → superuser: approve application (POST only)
  /applications/<id>/reject/  → superuser: reject application (POST only)
  /analytics/                 → superuser: analytics dashboard
  /friends/<enrollment>/request/  → send friend request (POST only)
  /friends/<id>/accept/           → accept friend request (POST only)
"""

import json
import logging
import os
import sys
import time
import uuid
from functools import wraps
from datetime import timedelta
from PIL import Image

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, connections, transaction

logger = logging.getLogger(__name__)
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from coordinate.models import RideParticipant, RideRequest

from .forms import AccountApplicationForm, EarlyLaunchDeclarationForm, EnrollmentAuthenticationForm
from .notifications import send_user_notification
from .models import (
    AccountApplication,
    AuditEvent,
    DirectMessage,
    DirectMessageReport,
    Friendship,
    FriendRequest,
    PasskeyCredential,
    PhoneOTP,
    PhysicalVerificationRequest,
    User,
    log_audit_event,
)
from .throttling import rate_limit

def verified_student_required(view_func):
    """Decorator to ensure user is active, verified, and not soft-deleted."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        from accounts.demo import is_guest_demo
        if is_guest_demo(request):
            messages.error(request, "This area is restricted in Guest Demo Mode to protect student privacy.")
            return redirect('dashboard')
        from coordinate.policies import is_verified_student
        if request.user.is_staff or request.user.is_superuser or is_verified_student(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(request, "Access restricted to verified students. Your account verification is pending.")
        return redirect('dashboard')
    return _wrapped

from .two_factor import (
    can_resend_otp,
    create_phone_otp,
    generate_webauthn_challenge,
    verify_passkey_assertion,
    verify_passkey_registration,
    verify_phone_otp,
    normalize_phone_number,
)

from campus.services import get_all_services
from coordinate.policies import (
    can_view_profile_field,
    can_view_student_directory,
    is_verified_student,
)

# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------

def _is_superuser(user):
    return user.is_active and user.is_superuser


# ---------------------------------------------------------------------------
# Public views
# ---------------------------------------------------------------------------

def landing(request):
    """Public landing page presenting the Campus Nexus umbrella platform."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    services = get_all_services()
    return render(request, 'landing.html', {
        'services': services,
    })


def service_catalogue(request):
    """Campus Nexus Service Directory accessible by all users."""
    services = get_all_services()
    return render(request, 'nexus/catalogue.html', {
        'services': services,
    })


def enforce_single_session_login(request, user: User):
    """
    Ensure each account can only have one active session at a time.
    Invalidates any previous session across other devices/browsers, and records current session key.
    """
    if not request.session.session_key:
        request.session.save()
    current_key = request.session.session_key

    # Fast path: revoke previously recorded session key directly from cache and database
    if user.current_session_key and user.current_session_key != current_key:
        try:
            from django.contrib.sessions.backends.cached_db import SessionStore as CachedSessionStore
            CachedSessionStore(session_key=user.current_session_key).delete()
        except Exception:
            pass
        Session.objects.filter(session_key=user.current_session_key).delete()

    prior_terminated = False
    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            data = s.get_decoded()
            if str(data.get('_auth_user_id')) == str(user.pk) and s.session_key != current_key:
                try:
                    from django.contrib.sessions.backends.cached_db import SessionStore as CachedSessionStore
                    CachedSessionStore(session_key=s.session_key).delete()
                except Exception:
                    pass
                s.delete()
                prior_terminated = True
                log_audit_event(
                    event_type='SESSION_TERMINATED_CONCURRENT',
                    actor_enrollment=user.enrollment_number,
                    description='Prior session invalidated on concurrent device/browser login',
                )
        except Exception:
            pass

    user.current_session_key = current_key
    user.save(update_fields=['current_session_key'])
    request.session['last_activity'] = time.time()

    try:
        from accounts.client_logger import register_client_identity
        enrollment = getattr(user, 'enrollment_number', '') or getattr(user, 'username', 'N.A.')
        full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
        display_name = full_name.strip() or getattr(user, 'display_name', '') or getattr(user, 'username', '') or enrollment
        register_client_identity(
            session_key=current_key,
            enrollment=enrollment,
            name=display_name,
        )
    except Exception:
        pass

    if prior_terminated:
        from accounts.utils import broadcast_user_session_revocation
        transaction.on_commit(lambda: broadcast_user_session_revocation(user.pk, "Prior session invalidated on concurrent login."))


def _safe_redirect_url(request, redirect_to, fallback='dashboard'):
    """Ensure redirection target belongs to the current host and scheme."""
    if redirect_to and url_has_allowed_host_and_scheme(
        url=redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect_to
    return fallback


@rate_limit(key_prefix='login', limit=10, period_seconds=60)
def custom_login_view(request, extra_context=None):
    """Enrollment-based authentication with single concurrent session & 2FA intercept.
    Compatible with Django admin login redirection (admin.site.login = custom_login_view).
    """
    if request.GET.get('reason') == 'inactivity':
        if request.user.is_authenticated:
            auth_logout(request)
        messages.info(request, "Your session has expired due to inactivity. Please sign in again.")
        return redirect('login')

    if request.user.is_authenticated:
        return redirect('dashboard')

    redirect_to = _safe_redirect_url(request, request.POST.get('next', request.GET.get('next', '')), fallback='')

    if request.method == 'POST':
        form = EnrollmentAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # Synthetic branch debug accounts (20XX...) bypass 2FA only in debug/test/alpha environments
            if user.is_debug_account:
                auth_login(request, user)
                enforce_single_session_login(request, user)
                request.session['auth_timestamp'] = timezone.now().timestamp()
                log_audit_event(
                    event_type='LOGIN_SUCCESS',
                    actor_enrollment=user.enrollment_number,
                    description='Debug account signed in successfully (2FA bypassed)',
                )
                return redirect(redirect_to or 'dashboard')

            # Real accounts: Staff, Superusers, or users with 2FA enabled require 2FA OTP verification
            if user.two_factor_enabled or user.is_staff or user.is_superuser:
                request.session['pre_2fa_user_id'] = user.pk
                request.session['pre_2fa_redirect'] = redirect_to
                request.session['pre_2fa_time'] = timezone.now().timestamp()
                can_resend, _ = can_resend_otp(user, purpose='2FA')
                if can_resend:
                    otp, raw_code = create_phone_otp(user, purpose='2FA')
                    if not getattr(otp, 'any_delivered', True):
                        messages.warning(request, "Unable to deliver verification code to your email or SMS. If you have registered a passkey, you may authenticate using it below.")
                return redirect('two_factor_verify')

            auth_login(request, user)
            enforce_single_session_login(request, user)
            request.session['auth_timestamp'] = timezone.now().timestamp()
            log_audit_event(
                event_type='LOGIN_SUCCESS',
                actor_enrollment=user.enrollment_number,
                description='Student signed in successfully',
            )
            return redirect(redirect_to or 'dashboard')
        else:
            login_username = request.POST.get('username', '').strip().upper()
            log_audit_event(
                event_type='LOGIN_FAILURE',
                actor_enrollment=login_username[:20],
                description='Failed login attempt',
            )
            if not getattr(settings, 'EARLY_LAUNCH_MODE', False):
                try:
                    from django.db import connections
                    if 'temp_early_access' in connections:
                        from accounts.models import User as UserModel
                        if UserModel.objects.using('temp_early_access').filter(enrollment_number=login_username).exists():
                            messages.info(
                                request,
                                "Notice: This account was registered during the temporary Early Launch period. Early Launch Mode is currently closed. To access Campus Nexus for regular semester services, please submit a verified student application."
                            )
                except Exception:
                    pass
    else:
        form = EnrollmentAuthenticationForm(request)

    context = {
        'form': form,
        'next': redirect_to,
    }
    if extra_context:
        context.update(extra_context)

    return render(request, 'registration/login.html', context)


@rate_limit(key_prefix='two_factor_verify', limit=10, period_seconds=60)
def two_factor_verify(request):
    """Handle 2FA Phone SMS verification and Passkey bypass options."""
    user_id = request.session.get('pre_2fa_user_id')
    pre_2fa_time = request.session.get('pre_2fa_time')
    now_ts = timezone.now().timestamp()

    # Expire pre-2FA session state after 5 minutes (300 seconds)
    if not user_id or not pre_2fa_time or (now_ts - pre_2fa_time) > 300:
        request.session.pop('pre_2fa_user_id', None)
        request.session.pop('pre_2fa_redirect', None)
        request.session.pop('pre_2fa_time', None)
        messages.error(request, "Authentication session expired. Please sign in again.")
        return redirect('login')

    user = get_object_or_404(User, id=user_id)
    if not user.is_active or getattr(user, 'is_deleted', False):
        request.session.pop('pre_2fa_user_id', None)
        request.session.pop('pre_2fa_redirect', None)
        request.session.pop('pre_2fa_time', None)
        messages.error(request, "This account is inactive or disabled.")
        return redirect('login')
    redirect_to = _safe_redirect_url(request, request.session.get('pre_2fa_redirect', 'dashboard'))

    p = user.phone_number or ''
    masked_phone = f"{p[:3]} {'*' * max(0, len(p) - 6)} {p[-3:]}" if len(p) >= 6 else p
    e = user.email or ''
    masked_email = f"{e[:2]}***@{e.split('@')[1]}" if '@' in e else e

    registered_passkeys = list(
        PasskeyCredential.objects.filter(user=user)
        .values('credential_id', 'name')
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'resend':
            can_resend, wait_seconds = can_resend_otp(user, purpose='2FA')
            if can_resend:
                otp, raw_code = create_phone_otp(user, purpose='2FA')
                if not getattr(otp, 'any_delivered', True):
                    messages.warning(request, "Delivery failed. Could not dispatch verification code to your email or mobile. You may authenticate using your passkey if registered.")
                elif getattr(otp, 'email_sent', False) and not getattr(otp, 'sms_sent', False):
                    messages.info(request, f"A new 6-digit verification code has been dispatched to your email ({masked_email}). (SMS delivery is currently unavailable).")
                else:
                    messages.info(request, "A new 6-digit verification code has been dispatched to your email and phone.")
            else:
                messages.warning(request, f"Please wait {wait_seconds} seconds before requesting a new code.")
            return redirect('two_factor_verify')

        elif action == 'verify':
            otp_code = request.POST.get('otp_code', '').strip()
            success, msg = verify_phone_otp(user, submitted_code=otp_code, purpose='2FA')
            if success:
                request.session.pop('pre_2fa_user_id', None)
                request.session.pop('pre_2fa_redirect', None)
                request.session.pop('pre_2fa_time', None)

                auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                enforce_single_session_login(request, user)
                request.session['auth_timestamp'] = timezone.now().timestamp()
                request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=15)).isoformat()
                log_audit_event(
                    event_type='LOGIN_SUCCESS',
                    actor_enrollment=user.enrollment_number,
                    description='2FA OTP successfully verified via email/SMS',
                )
                messages.success(request, f"Identity verified. Welcome back, {user.display_name}!")
                return redirect(redirect_to or 'dashboard')
            else:
                messages.error(request, msg)

    return render(request, 'registration/two_factor.html', {
        'masked_phone': masked_phone,
        'masked_email': masked_email,
        'user_obj': user,
        'registered_passkeys': registered_passkeys,
    })


def _get_webauthn_origin_and_rp_id(request):
    """
    Extract the effective WebAuthn origin, server expected rp_id, and client rp_id from request.
    Handles Cloudflare Tunnels (trycloudflare.com), ngrok, custom domains,
    and local development (localhost / 127.0.0.1).
    """
    origin_header = request.headers.get('origin') or request.META.get('HTTP_ORIGIN')
    forwarded_host = request.META.get('HTTP_X_FORWARDED_HOST')
    forwarded_proto = request.META.get('HTTP_X_FORWARDED_PROTO')

    # 1. Determine origin
    if origin_header:
        effective_origin = origin_header.rstrip('/')
    elif forwarded_host:
        proto = forwarded_proto or ('https' if request.is_secure() else 'http')
        effective_origin = f"{proto}://{forwarded_host}"
    else:
        proto = 'https' if request.is_secure() else 'http'
        effective_origin = f"{proto}://{request.get_host()}"

    # 2. Extract domain/host without port
    from urllib.parse import urlparse
    parsed = urlparse(effective_origin) if effective_origin else None
    host_candidate = parsed.hostname if (parsed and parsed.hostname) else (forwarded_host or request.get_host()).split(':')[0]

    # WebAuthn rp.id cannot be an IP address (RFC 6265 / WebAuthn Level 2)
    import ipaddress
    try:
        ipaddress.ip_address(host_candidate)
        client_rp_id = None
        server_rp_id = host_candidate
    except ValueError:
        client_rp_id = host_candidate
        server_rp_id = host_candidate

    return effective_origin, server_rp_id, client_rp_id


def _get_webauthn_rp_id(request):
    """Return a valid domain string for WebAuthn rp.id or None if host is an IP address."""
    _, _, client_rp_id = _get_webauthn_origin_and_rp_id(request)
    return client_rp_id


@rate_limit(key_prefix='passkey_auth_chal', limit=10, period_seconds=60)
def passkey_auth_challenge(request):
    """Generate WebAuthn challenge for bypassing 2FA with biometric / Windows Hello passkey."""
    user_id = request.session.get('pre_2fa_user_id') or (request.user.pk if request.user.is_authenticated else None)
    if not user_id:
        return JsonResponse({'error': 'No authentication session in progress.'}, status=401)

    pre_2fa_time = request.session.get('pre_2fa_time')
    now_ts = timezone.now().timestamp()
    if request.session.get('pre_2fa_user_id') and (not pre_2fa_time or (now_ts - pre_2fa_time) > 300):
        return JsonResponse({'error': 'Authentication session expired. Please sign in again.'}, status=401)

    user = get_object_or_404(User, id=user_id)
    challenge = generate_webauthn_challenge()
    request.session['passkey_challenge'] = challenge
    request.session['passkey_challenge_time'] = now_ts

    credentials = list(
        PasskeyCredential.objects.filter(user=user)
        .values_list('credential_id', flat=True)
    )

    return JsonResponse({
        'challenge': challenge,
        'credential_ids': credentials,
        'rp_id': _get_webauthn_rp_id(request),
    })


@rate_limit(key_prefix='passkey_auth_ver', limit=10, period_seconds=60)
@require_POST
def passkey_auth_verify(request):
    """Verify WebAuthn client assertion and bypass 2FA upon biometric / Windows Hello match."""
    user_id = request.session.get('pre_2fa_user_id')
    pre_2fa_time = request.session.get('pre_2fa_time')
    now_ts = timezone.now().timestamp()

    if not user_id or not pre_2fa_time or (now_ts - pre_2fa_time) > 300:
        return JsonResponse({'success': False, 'error': 'Session expired. Please sign in again.'}, status=401)

    user = get_object_or_404(User, id=user_id)
    if not user.is_active or getattr(user, 'is_deleted', False):
        request.session.pop('pre_2fa_user_id', None)
        request.session.pop('pre_2fa_redirect', None)
        request.session.pop('pre_2fa_time', None)
        return JsonResponse({'success': False, 'error': 'Account is inactive or disabled.'}, status=403)
    redirect_to = _safe_redirect_url(request, request.session.get('pre_2fa_redirect', '/dashboard/'), fallback='/dashboard/')
    challenge = request.session.pop('passkey_challenge', '')
    challenge_time = request.session.pop('passkey_challenge_time', None)
    if not challenge or not challenge_time or (now_ts - challenge_time) > 120:
        return JsonResponse({'success': False, 'error': 'Challenge already consumed, expired, or missing. Please try again.'}, status=400)

    try:
        data = json.loads(request.body.decode('utf-8'))
        credential_id = data.get('credential_id') or data.get('id', '')
        client_data_json = data.get('client_data_json') or data.get('response', {}).get('clientDataJSON', '')
        effective_origin, server_rp_id, _ = _get_webauthn_origin_and_rp_id(request)

        full_cred = data if ('response' in data and 'authenticatorData' in data['response']) else None

        success, msg = verify_passkey_assertion(
            user=user,
            credential_id=credential_id,
            client_data_json_b64=client_data_json,
            session_challenge=challenge,
            full_credential=full_cred,
            expected_rp_id=server_rp_id,
            expected_origin=effective_origin,
        )
        if success:
            request.session.pop('pre_2fa_user_id', None)
            request.session.pop('pre_2fa_redirect', None)
            request.session.pop('pre_2fa_time', None)

            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            enforce_single_session_login(request, user)
            request.session['auth_timestamp'] = timezone.now().timestamp()
            request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=15)).isoformat()
            log_audit_event(
                event_type='LOGIN_SUCCESS',
                actor_enrollment=user.enrollment_number,
                description='2FA bypassed via Windows Hello / Biometric Passkey',
            )
            return JsonResponse({'success': True, 'redirect_url': redirect_to or '/dashboard/'})
        else:
            return JsonResponse({'success': False, 'error': msg}, status=400)
    except Exception as exc:
        logger.warning("Passkey assertion error: %s", type(exc).__name__)
        return JsonResponse({'success': False, 'error': 'Invalid passkey assertion request.'}, status=400)


@login_required
@rate_limit(key_prefix='passkey_reg_chal', limit=10, period_seconds=60)
def passkey_reg_challenge(request):
    """Generate registration challenge for saving a biometric / Windows Hello key. Requires recent reauthentication."""
    sec_until = request.session.get('security_otp_verified_until')
    grace_active = False
    if sec_until:
        try:
            from datetime import datetime as dt_cls
            grace_active = timezone.now() < dt_cls.fromisoformat(sec_until)
        except Exception:
            pass
    if not grace_active and not getattr(request.user, 'is_debug_account', False):
        return JsonResponse({
            'success': False,
            'error': 'Recent re-authentication required to register passkeys. Please verify your identity first.',
            'requires_step_up': True,
        }, status=403)

    challenge = generate_webauthn_challenge()
    request.session['passkey_reg_challenge'] = challenge
    request.session['passkey_reg_challenge_time'] = timezone.now().timestamp()
    return JsonResponse({
        'challenge': challenge,
        'user_id': str(request.user.pk),
        'user_name': request.user.enrollment_number,
        'display_name': request.user.display_name,
        'rp_id': _get_webauthn_rp_id(request),
    })


def get_device_of_origin_from_ua(user_agent: str) -> str:
    """Extract human-readable device of origin and browser from User-Agent string."""
    if not user_agent:
        return "Biometric Key"
    ua = user_agent
    if "Windows" in ua:
        device = "Windows PC"
    elif "iPhone" in ua:
        device = "iPhone"
    elif "iPad" in ua:
        device = "iPad"
    elif "Macintosh" in ua or "Mac OS" in ua:
        device = "Mac"
    elif "Android" in ua:
        device = "Android"
    elif "Linux" in ua:
        device = "Linux"
    elif "CrOS" in ua:
        device = "Chromebook"
    else:
        device = "Biometric Key"

    if "Edg/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua and "Edg/" not in ua:
        browser = "Chrome"
    elif "Safari/" in ua and "Chrome/" not in ua:
        browser = "Safari"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Opera" in ua or "OPR/" in ua:
        browser = "Opera"
    else:
        browser = ""

    return f"{device} ({browser})" if browser else device

@login_required
@rate_limit(key_prefix='passkey_reg_ver', limit=10, period_seconds=60)
@require_POST
def passkey_reg_verify(request):
    """Register and store new PasskeyCredential for the authenticated student. Requires recent reauthentication."""
    sec_until = request.session.get('security_otp_verified_until')
    grace_active = False
    if sec_until:
        try:
            from datetime import datetime as dt_cls
            grace_active = timezone.now() < dt_cls.fromisoformat(sec_until)
        except Exception:
            pass
    if not grace_active and not getattr(request.user, 'is_debug_account', False):
        return JsonResponse({
            'success': False,
            'error': 'Recent re-authentication required to register passkeys.',
            'requires_step_up': True,
        }, status=403)

    now_ts = timezone.now().timestamp()
    challenge = request.session.pop('passkey_reg_challenge', '')
    challenge_time = request.session.pop('passkey_reg_challenge_time', None)

    if not challenge or not challenge_time or (now_ts - challenge_time) > 120:
        return JsonResponse({'success': False, 'error': 'Registration challenge expired, consumed, or missing.'}, status=400)

    try:
        data = json.loads(request.body.decode('utf-8'))

        # Label biometric keys by device of origin and time
        device_origin = data.get('device') or data.get('device_name')
        if not device_origin or device_origin in [
            'Windows Hello / Biometric Key',
            'Windows Hello / Biometric Passkey',
            'Windows Hello Device',
            'Biometric Key',
            'Passkey',
        ]:
            ua_header = request.META.get('HTTP_USER_AGENT', '')
            device_origin = get_device_of_origin_from_ua(ua_header)

        client_time = data.get('client_time')
        if not client_time:
            now = timezone.localtime(timezone.now())
            client_time = now.strftime("%b %d, %Y, %I:%M %p")

        raw_name = data.get('name', '').strip()
        default_placeholders = [
            'Windows Hello / Biometric Key',
            'Windows Hello / Biometric Passkey',
            'Windows Hello Device',
            'Passkey',
            'Biometric Key',
        ]
        if raw_name and raw_name not in default_placeholders:
            if "—" in raw_name or "•" in raw_name:
                key_name = raw_name
            else:
                key_name = f"{raw_name} — {client_time}"
        else:
            key_name = f"{device_origin} — {client_time}"

        effective_origin, server_rp_id, _ = _get_webauthn_origin_and_rp_id(request)

        success, msg, cred_info = verify_passkey_registration(
            data=data,
            session_challenge=challenge,
            expected_rp_id=server_rp_id,
            expected_origin=effective_origin,
        )

        if not success:
            return JsonResponse({'success': False, 'error': msg}, status=400)

        if PasskeyCredential.objects.filter(credential_id=cred_info['credential_id']).exclude(user=request.user).exists():
            return JsonResponse({'success': False, 'error': 'Unable to register this passkey.'}, status=400)

        PasskeyCredential.objects.update_or_create(
            user=request.user,
            credential_id=cred_info['credential_id'],
            defaults={
                'public_key': cred_info['public_key'],
                'sign_count': cred_info.get('sign_count', 0),
                'name': key_name[:100],
            }
        )

        request.user.two_factor_enabled = True
        request.user.save(update_fields=['two_factor_enabled'])

        log_audit_event(
            event_type='PASSKEY_REGISTERED',
            actor_enrollment=request.user.enrollment_number,
            description=f'Registered passkey credential: {key_name[:50]}',
        )

        return JsonResponse({'success': True, 'message': 'Passkey registered successfully!'})
    except Exception as exc:
        return JsonResponse({'success': False, 'error': 'Passkey registration error.'}, status=400)


@login_required
@rate_limit(key_prefix='ver_sec_otp', limit=10, period_seconds=60)
@require_POST
def verify_security_otp(request):
    """Validate submitted phone/email OTP inline to activate 10-minute session grace window without full page submission."""
    if getattr(request.user, 'is_debug_account', False):
        request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=10)).isoformat()
        return JsonResponse({'success': True, 'message': 'Debug account verified.'})

    try:
        data = json.loads(request.body.decode('utf-8'))
        code = data.get('otp_code', '').strip()
    except Exception:
        code = request.POST.get('otp_code', '').strip()

    if not code:
        return JsonResponse({'success': False, 'error': 'Please enter the 6-digit verification code.'}, status=400)

    ok, msg = verify_phone_otp(request.user, submitted_code=code, purpose='SECURITY_CHANGE')
    if not ok:
        return JsonResponse({'success': False, 'error': msg}, status=400)

    request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=10)).isoformat()
    return JsonResponse({
        'success': True,
        'message': 'Identity verified successfully! 10-minute grace window active.',
    })


@login_required
@rate_limit(key_prefix='step_up', limit=5, period_seconds=60)
@require_POST
def verify_security_step_up(request):
    """Validate current password and security OTP to unlock sensitive security actions for 5 minutes."""
    current_pw = request.POST.get('current_password', '').strip()
    if not current_pw or not request.user.check_password(current_pw):
        return JsonResponse({'success': False, 'error': 'Current password is required and must be correct.'}, status=400)

    if not request.user.is_debug_account:
        otp_code = request.POST.get('otp_code', '').strip()
        ok, msg = verify_phone_otp(request.user, submitted_code=otp_code, purpose='SECURITY_CHANGE')
        if not ok:
            return JsonResponse({'success': False, 'error': msg}, status=400)

    request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=5)).isoformat()
    return JsonResponse({'success': True, 'message': 'Security verification confirmed.'})

@login_required
@rate_limit(key_prefix='passkey_del', limit=10, period_seconds=60)
@require_POST
def passkey_delete(request, credential_id):
    """Delete a registered passkey credential after verifying recent reauthentication."""
    sec_until = request.session.get('security_otp_verified_until')
    grace_active = False
    if sec_until:
        try:
            from datetime import datetime as dt_cls
            grace_active = timezone.now() < dt_cls.fromisoformat(sec_until)
        except Exception:
            pass
    if not grace_active and not getattr(request.user, 'is_debug_account', False):
        return JsonResponse({
            'success': False,
            'error': 'Recent re-authentication required to delete passkeys.',
            'requires_step_up': True,
        }, status=403)

    cred = get_object_or_404(PasskeyCredential, id=credential_id, user=request.user)
    cred_name = cred.name
    cred.delete()

    log_audit_event(
        event_type='SECURITY_SETTINGS_UPDATED',
        actor_enrollment=request.user.enrollment_number,
        description=f'Deleted passkey credential: {cred_name[:50]}',
    )
    return JsonResponse({'success': True, 'message': 'Passkey deleted successfully.'})



@rate_limit(key_prefix='send_signup_otp', limit=5, period_seconds=60)
@require_POST
def send_signup_otp(request):
    """Dispatch phone and email OTP for student registration verification."""
    phone = request.POST.get('phone_number', '').strip()
    email = request.POST.get('email', '').strip().lower()
    if not phone or not email:
        try:
            body = json.loads(request.body.decode('utf-8'))
            phone = phone or body.get('phone_number', '').strip()
            email = email or body.get('email', '').strip().lower()
        except Exception:
            pass

    if not phone or len(phone) < 10:
        return JsonResponse({'success': False, 'error': 'Please provide a valid phone number.'}, status=400)

    can_resend, wait_sec = can_resend_otp(phone_number=phone, email=email, purpose='SIGNUP')
    if not can_resend:
        return JsonResponse({'success': False, 'error': f'Please wait {wait_sec} seconds before requesting a new code.'}, status=429)

    otp, raw_code = create_phone_otp(phone_number=phone, email=email, purpose='SIGNUP')
    if not getattr(otp, 'any_delivered', True):
        return JsonResponse({'success': False, 'error': 'Unable to deliver verification code via SMS or email. Please check your contact details or try again.'}, status=502)

    dest_parts = []
    if getattr(otp, 'email_sent', False) or (email and not getattr(otp, 'sms_sent', False)):
        dest_parts.append(f"email ({email})")
    if getattr(otp, 'sms_sent', False):
        dest_parts.append(f"mobile ({phone[-4:]})")
    dest_str = " and ".join(dest_parts) or f"email ({email})"
    return JsonResponse({
        'success': True,
        'message': f'Verification code dispatched to {dest_str}. Check your email inbox or SMS.',
    })


@login_required
@rate_limit(key_prefix='send_security_otp', limit=5, period_seconds=60)
@require_POST
def send_security_otp(request):
    """Dispatch phone and email OTP to currently logged-in student to authorize security edits.
    Strictly dispatches to the stored contact details of request.user; client overrides are ignored.
    """
    user = request.user
    if user.is_debug_account:
        request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=5)).isoformat()
        return JsonResponse({'success': True, 'message': 'Debug account: OTP bypassed.'})

    target_phone = user.phone_number
    target_email = user.email
    if not target_phone and not target_email:
        return JsonResponse({'success': False, 'error': 'No registered contact (phone or email) found on account.'}, status=400)

    can_resend, wait_sec = can_resend_otp(user=user, phone_number=target_phone, email=target_email, purpose='SECURITY_CHANGE')
    if not can_resend:
        return JsonResponse({'success': False, 'error': f'Please wait {wait_sec} seconds before requesting a new code.'}, status=429)

    otp, raw_code = create_phone_otp(user=user, phone_number=target_phone, email=target_email, purpose='SECURITY_CHANGE')
    if not getattr(otp, 'any_delivered', True):
        return JsonResponse({'success': False, 'error': 'Unable to deliver security code to your registered contacts. Please try again later.'}, status=502)

    dest_parts = []
    if getattr(otp, 'email_sent', False) or (target_email and not getattr(otp, 'sms_sent', False)):
        dest_parts.append(f"email ({target_email})")
    if getattr(otp, 'sms_sent', False):
        dest_parts.append(f"mobile ({target_phone[-4:]})")
    dest_str = " and ".join(dest_parts) or "your registered contacts"
    return JsonResponse({
        'success': True,
        'message': f'Verification code dispatched to {dest_str}. Check your email or SMS.',
    })


@rate_limit(key_prefix='forgot_password', limit=5, period_seconds=60)
def forgot_password_view(request):
    """Forgotten password recovery: Phone SMS / Email OTP reset or Physical In-Person Desk request."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    active_tab = 'otp'
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'request_otp':
            enrollment = request.POST.get('enrollment_number', '').strip().upper()
            user = User.objects.filter(enrollment_number=enrollment, is_deleted=False).first()
            if user:
                can_resend, wait_sec = can_resend_otp(user, purpose='PASSWORD_RESET')
                if can_resend:
                    create_phone_otp(user, purpose='PASSWORD_RESET')
                request.session['reset_user_id'] = user.pk
                request.session['reset_requested_at'] = timezone.now().timestamp()
            
            messages.info(
                request,
                "If an active account matches that enrollment number, a verification code has been dispatched to its registered contact channels."
            )
            return render(request, 'registration/forgot_password.html', {
                'step': 'verify_otp',
                'enrollment_number': enrollment,
            })

        elif action == 'verify_reset':
            user_id = request.session.get('reset_user_id')
            reset_req_at = request.session.get('reset_requested_at')
            now_ts = timezone.now().timestamp()

            if not user_id or not reset_req_at or (now_ts - reset_req_at) > 300:
                request.session.pop('reset_user_id', None)
                request.session.pop('reset_requested_at', None)
                messages.error(request, "Password reset session expired. Please request a new code.")
                return redirect('forgot_password')

            user = get_object_or_404(User, id=user_id)

            otp_code = request.POST.get('otp_code', '').strip()
            new_password = request.POST.get('new_password', '').strip()
            confirm_password = request.POST.get('confirm_password', '').strip()

            if new_password != confirm_password:
                messages.error(request, "Passwords do not match.")
                return render(request, 'registration/forgot_password.html', {
                    'step': 'verify_otp',
                    'enrollment_number': user.enrollment_number,
                })

            try:
                validate_password(new_password, user)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
                return render(request, 'registration/forgot_password.html', {
                    'step': 'verify_otp',
                    'enrollment_number': user.enrollment_number,
                })

            ok, msg = verify_phone_otp(user, submitted_code=otp_code, purpose='PASSWORD_RESET')
            if ok:
                user.set_password(new_password)
                # Revoke all active sessions for this user across all devices/API
                for s in Session.objects.filter(expire_date__gt=timezone.now()):
                    try:
                        data = s.get_decoded()
                        if str(data.get('_auth_user_id')) == str(user.pk):
                            s.delete()
                    except Exception:
                        pass
                user.current_session_key = ''
                user.save()
                request.session.pop('reset_user_id', None)
                request.session.pop('reset_requested_at', None)
                from accounts.utils import broadcast_user_session_revocation
                transaction.on_commit(lambda: broadcast_user_session_revocation(user.pk, "Password reset via verification code."))
                log_audit_event(
                    event_type='SECURITY_SETTINGS_UPDATED',
                    actor_enrollment=user.enrollment_number,
                    description='Password reset successfully via Phone OTP; existing sessions revoked',
                )
                messages.success(request, "Your password has been reset successfully! Please sign in with your new password.")
                return redirect('login')
            else:
                messages.error(request, msg)
                return render(request, 'registration/forgot_password.html', {
                    'step': 'verify_otp',
                    'enrollment_number': user.enrollment_number,
                })

        elif action == 'physical_request':
            enrollment = request.POST.get('enrollment_number', '').strip().upper()
            contact_email = request.POST.get('contact_email', '').strip()[:254]
            contact_phone = normalize_phone_number(request.POST.get('contact_phone', '').strip())[:20]
            student_notes = request.POST.get('student_notes', '').strip()[:500]

            user = User.objects.filter(enrollment_number=enrollment, is_deleted=False).first()
            if user:
                existing = PhysicalVerificationRequest.objects.filter(user=user, status='PENDING').first()
                if not existing:
                    PhysicalVerificationRequest.objects.create(
                        user=user,
                        contact_email=contact_email,
                        contact_phone=contact_phone,
                        student_notes=student_notes,
                        status='PENDING',
                    )
                    log_audit_event(
                        event_type='PHYSICAL_VERIFICATION_REQUESTED',
                        actor_enrollment=user.enrollment_number,
                        description='Physical In-Person Verification request submitted',
                    )
            messages.success(
                request,
                "If the enrollment number is valid, your in-person verification request has been queued. Please visit the Campus Operations Desk with your official Institute ID card."
            )
            return redirect('login')

    return render(request, 'registration/forgot_password.html', {
        'step': 'request_otp',
        'active_tab': active_tab,
    })


@rate_limit(key_prefix='apply', limit=5, period_seconds=60)
def apply_account(request):
    """Student account application or Early Launch self-declaration."""
    early_launch = getattr(settings, 'EARLY_LAUNCH_MODE', False)
    from accounts.demo import is_guest_demo
    in_guest_demo = is_guest_demo(request) or (request.user.is_authenticated and getattr(request.user, 'enrollment_number', '') == 'GUEST_DEMO')
    is_real_authenticated = request.user.is_authenticated and not in_guest_demo

    mode = (
        request.POST.get('registration_mode')
        or request.GET.get('mode')
        or ('early' if early_launch else 'permanent')
    )
    if not early_launch:
        mode = 'permanent'

    if is_real_authenticated:
        # Verified students cannot re-apply
        if request.user.verified:
            messages.info(request, "Your account is already permanently verified.")
            return redirect('dashboard')
        # Untrusted / early access users can apply for permanent verification
        mode = 'permanent'

        # Check if an application is already pending review
        pending_app = AccountApplication.objects.filter(
            enrollment_number=request.user.enrollment_number,
            approved=False,
        ).first()
        if pending_app and request.method == 'GET':
            return render(request, 'accounts/application_status.html', {
                'application': pending_app,
                'early_launch_mode': early_launch,
            })

    designated_admins = User.objects.filter(is_superuser=True, is_active=True).order_by('enrollment_number')

    if request.method == 'POST':
        if early_launch and mode == 'early':
            form = EarlyLaunchDeclarationForm(request.POST)
            if form.is_valid():
                with transaction.atomic():
                    user = form.save()
                    log_audit_event(
                        event_type='EARLY_LAUNCH_SELF_DECLARATION',
                        actor_enrollment=user.enrollment_number,
                        description=f'Self-declaration under Early Launch Mode for {user.enrollment_number}',
                        target_id=user.enrollment_number,
                    )
                if in_guest_demo and hasattr(request, 'session'):
                    request.session.pop('is_guest_demo', None)
                from django.contrib.auth import login
                login(request, user, backend='accounts.backends.EnrollmentBackend')
                messages.warning(
                    request,
                    "Notice: Your account is self-declared and stored in the temporary transit database (untrusted). Physical verification is pending, but you can coordinate rides immediately."
                )
                return redirect('dashboard')

            return render(request, 'accounts/apply.html', {
                'form': form,
                'early_launch_mode': early_launch,
                'active_tab': 'early',
                'designated_admins': designated_admins,
                'is_guest_demo': in_guest_demo,
            })

        else:
            form = AccountApplicationForm(request.POST, user=request.user if is_real_authenticated else None)
            enrollment = request.user.enrollment_number if is_real_authenticated else request.POST.get('enrollment_number', '').strip().upper()
            phone_otp = request.POST.get('phone_otp', '').strip()
            phone_num = request.POST.get('phone_number', '').strip()
            email_addr = request.POST.get('email', '').strip().lower()

            is_debug = enrollment.startswith('20XX') and (getattr(settings, 'DEBUG', False) or 'test' in sys.argv)
            phone_verified = False

            if form.is_valid():
                if is_debug:
                    phone_verified = True
                elif is_real_authenticated and not phone_otp and normalize_phone_number(phone_num) == normalize_phone_number(request.user.phone_number):
                    phone_verified = True
                else:
                    if not phone_otp:
                        form.add_error(None, "Phone verification code is required. Please verify your phone number via OTP.")
                    else:
                        phone_verified, otp_err = verify_phone_otp(phone_number=phone_num, email=email_addr, submitted_code=phone_otp, purpose='SIGNUP')
                        if not phone_verified:
                            form.add_error(None, f"Phone verification failed: {otp_err}")

                if phone_verified:
                    with transaction.atomic():
                        application = form.save(commit=False)
                        application.enrollment_number = enrollment
                        application.application_number = uuid.uuid4().hex[:8].upper()
                        if form.cleaned_data.get('password'):
                            application.password = make_password(form.cleaned_data['password'])
                        elif is_real_authenticated:
                            application.password = request.user.password
                        application.phone_verified = True
                        application.save()

                        # Immediately create active temporary user account so student can use campus facilities while pending physical review
                        name_parts = application.full_name.strip().split()
                        first_name = name_parts[0] if name_parts else ''
                        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                        user, user_created = User.objects.get_or_create(
                            enrollment_number=enrollment,
                            defaults={
                                'username': enrollment,
                                'first_name': first_name,
                                'last_name': last_name,
                                'email': application.email,
                                'phone_number': application.phone_number,
                                'gender': application.gender,
                                'password': application.password,
                                'verified': False,
                                'is_active': True,
                            }
                        )
                        if not user_created and not user.verified:
                            user.first_name = first_name
                            user.last_name = last_name
                            user.email = application.email
                            user.phone_number = application.phone_number
                            user.gender = application.gender
                            user.password = application.password
                            user.is_active = True
                            user.save()

                        # Mirror application and temporary user to permanent DB if running with isolated early launch temporary DB
                        if 'permanent' in connections:
                            try:
                                AccountApplication.objects.using('permanent').update_or_create(
                                    enrollment_number=application.enrollment_number,
                                    defaults={
                                        'full_name': application.full_name,
                                        'email': application.email,
                                        'phone_number': application.phone_number,
                                        'gender': application.gender,
                                        'password': application.password,
                                        'application_number': application.application_number,
                                        'phone_verified': True,
                                        'approved': False,
                                    }
                                )
                                User.objects.using('permanent').update_or_create(
                                    enrollment_number=enrollment,
                                    defaults={
                                        'username': enrollment,
                                        'first_name': first_name,
                                        'last_name': last_name,
                                        'email': application.email,
                                        'phone_number': application.phone_number,
                                        'gender': application.gender,
                                        'password': application.password,
                                        'verified': False,
                                        'is_active': True,
                                    }
                                )
                            except Exception as e:
                                logger.warning("Could not mirror permanent records: %s", e)

                        log_audit_event(
                            event_type='APP_SUBMITTED',
                            actor_enrollment=enrollment,
                            description=f'Application #{application.application_number} submitted; temporary account activated for {enrollment}',
                            target_id=enrollment,
                        )

                    # Send application received confirmation email
                    try:
                        send_mail(
                            subject='Campus Nexus — Student Verification Application Received',
                            message=(
                                f'Hi {first_name},\n\n'
                                f'We have received your application for permanent student ID verification ({enrollment}).\n\n'
                                f'Application Reference: #{application.application_number}\n'
                                f'Status: Under Review\n\n'
                                f'A temporary campus access account has been activated for you immediately. '
                                f'You can coordinate rides, browse campus activities, and connect with peers right away '
                                f'while your permanent verification is pending review.\n\n'
                                f'For any questions or follow-up regarding your verification, please contact asaiyed_2024BCSE065@nitsri.ac.in.\n\n'
                                f'— The Campus Nexus Team, NIT Srinagar'
                            ),
                            from_email=None,
                            recipient_list=[application.email] if application.email else [],
                            fail_silently=True,
                        )
                    except Exception:
                        pass

                    # Clean guest session if applicable and log user in immediately
                    if in_guest_demo and hasattr(request, 'session'):
                        request.session.pop('is_guest_demo', None)
                    from django.contrib.auth import login
                    login(request, user, backend='accounts.backends.EnrollmentBackend')

                    messages.success(
                        request,
                        f'Application #{application.application_number} submitted! Temporary campus access activated. You can coordinate rides immediately while awaiting admin verification.'
                    )
                    return render(request, 'accounts/application_success.html', {
                        'application': application,
                    })

            return render(request, 'accounts/apply.html', {
                'form': form,
                'early_launch_mode': early_launch,
                'active_tab': 'permanent',
                'designated_admins': designated_admins,
                'is_guest_demo': in_guest_demo,
            })

    # GET request
    if early_launch and mode == 'early':
        form = EarlyLaunchDeclarationForm()
    else:
        initial_data = {}
        if is_real_authenticated:
            initial_data = {
                'enrollment_number': request.user.enrollment_number,
                'full_name': request.user.get_full_name() or f"{request.user.first_name} {request.user.last_name}".strip(),
                'email': request.user.email,
                'phone_number': request.user.phone_number,
                'gender': request.user.gender,
            }
        form = AccountApplicationForm(user=request.user if is_real_authenticated else None, initial=initial_data)

    return render(request, 'accounts/apply.html', {
        'form': form,
        'early_launch_mode': early_launch,
        'active_tab': mode,
        'designated_admins': designated_admins,
        'is_guest_demo': in_guest_demo,
    })


def application_success(request):
    return render(request, 'accounts/application_success.html')


# ---------------------------------------------------------------------------
# Authenticated views
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    """User's home page. Shows their active ride if they have one."""
    from accounts.demo import is_guest_demo
    if is_guest_demo(request):
        demo_participations = list(
            RideParticipant.objects.using('guest_demo')
            .filter(
                user=request.user,
                ride__demo_session_key=request.session.session_key,
                ride__status__in=['OPEN', 'FULL'],
            )
            .select_related('ride', 'ride__creator')
            .order_by('ride__departure_time')
        )
        active_participation = demo_participations[0] if demo_participations else None
        other_participations = demo_participations[1:] if len(demo_participations) > 1 else []
        return render(request, 'accounts/dashboard.html', {
            'active_participation': active_participation,
            'other_participations': other_participations,
            'services': get_all_services(),
            'pending_application': None,
            'incoming_friend_requests': [],
            'incoming_ride_join_requests': [],
            'is_guest_demo': True,
            'recent_conversations': [],
            'total_unread_messages': 0,
        })

    active_participations = list(
        RideParticipant.objects
        .filter(user=request.user, ride__status__in=['OPEN', 'FULL'])
        .select_related('ride', 'ride__creator')
        .order_by('ride__departure_time')
    )
    active_participation = active_participations[0] if active_participations else None
    other_participations = active_participations[1:] if len(active_participations) > 1 else []
    pending_application = None
    if not request.user.verified:
        pending_application = AccountApplication.objects.filter(
            enrollment_number=request.user.enrollment_number,
            approved=False,
        ).first()

    incoming_friend_requests = (
        FriendRequest.objects
        .filter(receiver=request.user)
        .select_related('sender')
        .order_by('-created_at')
    )

    from coordinate.models import RideJoinRequest
    incoming_ride_join_requests = list(
        RideJoinRequest.objects
        .filter(ride__creator=request.user, ride__status__in=['OPEN', 'FULL'], status='PENDING')
        .select_related('ride', 'user')
        .order_by('-created_at')
    )

    recent_conversations, total_unread_messages = get_user_recent_conversations(request.user, limit=4, include_empty=False)

    return render(request, 'accounts/dashboard.html', {
        'active_participation': active_participation,
        'other_participations': other_participations,
        'services': get_all_services(),
        'pending_application': pending_application,
        'incoming_friend_requests': incoming_friend_requests,
        'incoming_ride_join_requests': incoming_ride_join_requests,
        'recent_conversations': recent_conversations,
        'total_unread_messages': total_unread_messages,
    })


@login_required
@verified_student_required
def profile_view(request, enrollment_number):
    """View any verified user's profile with privacy safeguards, password change, and 10-minute grace window."""
    profile_user = get_object_or_404(
        User,
        enrollment_number=enrollment_number.strip().upper(),
        is_deleted=False,
        is_active=True,
    )

    is_self = profile_user == request.user
    is_moderator = request.user.is_staff or request.user.is_superuser

    # 10-minute session grace window for security changes
    grace_str = request.session.get('security_otp_verified_until')
    grace_active = False
    if grace_str:
        try:
            grace_dt = timezone.datetime.fromisoformat(grace_str)
            if timezone.now() < grace_dt:
                grace_active = True
        except Exception:
            pass

    if request.method == 'POST' and is_self:
        new_phone = request.POST.get('phone_number', '').strip()
        new_email = request.POST.get('email', '').strip().lower()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()
        new_2fa = (request.POST.get('two_factor_enabled') == 'on')

        phone_changed = bool(new_phone and new_phone != profile_user.phone_number)
        email_changed = bool(new_email and new_email != profile_user.email)
        password_changed = bool(new_password)
        two_factor_changed = (new_2fa != profile_user.two_factor_enabled)
        security_changed = phone_changed or email_changed or password_changed or two_factor_changed

        if security_changed:
            if not profile_user.is_debug_account and not grace_active:
                sec_otp = request.POST.get('security_otp_code', '').strip()
                otp_valid, otp_msg = verify_phone_otp(profile_user, submitted_code=sec_otp, purpose='SECURITY_CHANGE')
                if not otp_valid:
                    messages.error(request, f"Security verification required: {otp_msg}")
                    return redirect('profile', enrollment_number=profile_user.enrollment_number)
                request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=10)).isoformat()
                grace_active = True

            if password_changed:
                current_password = request.POST.get('current_password', '').strip()
                if not current_password or not profile_user.check_password(current_password):
                    messages.error(request, "Current password is required and must be correct to change your password.")
                    return redirect('profile', enrollment_number=profile_user.enrollment_number)

        if email_changed:
            if User.objects.filter(email=new_email, is_deleted=False).exclude(pk=profile_user.pk).exists():
                messages.error(request, "This email address is already registered to another active account.")
                return redirect('profile', enrollment_number=profile_user.enrollment_number)

        if password_changed:
            if new_password != confirm_password:
                messages.error(request, "New passwords do not match. Please re-enter.")
                return redirect('profile', enrollment_number=profile_user.enrollment_number)

            try:
                validate_password(new_password, profile_user)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
                return redirect('profile', enrollment_number=profile_user.enrollment_number)

        # Profile photo upload with strict validation
        if 'profile_photo' in request.FILES:
            photo = request.FILES['profile_photo']
            if photo.size > 5 * 1024 * 1024:
                messages.error(request, "Profile photo file size exceeds 5MB limit.")
                return redirect('profile', enrollment_number=profile_user.enrollment_number)

            ext = os.path.splitext(photo.name)[1].lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
                messages.error(request, "Only JPG, PNG, and WebP image formats are allowed.")
                return redirect('profile', enrollment_number=profile_user.enrollment_number)

            try:
                img = Image.open(photo)
                img.verify()
                photo.seek(0)
                img = Image.open(photo)
                img_format = (img.format or '').lower()
                if img_format not in ('jpeg', 'png', 'webp'):
                    messages.error(request, "Invalid or unsupported image format.")
                    return redirect('profile', enrollment_number=profile_user.enrollment_number)
            except Exception:
                messages.error(request, "Uploaded file is not a valid image.")
                return redirect('profile', enrollment_number=profile_user.enrollment_number)

            from io import BytesIO
            from django.core.files.uploadedfile import InMemoryUploadedFile
            buffer = BytesIO()
            img = img.convert('RGB')
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            img.save(buffer, format='JPEG', quality=85, optimize=True)
            buffer.seek(0)
            clean_filename = f"{uuid.uuid4().hex}.jpg"
            clean_photo = InMemoryUploadedFile(
                file=buffer,
                field_name='profile_photo',
                name=clean_filename,
                content_type='image/jpeg',
                size=buffer.getbuffer().nbytes,
                charset=None,
            )
            setattr(profile_user, 'profile_photo', clean_photo)
        # Phone number update
        if new_phone:
            norm_phone = normalize_phone_number(new_phone)
            if len(norm_phone) > 15:
                messages.error(request, "Phone number exceeds maximum length of 15 digits.")
                return redirect('profile', enrollment_number=profile_user.enrollment_number)
            profile_user.phone_number = norm_phone
        # Email update
        if new_email:
            profile_user.email = new_email
        # Password update
        if password_changed:
            profile_user.set_password(new_password)
        # Short Bio (up to 500 characters)
        if 'bio' in request.POST:
            profile_user.bio = request.POST.get('bio', '').strip()[:500]
        # Privacy & Security toggles
        profile_user.show_friends_publicly = (request.POST.get('show_friends_publicly') == 'on')
        profile_user.hide_online_status = (request.POST.get('hide_online_status') == 'on')
        profile_user.hide_from_search = (request.POST.get('hide_from_search') == 'on')
        profile_user.hide_profile_photo = (request.POST.get('hide_profile_photo') == 'on')
        profile_user.show_contact_on_profile = (request.POST.get('show_contact_on_profile') == 'on')
        profile_user.dm_read_receipts = (request.POST.get('dm_read_receipts') == 'on')
        profile_user.two_factor_enabled = new_2fa
        profile_user.save()

        if password_changed:
            current_key = request.session.session_key
            for s in Session.objects.filter(expire_date__gt=timezone.now()):
                try:
                    if s.session_key != current_key:
                        data = s.get_decoded()
                        if str(data.get('_auth_user_id')) == str(profile_user.pk):
                            s.delete()
                except Exception:
                    pass
            update_session_auth_hash(request, profile_user)
            enforce_single_session_login(request, profile_user)

        if security_changed:
            request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=10)).isoformat()
            log_audit_event(
                event_type='SECURITY_SETTINGS_UPDATED',
                actor_enrollment=profile_user.enrollment_number,
                description='User updated security credentials/settings',
            )

        messages.success(request, 'Profile and security settings updated successfully.')
        return redirect('profile', enrollment_number=profile_user.enrollment_number)

    is_friend = False
    friend_request_sent = False
    incoming_request = None

    if not is_self:
        is_friend = Friendship.objects.filter(
            Q(user1=request.user, user2=profile_user) |
            Q(user1=profile_user, user2=request.user)
        ).exists()

        if not is_friend:
            friend_request_sent = FriendRequest.objects.filter(
                sender=request.user, receiver=profile_user
            ).exists()
            incoming_request = FriendRequest.objects.filter(
                sender=profile_user, receiver=request.user
            ).first()

    # Friends list visibility (public toggle check)
    can_see_friends = is_self or profile_user.show_friends_publicly or is_moderator
    friends = []
    if can_see_friends:
        friendships = Friendship.objects.filter(
            Q(user1=profile_user) | Q(user2=profile_user)
        ).select_related('user1', 'user2')
        friends = [f.user2 if f.user1 == profile_user else f.user1 for f in friendships]

    # Privacy checks via centralized policy
    can_see_phone = can_view_profile_field(request.user, profile_user, 'phone_number')
    can_see_email = can_view_profile_field(request.user, profile_user, 'email')
    can_see_photo = can_view_profile_field(request.user, profile_user, 'profile_photo')

    # Recent reviews: complaint text is only visible to authorized moderators
    recent_reviews = (
        profile_user.received_reviews
        .select_related('reviewer', 'ride')
        .order_by('-created_at')[:5]
    )

    registered_passkeys = list(PasskeyCredential.objects.filter(user=profile_user)) if is_self else []
    return render(request, 'accounts/profile.html', {
        'profile_user': profile_user,
        'registered_passkeys': registered_passkeys,
        'is_self': is_self,
        'is_friend': is_friend,
        'can_see_phone': can_see_phone,
        'can_see_email': can_see_email,
        'can_see_photo': can_see_photo,
        'can_see_friends': can_see_friends,
        'friends': friends,
        'is_moderator': is_moderator,
        'friend_request_sent': friend_request_sent,
        'incoming_request': incoming_request,
        'recent_reviews': recent_reviews,
        'grace_active': grace_active,
    })


@login_required
def user_search(request):
    """Search for students by enrollment number or name with privacy filtering."""
    from accounts.demo import is_guest_demo
    if is_guest_demo(request) or not can_view_student_directory(request.user):
        messages.error(request, "Access to the student directory is restricted in Guest Demo Mode to protect student privacy.")
        return redirect('dashboard')
    if not can_view_student_directory(request.user):
        messages.error(request, "Access to the student directory requires a verified student account.")
        return redirect('dashboard')
    query = request.GET.get('q', '').strip()

    # Base queryset: active students (in early launch mode, includes unverified self-declarations)
    if getattr(settings, 'EARLY_LAUNCH_MODE', False):
        students_qs = User.objects.filter(is_deleted=False, is_active=True).exclude(pk=request.user.pk)
    else:
        students_qs = User.objects.filter(verified=True, is_deleted=False, is_active=True).exclude(pk=request.user.pk)
    if not (request.user.is_staff or request.user.is_superuser):
        students_qs = students_qs.filter(hide_from_search=False)

    if query:
        students_qs = students_qs.filter(
            Q(enrollment_number__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(branch__icontains=query)
        )

    students_qs = students_qs.order_by('enrollment_number')[:60]

    # Map friendships and friend requests
    user_friends = set(
        Friendship.objects.filter(Q(user1=request.user) | Q(user2=request.user))
        .values_list('user1_id', 'user2_id')
    )
    friend_ids = {u2 if u1 == request.user.pk else u1 for u1, u2 in user_friends}

    sent_request_ids = set(
        FriendRequest.objects.filter(sender=request.user).values_list('receiver_id', flat=True)
    )
    incoming_requests_map = dict(
        FriendRequest.objects.filter(receiver=request.user).values_list('sender_id', 'id')
    )

    student_list = []
    for s in students_qs:
        student_list.append({
            'student': s,
            'is_friend': s.pk in friend_ids,
            'request_sent': s.pk in sent_request_ids,
            'incoming_request_id': incoming_requests_map.get(s.pk),
        })

    # Group students by enrollment year first, then branch of study
    # 1. First group: current user's year & branch
    # 2. Other branches in current user's year (lexicographically)
    # 3. Other years lexicographically, each with branches lexicographically
    my_year = request.user.joining_year
    if not my_year:
        year_str = (request.user.enrollment_number or '')[:4]
        my_year = int(year_str) if year_str.isdigit() else 2024
    my_branch = (request.user.branch or ((request.user.enrollment_number or '')[5:8] if len(request.user.enrollment_number or '') >= 8 else '')).upper().strip()

    from collections import defaultdict
    year_branch_map = defaultdict(lambda: defaultdict(list))

    for item in student_list:
        s = item['student']
        s_year = s.joining_year
        if not s_year:
            s_year_str = (s.enrollment_number or '')[:4]
            s_year = int(s_year_str) if s_year_str.isdigit() else 0
        s_branch = (s.branch or ((s.enrollment_number or '')[5:8] if len(s.enrollment_number or '') >= 8 else 'OTHER')).upper().strip()
        year_branch_map[s_year][s_branch].append(item)

    all_years = list(year_branch_map.keys())
    other_years = sorted([y for y in all_years if y != my_year], key=lambda y: str(y))
    ordered_years = ([my_year] if my_year in year_branch_map else []) + other_years

    year_groups = []
    flattened_ordered_list = []

    for y in ordered_years:
        branches_dict = year_branch_map[y]
        all_branches = list(branches_dict.keys())

        if y == my_year:
            other_branches = sorted([b for b in all_branches if b != my_branch])
            ordered_branches = ([my_branch] if my_branch in branches_dict else []) + other_branches
        else:
            ordered_branches = sorted(all_branches)

        branch_list = []
        for b in ordered_branches:
            stus = branches_dict[b]
            is_my_group = (y == my_year and b == my_branch)
            branch_info = {
                'branch': b,
                'is_my_branch': (b == my_branch),
                'is_my_group': is_my_group,
                'students': stus,
                'count': len(stus),
            }
            branch_list.append(branch_info)
            flattened_ordered_list.extend(stus)

        year_groups.append({
            'year': y,
            'is_my_year': (y == my_year),
            'branches': branch_list,
            'total_students': sum(len(b['students']) for b in branch_list),
        })

    return render(request, 'accounts/user_search.html', {
        'query': query,
        'student_list': flattened_ordered_list,
        'year_groups': year_groups,
        'total_count': len(flattened_ordered_list),
        'my_year': my_year,
        'my_branch': my_branch,
    })


@login_required
@verified_student_required
@require_POST
def unfriend_user(request, enrollment_number):
    """Remove a connection between friends."""
    target_user = get_object_or_404(User, enrollment_number=enrollment_number.strip().upper())
    with transaction.atomic():
        deleted, _ = Friendship.objects.filter(
            Q(user1=request.user, user2=target_user) |
            Q(user1=target_user, user2=request.user)
        ).delete()
        if deleted:
            def _broadcast_unfriend():
                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    channel_layer = get_channel_layer()
                    if channel_layer:
                        u1_id, u2_id = sorted([request.user.pk, target_user.pk])
                        async_to_sync(channel_layer.group_send)(
                            f'direct_chat_{u1_id}_{u2_id}',
                            {
                                'type': 'chat.unfriended',
                                'message': 'Friendship connection ended.',
                            }
                        )
                except Exception:
                    pass
            transaction.on_commit(_broadcast_unfriend)
            messages.info(request, f'Removed {target_user.enrollment_number} from your friends.')
    return redirect('profile', enrollment_number=target_user.enrollment_number)


@login_required
@verified_student_required
def direct_chat_view(request, enrollment_number):
    """1-on-1 chat room between students."""
    friend = get_object_or_404(User, enrollment_number=enrollment_number.strip().upper())
    if friend == request.user:
        messages.warning(request, "You cannot chat with yourself.")
        return redirect('dashboard')

    is_friend = Friendship.objects.filter(
        Q(user1=request.user, user2=friend) | Q(user1=friend, user2=request.user)
    ).exists()

    read_only = not friend.is_active or getattr(friend, 'is_deleted', False)

    if read_only and request.method == 'POST':
        messages.error(request, "This user account is inactive. Conversation is read-only.")
        return redirect('direct_chat', enrollment_number=friend.enrollment_number)

    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        if content:
            msg = DirectMessage.objects.create(
                sender=request.user,
                recipient=friend,
                content=content[:2000],
            )
            def _broadcast_dm():
                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    channel_layer = get_channel_layer()
                    if channel_layer:
                        u1_id, u2_id = sorted([request.user.pk, friend.pk])
                        async_to_sync(channel_layer.group_send)(
                            f'direct_chat_{u1_id}_{u2_id}',
                            {
                                'type': 'chat.direct_message',
                                'message_id': msg.id,
                                'sender': request.user.enrollment_number,
                                'content': msg.content,
                                'timestamp': msg.created_at.strftime('%b %d, %H:%M'),
                            }
                        )
                        async_to_sync(channel_layer.group_send)(
                            f'user_notifications_{friend.pk}',
                            {
                                'type': 'chat.notification',
                                'message_id': msg.id,
                                'sender': request.user.enrollment_number,
                                'sender_name': request.user.get_full_name() or request.user.enrollment_number,
                                'sender_avatar': request.user.profile_photo.url if request.user.profile_photo else '',
                                'content': msg.content[:120],
                                'url': f'/messages/{request.user.enrollment_number}/',
                                'timestamp': msg.created_at.strftime('%H:%M'),
                            }
                        )
                except Exception:
                    pass
            transaction.on_commit(_broadcast_dm)
            return redirect('direct_chat', enrollment_number=friend.enrollment_number)

    # Mark unread messages from this friend as read
    DirectMessage.objects.filter(
        sender=friend,
        recipient=request.user,
        is_read=False,
    ).update(is_read=True)

    # Load recent conversation history (last 100 messages)
    chat_messages = list(
        DirectMessage.objects
        .filter(
            (Q(sender=request.user, recipient=friend) | Q(sender=friend, recipient=request.user))
        )
        .order_by('created_at')[:100]
    )

    return render(request, 'accounts/friend_chat.html', {
        'friend': friend,
        'chat_messages': chat_messages,
        'is_friend': is_friend,
        'read_only': read_only,
    })


@login_required
@verified_student_required
@require_POST
def report_direct_chat_view(request, enrollment_number):
    """File a formal conduct report against a direct messaging partner.

    Captures an optional evidentiary transcript snapshot for the selected
    timeframe: 15_MINUTES, 1_HOUR, 1_DAY, or LIFETIME.
    """
    reported_user = get_object_or_404(User, enrollment_number=enrollment_number.strip().upper())
    if reported_user == request.user:
        messages.error(request, "You cannot file a report against yourself.")
        return redirect('dashboard')

    reason_category = request.POST.get('reason_category', '').strip()
    valid_categories = [c[0] for c in DirectMessageReport.CATEGORY_CHOICES]
    if reason_category not in valid_categories:
        messages.error(request, "Please select a valid report reason category.")
        return redirect('direct_chat', enrollment_number=reported_user.enrollment_number)

    statement = request.POST.get('statement', '').strip()
    share_transcript = request.POST.get('share_transcript') in ('1', 'true', 'on', True)
    transcript_timeframe = request.POST.get('transcript_timeframe', '').strip()
    valid_timeframes = [t[0] for t in DirectMessageReport.TIMEFRAME_CHOICES]

    transcript_snapshot = ''
    transcript_count = 0

    if share_transcript:
        if transcript_timeframe not in valid_timeframes:
            transcript_timeframe = '1_HOUR'

        now = timezone.now()
        qs = DirectMessage.objects.filter(
            (Q(sender=request.user, recipient=reported_user) | Q(sender=reported_user, recipient=request.user))
        )

        if transcript_timeframe == '15_MINUTES':
            cutoff = now - timedelta(minutes=15)
            qs = qs.filter(created_at__gte=cutoff)
        elif transcript_timeframe == '1_HOUR':
            cutoff = now - timedelta(hours=1)
            qs = qs.filter(created_at__gte=cutoff)
        elif transcript_timeframe == '1_DAY':
            cutoff = now - timedelta(days=1)
            qs = qs.filter(created_at__gte=cutoff)
        # LIFETIME: all messages

        messages_list = list(qs.order_by('created_at'))
        transcript_count = len(messages_list)

        lines = [
            "=== DIRECT MESSAGE TRANSCRIPT EVIDENCE ===",
            "Reference: PENDING",
            f"Reporter: {request.user.enrollment_number} ({request.user.get_full_name() or 'N/A'})",
            f"Reported User: {reported_user.enrollment_number} ({reported_user.get_full_name() or 'N/A'})",
            f"Timeframe: {dict(DirectMessageReport.TIMEFRAME_CHOICES).get(transcript_timeframe, transcript_timeframe)}",
            f"Captured At: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Message Count: {transcript_count}",
            "==========================================",
            ""
        ]
        for m in messages_list:
            sender_name = m.sender.get_full_name() or m.sender.enrollment_number
            ts = m.created_at.strftime('%Y-%m-%d %H:%M:%S')
            lines.append(f"[{ts}] {sender_name} ({m.sender.enrollment_number}): {m.content}")

        transcript_snapshot = "\n".join(lines)
    else:
        transcript_timeframe = ''

    ref_id = f"DMR-{uuid.uuid4().hex[:10].upper()}"
    if transcript_snapshot:
        transcript_snapshot = transcript_snapshot.replace("Reference: PENDING", f"Reference: {ref_id}")

    report = DirectMessageReport.objects.create(
        reference_id=ref_id,
        reporter=request.user,
        reported_user=reported_user,
        reason_category=reason_category,
        statement=statement,
        share_transcript=share_transcript,
        transcript_timeframe=transcript_timeframe,
        transcript_snapshot=transcript_snapshot,
        transcript_message_count=transcript_count,
    )

    from django.db.models import F
    User.objects.filter(id=reported_user.id).update(
        behavior_reports=F('behavior_reports') + 1
    )

    log_audit_event(
        event_type='DM_REPORT_FILED',
        actor_enrollment=request.user.enrollment_number,
        target_id=reported_user.enrollment_number,
        description=f"Filed DM report {ref_id} ({reason_category}, transcript_shared={share_transcript}, messages={transcript_count})",
    )

    messages.success(
        request,
        f"Your report ({ref_id}) has been submitted securely to campus moderators. Thank you for helping keep our community safe."
    )
    return redirect('direct_chat', enrollment_number=reported_user.enrollment_number)


def get_user_recent_conversations(user, limit=None, include_empty=False):
    """Retrieve friend conversations with latest snippet and unread badges."""
    friendships = Friendship.objects.filter(
        Q(user1=user) | Q(user2=user)
    ).select_related('user1', 'user2')
    friends_list = [f.user2 if f.user1 == user else f.user1 for f in friendships]
    friend_ids = {f.pk for f in friends_list}

    # Include former friends with chat history
    dm_sent_to = set(DirectMessage.objects.filter(sender=user).values_list('recipient_id', flat=True))
    dm_rcvd_from = set(DirectMessage.objects.filter(recipient=user).values_list('sender_id', flat=True))
    all_chat_partner_ids = friend_ids | dm_sent_to | dm_rcvd_from
    if not all_chat_partner_ids:
        return [], 0

    all_chat_users = User.objects.filter(pk__in=all_chat_partner_ids).distinct()

    # Batch-fetch unread counts across all senders (1 query)
    unread_counts = dict(
        DirectMessage.objects
        .filter(recipient=user, is_read=False)
        .values('sender_id')
        .annotate(cnt=Count('id'))
        .values_list('sender_id', 'cnt')
    )
    total_unread = sum(unread_counts.values())

    # Batch-fetch latest message IDs across all chat partners (1 query + 1 fetch)
    latest_msg_subquery = (
        DirectMessage.objects
        .filter(
            Q(sender=user, recipient=OuterRef('pk')) |
            Q(sender=OuterRef('pk'), recipient=user)
        )
        .order_by('-created_at')
        .values('id')[:1]
    )

    user_latest_ids = {
        user_pk: msg_id
        for user_pk, msg_id in all_chat_users.annotate(
            latest_msg_id=Subquery(latest_msg_subquery)
        ).values_list('pk', 'latest_msg_id')
        if msg_id
    }

    latest_messages = {
        m.pk: m
        for m in DirectMessage.objects.filter(pk__in=user_latest_ids.values()).select_related('sender', 'recipient')
    }

    conversations = []
    for friend in all_chat_users:
        latest = latest_messages.get(user_latest_ids.get(friend.pk))
        if include_empty or latest or unread_counts.get(friend.pk, 0) > 0:
            conversations.append({
                'friend': friend,
                'latest_message': latest,
                'unread_count': unread_counts.get(friend.pk, 0),
            })

    conversations.sort(
        key=lambda c: c['latest_message'].created_at if c['latest_message'] else timezone.now() - timezone.timedelta(days=3650),
        reverse=True
    )

    if limit:
        conversations = conversations[:limit]

    return conversations, total_unread


@login_required
@verified_student_required
def messages_inbox(request):
    """Overview of all friend conversations with latest snippet and unread badges."""
    conversations, _ = get_user_recent_conversations(request.user, limit=None, include_empty=True)
    return render(request, 'accounts/messages_inbox.html', {
        'conversations': conversations,
    })


# ---------------------------------------------------------------------------
# Admin: Applications
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_superuser)
def application_list(request):
    """List all pending (unapproved) applications with temporary user upgrade indicators."""
    applications = list(AccountApplication.objects.filter(approved=False))
    unverified_enrollments = set(
        User.objects.filter(
            enrollment_number__in=[a.enrollment_number for a in applications],
            verified=False,
        ).values_list('enrollment_number', flat=True)
    )
    for app in applications:
        app.is_temp_upgrade = app.enrollment_number in unverified_enrollments

    return render(request, 'accounts/application_list.html', {
        'applications': applications,
    })


@require_POST
@login_required
@user_passes_test(_is_superuser)
def approve_application(request, application_id):
    """
    POST only. Creates a verified User from the application, sends a
    notification email, and removes the application from the queue.
    """
    from accounts.utils import check_step_up_or_recent_auth
    if not check_step_up_or_recent_auth(request):
        if getattr(request, 'htmx', False):
            import json
            response = HttpResponse(status=403)
            response['HX-Trigger'] = json.dumps({'stepUpRequired': True})
            return response
        messages.error(request, "Privileged session expired. Please re-authenticate before approving applications.")
        return redirect('application_list')

    with transaction.atomic():
        application = get_object_or_404(AccountApplication, id=application_id, approved=False)
        if application.enrollment_number == 'GUEST_DEMO':
            messages.error(request, "Cannot approve applications for GUEST_DEMO. The application is invalid and has been purged.")
            application.delete()
            if 'permanent' in connections:
                try:
                    AccountApplication.objects.using('permanent').filter(enrollment_number='GUEST_DEMO').delete()
                except Exception:
                    pass
            return redirect('application_list')

        parts = application.full_name.strip().split() if application.full_name else []
        first_name = parts[0][:150] if parts else ''
        last_name = ' '.join(parts[1:])[:150] if len(parts) > 1 else ''

        existing_user = User.objects.filter(enrollment_number=application.enrollment_number).first()
        if existing_user:
            if existing_user.verified:
                application.delete()
                if 'permanent' in connections:
                    try:
                        AccountApplication.objects.using('permanent').filter(enrollment_number=application.enrollment_number).delete()
                    except Exception:
                        pass
                messages.warning(request, 'A verified user with that enrollment number already exists.')
                if request.htmx:
                    return HttpResponse('')
                return redirect('application_list')
            else:
                # Upgrade untrusted early-launch user to verified!
                existing_user.verified = True
                if application.password:
                    existing_user.password = application.password
                if application.phone_number:
                    existing_user.phone_number = application.phone_number
                if application.email:
                    existing_user.email = application.email
                if first_name:
                    existing_user.first_name = first_name
                if last_name:
                    existing_user.last_name = last_name
                if application.gender:
                    existing_user.gender = application.gender
                existing_user.save()
                user = existing_user
        else:
            user = User.objects.create(
                enrollment_number=application.enrollment_number,
                username=application.enrollment_number,
                first_name=first_name,
                last_name=last_name,
                email=application.email,
                phone_number=application.phone_number,
                gender=application.gender,
                password=application.password,   # already hashed
                verified=True,
            )

        application.delete()

        # Mirror approved user to permanent DB if running with isolated early launch temporary DB
        if 'permanent' in connections:
            try:
                User.objects.using('permanent').update_or_create(
                    enrollment_number=user.enrollment_number,
                    defaults={
                        'username': user.enrollment_number,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'email': user.email,
                        'phone_number': user.phone_number,
                        'gender': user.gender,
                        'password': user.password,
                        'bio': getattr(user, 'bio', ''),
                        'profile_photo': user.profile_photo.name if getattr(user, 'profile_photo', None) else '',
                        'verified': True,
                        'is_active': True,
                    }
                )
                AccountApplication.objects.using('permanent').filter(enrollment_number=user.enrollment_number).delete()
            except Exception as e:
                logger.warning("Could not sync approved user to permanent DB: %s", e)

        # Also sync to temp_early_access if running with permanent as default DB
        if 'temp_early_access' in connections:
            try:
                temp_u = User.objects.using('temp_early_access').filter(enrollment_number=user.enrollment_number).first()
                if temp_u:
                    temp_u.verified = True
                    temp_u.save(update_fields=['verified'])
                AccountApplication.objects.using('temp_early_access').filter(enrollment_number=user.enrollment_number).delete()
            except Exception as e:
                logger.warning("Could not sync approved user to temp DB: %s", e)

        log_audit_event(
            event_type='APP_APPROVED',
            actor_enrollment=request.user.enrollment_number,
            description=f'Application approved and elevated to verified status for {user.enrollment_number}',
            target_id=user.enrollment_number,
        )

    # Send approval email (fire-and-forget; console backend in dev, SMTP in prod)
    try:
        send_mail(
            subject='Campus Nexus — Your Account Has Been Approved & Verified',
            message=(
                f'Hi {user.first_name},\n\n'
                f'Your application for permanent student ID verification ({user.enrollment_number}) '
                f'has been approved by campus administrators.\n\n'
                f'Your profile has been elevated to permanent, verified status with full student privileges.\n\n'
                f'You can continue using Campus Nexus at https://campusnexus.nitsri.ac.in/ (or your local environment)\n\n'
                f'— The Campus Nexus Team, NIT Srinagar'
            ),
            from_email=None,   # uses DEFAULT_FROM_EMAIL
            recipient_list=[user.email] if user.email else [],
            fail_silently=True,
        )
    except Exception:
        pass

    if request.htmx:
        return HttpResponse('')

    messages.success(request, f'Account approved and elevated to verified status for {user.enrollment_number}.')
    return redirect('application_list')


@require_POST
@login_required
@user_passes_test(_is_superuser)
def reject_application(request, application_id):
    """
    POST only. Safely rejects and discards a pending student application,
    freeing the enrollment number for correction/resubmission.
    """
    from accounts.utils import check_step_up_or_recent_auth
    if not check_step_up_or_recent_auth(request):
        if getattr(request, 'htmx', False):
            import json
            response = HttpResponse(status=403)
            response['HX-Trigger'] = json.dumps({'stepUpRequired': True})
            return response
        messages.error(request, "Privileged session expired. Please re-authenticate before rejecting applications.")
        return redirect('application_list')

    REJECTION_CHOICES = {
        'DETAILS_MISMATCH': 'Student profile details (name, branch, or year) do not match institute academic roster',
        'UNREADABLE_DOCUMENTS': 'Submitted student credentials or ID documentation could not be verified',
        'CONTACT_UNVERIFIABLE': 'Unable to verify provided phone number or student email address',
        'DUPLICATE_APPLICATION': 'Duplicate application detected or previous verified record already active',
    }
    choice = request.POST.get('rejection_reason_choice', '').strip()
    custom_reason = request.POST.get('rejection_reason_custom', '').strip()

    if choice == 'CUSTOM' or not choice:
        final_reason = custom_reason or 'Verification criteria not met upon review.'
    else:
        predefined = REJECTION_CHOICES.get(choice, choice)
        final_reason = f"{predefined}. Additional notes: {custom_reason}" if custom_reason else predefined

    with transaction.atomic():
        application = get_object_or_404(AccountApplication, id=application_id, approved=False)
        enrollment = application.enrollment_number
        applicant_name = application.full_name
        applicant_email = application.email
        application.delete()
        if 'permanent' in connections:
            try:
                AccountApplication.objects.using('permanent').filter(enrollment_number=enrollment).delete()
            except Exception:
                pass
        log_audit_event(
            event_type='APP_REJECTED',
            actor_enrollment=request.user.enrollment_number,
            description=f'Application rejected for {enrollment}. Reason: {final_reason}',
            target_id=enrollment,
        )

    # Dispatch rejection notification email with specified reason
    try:
        send_mail(
            subject='Campus Nexus — Verification Application Status Update',
            message=(
                f'Hi {applicant_name},\n\n'
                f'Your application for permanent student ID verification ({enrollment}) '
                f'could not be approved at this time.\n\n'
                f'Reason Specified by Administrator:\n'
                f'------------------------------------------------------------\n'
                f'{final_reason}\n'
                f'------------------------------------------------------------\n\n'
                f'Next Steps:\n'
                f'• You may submit a corrected application at https://dev.campus-nexus.online/apply/ or your campus portal.\n'
                f'• For inquiries or to clarify your credentials, please contact asaiyed_2024BCSE065@nitsri.ac.in.\n\n'
                f'— The Campus Nexus Team, NIT Srinagar'
            ),
            from_email=None,
            recipient_list=[applicant_email] if applicant_email else [],
            fail_silently=True,
        )
    except Exception:
        pass

    if request.htmx:
        return HttpResponse('')

    messages.info(request, f'Application for {enrollment} was rejected. Reason: {final_reason}. Rejection email dispatched.')
    return redirect('application_list')


# ---------------------------------------------------------------------------
# Admin: Analytics
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_superuser)
def admin_analytics(request):
    """Superuser analytics dashboard."""
    from datetime import timedelta
    from django.utils import timezone

    now = timezone.now()
    week_ago = now - timedelta(days=7)

    ride_stats = RideRequest.objects.aggregate(
        total=Count('id'),
        open=Count('id', filter=Q(status='OPEN')),
        full=Count('id', filter=Q(status='FULL')),
        completed=Count('id', filter=Q(status='COMPLETED')),
        recent=Count('id', filter=Q(created_at__gte=week_ago)),
    )

    user_stats = User.objects.aggregate(
        total=Count('id'),
        verified=Count('id', filter=Q(verified=True)),
    )

    pending_count = AccountApplication.objects.filter(approved=False).count()

    popular_routes = (
        RideRequest.objects
        .values('route')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    route_label = dict(RideRequest.ROUTE_CHOICES)
    for r in popular_routes:
        r['label'] = route_label.get(r['route'], r['route'])

    top_users = (
        User.objects
        .filter(verified=True)
        .order_by('-rides_completed')[:10]
    )

    flagged_users = (
        User.objects
        .filter(behavior_reports__gt=0)
        .order_by('-behavior_reports')[:10]
    )

    return render(request, 'accounts/admin_analytics.html', {
        'ride_stats': ride_stats,
        'user_stats': user_stats,
        'pending_count': pending_count,
        'popular_routes': popular_routes,
        'top_users': top_users,
        'flagged_users': flagged_users,
    })


# ---------------------------------------------------------------------------
# Social: Friends
# ---------------------------------------------------------------------------

@require_POST
@login_required
@verified_student_required
def send_friend_request(request, enrollment_number):
    target = get_object_or_404(
        User,
        enrollment_number=enrollment_number.strip().upper(),
    )

    from coordinate.policies import is_verified_student
    if not target.is_active or getattr(target, 'is_deleted', False) or not is_verified_student(target):
        messages.error(request, "This student account is not available for friend connections.")
        return redirect('user_search')

    if target == request.user:
        messages.error(request, "You can't send a friend request to yourself.")
        return redirect('profile', enrollment_number=enrollment_number)

    already_friends = Friendship.objects.filter(
        Q(user1=request.user, user2=target) |
        Q(user1=target, user2=request.user)
    ).exists()

    if already_friends:
        messages.info(request, 'You are already friends.')
        return redirect('profile', enrollment_number=enrollment_number)

    reciprocal = FriendRequest.objects.filter(sender=target, receiver=request.user).first()
    if reciprocal:
        try:
            with transaction.atomic():
                Friendship.objects.create(user1=request.user, user2=target)
                FriendRequest.objects.filter(
                    Q(sender=request.user, receiver=target) | Q(sender=target, receiver=request.user)
                ).delete()
            messages.success(request, f'You are now friends with {enrollment_number}.')
            return redirect('profile', enrollment_number=enrollment_number)
        except IntegrityError:
            FriendRequest.objects.filter(
                Q(sender=request.user, receiver=target) | Q(sender=target, receiver=request.user)
            ).delete()
            messages.info(request, 'Friendship already exists.')
            return redirect('profile', enrollment_number=enrollment_number)

    fr, created = FriendRequest.objects.get_or_create(sender=request.user, receiver=target)
    if created:
        sender_name = request.user.get_full_name() or request.user.enrollment_number
        send_user_notification(
            user_id=target.id,
            title='New Connection Request',
            content=f'{sender_name} sent you a campus friend request.',
            url='/dashboard/',
            category='friend_request',
            sender_user=request.user,
            badge='Connection Request',
            badge_class='bg-info-subtle text-info border border-info-subtle',
            icon='bi-person-plus-fill',
        )
    messages.success(request, f'Friend request sent to {enrollment_number}.')
    return redirect('profile', enrollment_number=enrollment_number)


def _redirect_after_friend_action(request, target_user=None):
    """Safely redirect to referer if within domain, or current user's profile, or dashboard."""
    referer = request.META.get('HTTP_REFERER')
    if referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        host = request.get_host().split(':')[0].lower()
        referer_host = (parsed.netloc or '').split(':')[0].lower()
        if not referer_host or referer_host == host or referer_host.endswith('.' + host):
            return redirect(referer)
    if request.user and getattr(request.user, 'enrollment_number', None):
        return redirect('profile', enrollment_number=request.user.enrollment_number)
    if target_user and getattr(target_user, 'enrollment_number', None):
        return redirect('profile', enrollment_number=target_user.enrollment_number)
    return redirect('dashboard')


@require_POST
@login_required
@verified_student_required
def accept_friend_request(request, request_id):
    """Accept an incoming friend request by request ID, sender user ID, or enrollment number.
    Gracefully handles already-accepted, duplicate, or stale requests without throwing 404.
    """
    int_id = None
    try:
        int_id = int(request_id)
    except (ValueError, TypeError):
        pass

    friend_request = None
    if int_id is not None:
        # 1. Direct FriendRequest primary key where request.user is receiver
        friend_request = FriendRequest.objects.filter(id=int_id, receiver=request.user).select_related('sender', 'receiver').first()
        # 2. Match by sender User ID where request.user is receiver
        if not friend_request:
            friend_request = FriendRequest.objects.filter(sender_id=int_id, receiver=request.user).select_related('sender', 'receiver').first()
        # 3. Match reciprocal/mutual where request.user is sender
        if not friend_request:
            friend_request = FriendRequest.objects.filter(id=int_id, sender=request.user).select_related('sender', 'receiver').first()
        if not friend_request:
            friend_request = FriendRequest.objects.filter(receiver_id=int_id, sender=request.user).select_related('sender', 'receiver').first()

    # 4. Match by sender or receiver enrollment number (e.g. string '2024BCSE037')
    if not friend_request:
        friend_request = FriendRequest.objects.filter(
            sender__enrollment_number__iexact=str(request_id),
            receiver=request.user,
        ).select_related('sender', 'receiver').first()

    if not friend_request:
        friend_request = FriendRequest.objects.filter(
            receiver__enrollment_number__iexact=str(request_id),
            sender=request.user,
        ).select_related('sender', 'receiver').first()

    if not friend_request:
        # Check if users are already friends or if target user exists
        target_user = None
        if int_id is not None:
            target_user = User.objects.filter(id=int_id).first()
        if not target_user:
            target_user = User.objects.filter(enrollment_number__iexact=str(request_id)).first()

        if target_user:
            already_friends = Friendship.objects.filter(
                Q(user1=request.user, user2=target_user) |
                Q(user1=target_user, user2=request.user)
            ).exists()
            if already_friends:
                name_or_enrollment = target_user.get_full_name() or target_user.enrollment_number
                messages.info(request, f'You are already friends with {name_or_enrollment}.')
                return _redirect_after_friend_action(request, target_user)

        messages.info(request, 'This friend request has already been processed or is no longer pending.')
        return _redirect_after_friend_action(request, target_user)

    # Determine other student
    other_user = friend_request.sender if friend_request.receiver == request.user else friend_request.receiver

    from coordinate.policies import is_verified_student
    if not other_user.is_active or getattr(other_user, 'is_deleted', False) or not is_verified_student(other_user):
        friend_request.delete()
        messages.error(request, 'This student account is no longer active.')
        return _redirect_after_friend_action(request, other_user)

    other_name = other_user.get_full_name() or other_user.enrollment_number
    try:
        with transaction.atomic():
            Friendship.objects.create(
                user1=request.user,
                user2=other_user,
            )
            FriendRequest.objects.filter(
                Q(sender=request.user, receiver=other_user) |
                Q(sender=other_user, receiver=request.user)
            ).delete()
        messages.success(request, f'You are now friends with {other_name}.')
        sender_name = request.user.get_full_name() or request.user.enrollment_number
        send_user_notification(
            user_id=other_user.id,
            title='Friend Request Accepted',
            content=f'{sender_name} accepted your friend request! You can now send direct messages.',
            url=f'/messages/{request.user.enrollment_number}/',
            category='friend_accepted',
            sender_user=request.user,
            badge='Friend Request Accepted',
            badge_class='bg-success-subtle text-success border border-success-subtle',
            icon='bi-person-check-fill',
        )
    except IntegrityError:
        FriendRequest.objects.filter(
            Q(sender=request.user, receiver=other_user) |
            Q(sender=other_user, receiver=request.user)
        ).delete()
        messages.info(request, f'You are already friends with {other_name}.')

    return _redirect_after_friend_action(request, other_user)


@require_POST
@login_required
@verified_student_required
def decline_friend_request(request, request_id):
    """Decline an incoming friend request by request ID, sender user ID, or enrollment number.
    Gracefully handles already-processed requests without throwing 404.
    """
    int_id = None
    try:
        int_id = int(request_id)
    except (ValueError, TypeError):
        pass

    friend_request = None
    if int_id is not None:
        friend_request = FriendRequest.objects.filter(id=int_id, receiver=request.user).select_related('sender').first()
        if not friend_request:
            friend_request = FriendRequest.objects.filter(sender_id=int_id, receiver=request.user).select_related('sender').first()

    if not friend_request:
        friend_request = FriendRequest.objects.filter(
            sender__enrollment_number__iexact=str(request_id),
            receiver=request.user,
        ).select_related('sender').first()

    if not friend_request:
        messages.info(request, 'This friend request has already been processed or is no longer pending.')
        return _redirect_after_friend_action(request)

    sender = friend_request.sender
    sender_name = sender.get_full_name() or sender.enrollment_number
    friend_request.delete()
    messages.info(request, f'Friend request from {sender_name} was declined.')
    return _redirect_after_friend_action(request, sender)

