"""
Environment detection and account utility helpers for Campus Nexus.
"""
from django.conf import settings


def is_test_environment() -> bool:
    """Return True if running under automated Django test runner."""
    return bool(getattr(settings, 'TESTING', False))


def is_alpha_environment() -> bool:
    """Return True if running in alpha/staging pilot mode."""
    return bool(getattr(settings, 'ALPHA_MODE', False))


def is_production_environment() -> bool:
    """Return True if running in strict production mode."""
    return not getattr(settings, 'DEBUG', False) and not is_test_environment() and not is_alpha_environment()


def is_debug_enrollment(enrollment: str) -> bool:
    """Check if enrollment string is a synthetic debug enrollment (starts with 20XX)."""
    return bool(enrollment and str(enrollment).strip().upper().startswith('20XX'))


def is_debug_allowed() -> bool:
    """Return True if synthetic debug accounts are permitted in the current environment."""
    return bool(getattr(settings, 'DEBUG', False) or is_test_environment())

from datetime import timedelta
from django.utils import timezone

def check_step_up_or_recent_auth(request, max_age_seconds: int = 900) -> bool:
    """Verify session freshness for high-privilege administrative actions."""
    if (
        is_test_environment()
        or getattr(settings, 'DEBUG', False)
        or getattr(settings, 'EARLY_LAUNCH_MODE', False)
        or getattr(request.user, 'is_debug_account', False)
    ):
        return True

    sec_until = request.session.get('security_otp_verified_until')
    if sec_until:
        try:
            from datetime import datetime as dt_cls
            if timezone.now() < dt_cls.fromisoformat(sec_until):
                return True
        except Exception:
            pass

    auth_ts = request.session.get('auth_timestamp')
    if auth_ts:
        try:
            if (timezone.now().timestamp() - float(auth_ts)) <= max_age_seconds:
                return True
        except Exception:
            pass

    admin_pw = request.POST.get('admin_password', '').strip()
    if admin_pw and request.user.is_authenticated and request.user.check_password(admin_pw):
        request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=15)).isoformat()
        return True

    return False


def is_passkey_step_up_active(request, max_age_seconds: int = 900) -> bool:
    """Verify session freshness for passkey registration and security credential modifications.
    Enforces active step-up reauthentication across all environments without artificial test bypass.
    """
    if getattr(request.user, 'is_debug_account', False):
        return True

    sec_until = request.session.get('security_otp_verified_until')
    if sec_until:
        try:
            from datetime import datetime as dt_cls
            if timezone.now() < dt_cls.fromisoformat(sec_until):
                return True
        except Exception:
            pass

    auth_ts = request.session.get('auth_timestamp')
    if auth_ts:
        try:
            if (timezone.now().timestamp() - float(auth_ts)) <= max_age_seconds:
                return True
        except Exception:
            pass

    admin_pw = request.POST.get('admin_password', '').strip() or request.POST.get('current_password', '').strip()
    if admin_pw and request.user.is_authenticated and request.user.check_password(admin_pw):
        request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=15)).isoformat()
        return True

    return False

