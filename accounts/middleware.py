"""Middleware for single active concurrent session enforcement and request client identity logging."""

import time
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from accounts.two_factor import hash_otp


class SingleSessionMiddleware:
    """Ensure each account can only have one active session at a time.

    If the same account logs in from another device or browser, the previous session is terminated.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if getattr(request.user, 'is_deleted', False) or not request.user.is_active:
                logout(request)
                if request.path_info.startswith('/api/'):
                    return JsonResponse({'status': 'error', 'error': 'Account inactive.', 'code': 'ACCOUNT_INACTIVE'}, status=401)
                messages.error(request, "This account has been closed.")
                return redirect('login')

            expected_key = getattr(request.user, 'current_session_key', '')
            actual_key = request.session.session_key
            if expected_key and actual_key:
                actual_hash = hash_otp(actual_key)
                if expected_key not in (actual_key, actual_hash):
                    logout(request)
                    if request.path_info.startswith('/api/'):
                        return JsonResponse({'status': 'error', 'error': 'Session invalidated.', 'code': 'SESSION_REVOKED'}, status=401)
                    messages.warning(
                        request,
                        "You have been logged out because this account was logged into from another device or browser."
                    )
                    return redirect('login')

        return self.get_response(request)


class ClientIdentityLoggingMiddleware:
    """Extract authenticated client identity (enrollment, name, real IP, receival timestamp)
    and record it for ASGI / Daphne console completion logging.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        initial_user = getattr(request, 'user', None)
        received_at = time.time()
        response = None
        try:
            response = self.get_response(request)
            return response
        finally:
            self._record_client_identity(request, initial_user, received_at, response)

    def _record_client_identity(self, request, initial_user=None, received_at=None, response=None):
        try:
            # 1. Determine client connection key matching Daphne's details['client']
            client_key = None
            scope = getattr(request, 'scope', None)
            if scope and scope.get('client'):
                client_key = f"{scope['client'][0]}:{scope['client'][1]}"
            elif request.META.get('REMOTE_ADDR') and request.META.get('REMOTE_PORT'):
                client_key = f"{request.META['REMOTE_ADDR']}:{request.META['REMOTE_PORT']}"
            else:
                client_key = request.META.get('REMOTE_ADDR')

            # 2. Extract real IP from proxy headers if present
            real_ip = (
                request.META.get('HTTP_CF_CONNECTING_IP', '').strip()
                or (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() if request.META.get('HTTP_X_FORWARDED_FOR') else None)
                or request.META.get('REMOTE_ADDR')
            )

            # 3. Resolve user identity
            user = getattr(request, 'user', None)
            if (not user or not getattr(user, 'is_authenticated', False)) and initial_user and getattr(initial_user, 'is_authenticated', False):
                user = initial_user

            # If still not authenticated, check mobile Bearer token in Authorization header
            if (not user or not getattr(user, 'is_authenticated', False)) and request.META.get('HTTP_AUTHORIZATION'):
                auth_header = request.META['HTTP_AUTHORIZATION']
                parts = auth_header.split()
                if len(parts) == 2 and parts[0].lower() in ('bearer', 'token'):
                    token = parts[1].strip()
                    user = self._get_user_from_token(token)

            from accounts.demo import is_guest_demo
            in_guest_demo = is_guest_demo(request)

            enrollment = "N.A."
            name = "N.A."
            if user and getattr(user, 'is_authenticated', False) and not in_guest_demo:
                enrollment = getattr(user, 'enrollment_number', '') or getattr(user, 'username', 'N.A.') or 'N.A.'
                full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
                name = full_name.strip() or getattr(user, 'display_name', '') or getattr(user, 'username', '') or enrollment
                if not name:
                    name = "N.A."

            from accounts.client_logger import update_or_record_client_request, resolve_client_display, is_action_logger_active
            real_client = resolve_client_display(client_key, real_ip)
            update_or_record_client_request(
                client_key,
                enrollment=enrollment,
                name=name,
                real_client=real_client,
                received_at=received_at,
            )

            # If Daphne's custom Command.log_action is NOT active, log directly as fallback
            if not is_action_logger_active() and response is not None:
                import datetime
                import logging
                server_time = datetime.datetime.fromtimestamp(received_at or time.time()).strftime("%d/%b/%Y %H:%M:%S")
                duration = time.time() - (received_at or time.time())
                status_code = getattr(response, 'status_code', 200)
                method = request.method
                path = request.get_full_path()
                msg = f"[{server_time}] HTTP {method} {path} {status_code} [{duration:.2f}, {real_client}, {enrollment}, {name}]"
                logging.getLogger("django.channels.server").info(msg)
        except Exception:
            pass

    @staticmethod
    def _get_user_from_token(token_key):
        if not token_key:
            return None
        try:
            from django.contrib.sessions.models import Session
            from django.contrib.auth import get_user_model
            from django.utils import timezone
            from accounts.two_factor import hash_otp

            session = Session.objects.filter(session_key=token_key, expire_date__gt=timezone.now()).first()
            if not session:
                return None
            data = session.get_decoded()
            user_id = data.get('_auth_user_id')
            if not user_id:
                return None
            User = get_user_model()
            user = User.objects.filter(pk=user_id, is_active=True).first()
            if not user or getattr(user, 'is_deleted', False):
                return None
            token_hash = hash_otp(token_key)
            if user.current_session_key and user.current_session_key not in (token_key, token_hash):
                return None
            return user
        except Exception:
            return None
