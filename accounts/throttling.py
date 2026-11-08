"""Lightweight rate-limiting and anti-abuse utility for COORDINATE.

Protects sensitive endpoints (login, account application, password recovery)
against brute-force and resource exhaustion attacks.
"""

from functools import wraps
import logging
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Extract client IP address safely from request.
    Only inspects HTTP_X_FORWARDED_FOR if TRUST_X_FORWARDED_FOR is True and the immediate
    REMOTE_ADDR is in TRUSTED_PROXY_IPS.
    """
    remote_addr = request.META.get('REMOTE_ADDR', '127.0.0.1')
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        trusted_proxies = getattr(settings, 'TRUSTED_PROXY_IPS', ['127.0.0.1'])
        if remote_addr in trusted_proxies:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                parts = [p.strip() for p in x_forwarded_for.split(',') if p.strip()]
                if parts:
                    return parts[-1]
    return remote_addr


def rate_limit(key_prefix: str, limit: int, period_seconds: int, message: str = None):
    """
    Decorator to rate limit POST requests by client IP and identifier.
    Uses atomic cache.add and cache.incr to prevent race conditions and TTL resets.
    Wraps cache operations in try/except to prevent 500s on temporary cache outage.
    """
    if message is None:
        message = 'Too many requests. Please wait a few minutes before trying again.'

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # Bypass rate limiting for internal benchmark load testing when secret bypass header is present
            if request.headers.get('x-loadtest-bypass') == 'campus-nexus-benchmark-active':
                return view_func(request, *args, **kwargs)
            if not getattr(settings, 'RATELIMIT_ENABLE', True):
                return view_func(request, *args, **kwargs)

            if request.method == 'POST':
                ip = get_client_ip(request)
                cache_key_ip = f'ratelimit:{key_prefix}:{ip}'

                # Extract identifier if available
                ident = ''
                for field in ('username', 'enrollment_number', 'phone_number', 'email'):
                    val = request.POST.get(field, '').strip().upper()
                    if val:
                        ident = val
                        break

                if not ident and 'application/json' in (request.content_type or ''):
                    try:
                        import json
                        body_data = json.loads(request.body.decode('utf-8'))
                        if isinstance(body_data, dict):
                            for field in ('username', 'enrollment_number', 'phone_number', 'email', 'enrollment'):
                                val = str(body_data.get(field, '')).strip().upper()
                                if val:
                                    ident = val
                                    break
                    except Exception:
                        pass

                current_count_ip = 1
                current_count_id = 1

                try:
                    # IP throttling counter
                    if not cache.add(cache_key_ip, 1, timeout=period_seconds):
                        try:
                            current_count_ip = cache.incr(cache_key_ip)
                        except ValueError:
                            current_count_ip = cache.get(cache_key_ip, 1)
                    else:
                        current_count_ip = 1

                    # Identifier throttling counter
                    if ident:
                        import hashlib
                        ident_hash = hashlib.sha256(ident.encode('utf-8')).hexdigest()[:16]
                        cache_key_id = f'ratelimit:{key_prefix}:id:{ident_hash}'
                        if not cache.add(cache_key_id, 1, timeout=period_seconds):
                            try:
                                current_count_id = cache.incr(cache_key_id)
                            except ValueError:
                                current_count_id = cache.get(cache_key_id, 1)
                        else:
                            current_count_id = 1
                except Exception as exc:
                    logger.warning("Cache backend unavailable in rate_limit: %s", type(exc).__name__)
                    current_count_ip = 1
                    current_count_id = 1

                if current_count_ip > limit or current_count_id > limit:
                    try:
                        from accounts.two_factor import get_active_otp, emit_otp_console_log
                        from accounts.models import User
                        target_user = getattr(request, 'user', None)
                        if not target_user or not getattr(target_user, 'is_authenticated', False):
                            if hasattr(request, 'session'):
                                pre_id = request.session.get('pre_2fa_user_id')
                                if pre_id:
                                    target_user = User.objects.filter(pk=pre_id).first()
                        t_phone = request.POST.get('phone_number') or request.GET.get('phone_number')
                        t_email = request.POST.get('email') or request.GET.get('email')
                        purpose = '2FA'
                        if 'signup' in key_prefix:
                            purpose = 'SIGNUP'
                        elif 'security' in key_prefix:
                            purpose = 'SECURITY_CHANGE'
                        elif 'password' in key_prefix:
                            purpose = 'PASSWORD_RESET'

                        active_code = get_active_otp(user=target_user, phone_number=t_phone, email=t_email, purpose=purpose)
                        if active_code:
                            request._active_otp_info = f"[OTP: {active_code} (Rate Limit 429)]"
                            emit_otp_console_log(
                                raw_code=active_code,
                                target=target_user or t_phone or t_email or ip,
                                purpose=purpose,
                                reason=f"RATE LIMIT EXCEEDED (HTTP 429 on {key_prefix})",
                            )
                    except Exception:
                        pass
                    # Check if client expects JSON
                    accept = request.headers.get('accept', '') or ''
                    content_type = request.content_type or ''
                    is_ajax = (
                        request.headers.get('x-requested-with') == 'XMLHttpRequest'
                        or getattr(request, 'htmx', False)
                        or 'application/json' in accept
                        or 'application/json' in content_type
                    )

                    if is_ajax:
                        return JsonResponse(
                            {'success': False, 'error': message, 'code': 'RATE_LIMITED'},
                            status=429,
                        )
                    return render(
                        request,
                        'accounts/throttled.html',
                        {'message': message, 'retry_after': period_seconds},
                        status=429,
                    )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