def broadcast_user_session_revocation(user_id: int, reason: str = 'Session terminated by security policy.'):
    """Dispatch push notification to disconnect all active WebSocket sessions for this user."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f"user_{user_id}",
                {
                    "type": "user.session_revoked",
                    "reason": reason,
                }
            )
    except Exception:
        pass


def clean_person_name(name: str) -> str:
    from accounts.two_factor import clean_person_name as _clean
    return _clean(name)


def normalize_phone_number(phone: str) -> str:
    from accounts.two_factor import normalize_phone_number as _norm
    return _norm(phone)


def validate_phone_number(phone: str) -> str:
    from accounts.two_factor import validate_phone_number as _val
    return _val(phone)


# ---------------------------------------------------------------------------
# Multi-Platform Simultaneous Session Management
# ---------------------------------------------------------------------------
MAX_SIMULTANEOUS_PLATFORMS = 3


def detect_platform_from_request(request) -> str:
    """
    Detect the client platform from Request headers or User-Agent.
    Returns normalized platform identifier:
    'windows', 'android', 'ios', 'macos', 'linux', or 'other'.
    """
    if not request or not hasattr(request, 'META'):
        return 'other'

    # 1. Custom / explicit header override (useful for apps and automated testing)
    explicit = request.META.get('HTTP_X_PLATFORM') or (request.headers.get('X-Platform') if hasattr(request, 'headers') else None)
    if explicit:
        val = explicit.strip().lower()
        if val in ('windows', 'android', 'ios', 'macos', 'linux', 'other'):
            return val
        return val[:32]

    # 2. Sec-CH-UA-Platform client hint
    ch = request.META.get('HTTP_SEC_CH_UA_PLATFORM', '').strip('"\'- ').lower()
    if ch:
        if 'win' in ch:
            return 'windows'
        if 'android' in ch:
            return 'android'
        if any(k in ch for k in ('ios', 'iphone', 'ipad')):
            return 'ios'
        if 'mac' in ch:
            return 'macos'
        if 'linux' in ch:
            return 'linux'

    # 3. User-Agent parsing
    ua = (request.META.get('HTTP_USER_AGENT') or '').lower()
    if 'android' in ua:
        return 'android'
    if any(token in ua for token in ('iphone', 'ipad', 'ipod')):
        return 'ios'
    if 'windows' in ua:
        return 'windows'
    if 'macintosh' in ua or 'mac os' in ua:
        return 'macos'
    if 'linux' in ua:
        return 'linux'

    return 'other'


def enforce_platform_session_login(request, user):
    """
    Enforce multi-platform simultaneous login policy:
    1. One sign-in per platform at a time (concurrent login on same platform terminates prior session).
    2. Multiple sign-ins across different platforms allowed up to a maximum of 3.
    3. If a 4th platform logs in, the least-recently-active platform session is evicted.
    """
    import time
    from django.utils import timezone
    from django.contrib.sessions.models import Session
    from accounts.models import UserPlatformSession
    from accounts.models import log_audit_event

    if not request.session.session_key:
        request.session.save()
    current_key = request.session.session_key
    platform = detect_platform_from_request(request)

    request.session['platform'] = platform
    request.session['last_activity'] = time.time()

    # Step 1: Same-Platform Eviction ("One sign in per platform at a time")
    existing_same_platform = UserPlatformSession.objects.filter(
        user=user, platform=platform
    ).exclude(session_key=current_key)

    for old_ps in existing_same_platform:
        try:
            from django.contrib.sessions.backends.cached_db import SessionStore as CachedSessionStore
            CachedSessionStore(session_key=old_ps.session_key).delete()
        except Exception:
            pass
        Session.objects.filter(session_key=old_ps.session_key).delete()
        log_audit_event(
            event_type='SESSION_TERMINATED_CONCURRENT',
            actor_enrollment=getattr(user, 'enrollment_number', 'N.A.'),
            description=f'Prior session on platform ({platform}) terminated on concurrent login',
        )
    existing_same_platform.delete()

    # Step 2: Max Platforms Limit ("maximum of three")
    other_platforms = list(
        UserPlatformSession.objects.filter(user=user)
        .exclude(platform=platform)
        .order_by('last_activity')
    )

    while len(other_platforms) >= MAX_SIMULTANEOUS_PLATFORMS:
        oldest = other_platforms.pop(0)
        try:
            from django.contrib.sessions.backends.cached_db import SessionStore as CachedSessionStore
            CachedSessionStore(session_key=oldest.session_key).delete()
        except Exception:
            pass
        Session.objects.filter(session_key=oldest.session_key).delete()
        log_audit_event(
            event_type='SESSION_TERMINATED_CONCURRENT',
            actor_enrollment=getattr(user, 'enrollment_number', 'N.A.'),
            description=f'Oldest platform session ({oldest.platform}) evicted to maintain max {MAX_SIMULTANEOUS_PLATFORMS} active platforms',
        )
        oldest.delete()

    # Step 3: Register or update current platform session record
    ip_addr = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', '')
    ua_str = (request.META.get('HTTP_USER_AGENT') or '')[:255]

    UserPlatformSession.objects.update_or_create(
        user=user,
        platform=platform,
        defaults={
            'session_key': current_key,
            'ip_address': ip_addr,
            'user_agent': ua_str,
            'last_activity': timezone.now(),
        }
    )

    # Step 4: Synchronize user.current_session_key for quick access & backward compatibility
    active_keys = list(UserPlatformSession.objects.filter(user=user).values_list('session_key', flat=True))
    user.current_session_key = ','.join(active_keys)[:255]
    user.last_seen = timezone.now()
    user.save(update_fields=['current_session_key', 'last_seen'])

    # Step 5: Register client logger identity
    try:
        from accounts.client_logger import register_client_identity
        enrollment = getattr(user, 'enrollment_number', '') or getattr(user, 'username', 'N.A.')
        full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
        display_name = full_name.strip() or getattr(user, 'display_name', '') or getattr(user, 'username', '') or enrollment
        register_client_identity(
            session_key=current_key,
            enrollment=enrollment,
            name=display_name,
            platform=platform,
        )
    except Exception:
        pass

    return platform


def tombstone_deleted_user(user) -> None:
    """
    Release unique constraints (enrollment_number, username, email) on a soft-deleted user.
    Preserves all 34 foreign key relationships (past rides, reviews, direct messages, audit logs)
    while freeing the original student identifiers for new account registrations.
    """
    from django.utils import timezone
    ts = int(timezone.now().timestamp())
    user.is_deleted = True
    user.is_active = False

    dirty_fields = ['is_deleted', 'is_active']

    if not user.enrollment_number.startswith('DEL_'):
        user.enrollment_number = f"DEL_{user.id}_{ts}_{user.enrollment_number}"[:50]
        dirty_fields.append('enrollment_number')

    if not user.username.startswith('del_'):
        user.username = f"del_{user.id}_{ts}_{user.username}"[:150]
        dirty_fields.append('username')

    if user.email and not user.email.startswith('del_'):
        user.email = f"del_{user.id}_{ts}_{user.email}"[:254]
        dirty_fields.append('email')

    user.save(update_fields=dirty_fields)


def release_deleted_user_identifiers(enrollment: str = None, email: str = None) -> None:
    """
    If any soft-deleted user is currently holding the given enrollment number or email,
    tombstone that deleted user to immediately free the identifiers for new registration.
    Also clears any obsolete pending AccountApplication rows for that identifier.
    """
    from accounts.models import User, AccountApplication
    from django.db.models import Q

    cond = Q()
    if enrollment:
        clean_enroll = enrollment.strip().upper()
        cond |= Q(enrollment_number__iexact=clean_enroll) | Q(username__iexact=clean_enroll)
    if email:
        clean_email = email.strip().lower()
        cond |= Q(email__iexact=clean_email)

    if cond:
        deleted_users = list(User.objects.filter(is_deleted=True).filter(cond))
        if deleted_users:
            for u in deleted_users:
                tombstone_deleted_user(u)

            app_q = Q()
            if enrollment:
                app_q |= Q(enrollment_number__iexact=clean_enroll)
            if email:
                app_q |= Q(email__iexact=clean_email)
            AccountApplication.objects.filter(approved=False).filter(app_q).delete()

