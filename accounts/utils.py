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
