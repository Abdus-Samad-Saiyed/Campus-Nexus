"""
Channels Token & Session Authentication Middleware for Campus Nexus.

Enables React Native mobile applications to connect to WebSockets using Bearer
session tokens via query string: `ws://.../ws/ride/1/?token=<session_token>`
or Authorization headers, while maintaining full backward-compatibility with
browser cookie-based authentication.
"""

from urllib.parse import parse_qs, urlparse
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from channels.security.websocket import OriginValidator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.utils import timezone

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token_key: str):
    """Retrieve and validate User instance associated with the given session key."""
    if not token_key:
        return AnonymousUser()

    try:
        from accounts.two_factor import hash_otp
        session = Session.objects.filter(session_key=token_key).first()
        if not session or session.expire_date <= timezone.now():
            return AnonymousUser()

        session_data = session.get_decoded()
        user_id = session_data.get('_auth_user_id')
        if not user_id:
            return AnonymousUser()

        user = User.objects.filter(pk=user_id, is_active=True).first()
        if not user or getattr(user, 'is_deleted', False):
            return AnonymousUser()

        # Enforce valid session key against active platform sessions
        token_hash = hash_otp(token_key)
        if hasattr(user, 'is_session_valid'):
            active_keys = user.get_active_session_keys() if hasattr(user, 'get_active_session_keys') else []
            if active_keys and not user.is_session_valid(token_key):
                return AnonymousUser()
        elif user.current_session_key:
            active_keys = [k.strip() for k in user.current_session_key.split(',') if k.strip()]
            if active_keys and token_key not in active_keys and token_hash not in active_keys:
                return AnonymousUser()

        return user
    except Exception:
        return AnonymousUser()


class TokenOrSessionAuthMiddleware(BaseMiddleware):
    """Middleware resolving user from either Bearer token query/header or Session cookie."""

    async def __call__(self, scope, receive, send):
        # 1. Check if user was already resolved by prior middleware (e.g. cookie auth)
        user = scope.get('user')
        if user and user.is_authenticated:
            return await super().__call__(scope, receive, send)

        token = None

        # 2. Prioritize headers: Authorization: Bearer <token> or Sec-WebSocket-Protocol
        if 'headers' in scope:
            for name, val in scope['headers']:
                header_name = name.lower()
                if header_name == b'authorization':
                    parts = val.decode('utf-8').split()
                    if len(parts) == 2 and parts[0].lower() in ('bearer', 'token'):
                        token = parts[1].strip()
                        break
                elif header_name == b'sec-websocket-protocol':
                    protocols = [p.strip() for p in val.decode('utf-8').split(',')]
                    for i, p in enumerate(protocols):
                        if p.lower() in ('bearer', 'token') and i + 1 < len(protocols):
                            token = protocols[i + 1]
                            break
                        elif p.startswith('token.'):
                            token = p.split('.', 1)[1]
                            break

        # 3. Fallback: query string ?token=<session_token> (deprecated for security)
        if not token:
            query_string = scope.get('query_string', b'').decode('utf-8')
            if query_string:
                parsed = parse_qs(query_string)
                if 'token' in parsed and parsed['token']:
                    token = parsed['token'][0].strip()

        # 4. Resolve user from token if found
        if token:
            scope['user'] = await get_user_from_token(token)

        return await super().__call__(scope, receive, send)


class WebSocketIdentityLoggerMiddleware(BaseMiddleware):
    """Register authenticated user identity in the client logger registry immediately upon connection."""

    async def __call__(self, scope, receive, send):
        user = scope.get('user')
        if user and getattr(user, 'is_authenticated', False):
            from accounts.client_logger import register_client_identity
            client = scope.get('client')
            client_key = f"{client[0]}:{client[1]}" if client else None
            enrollment = getattr(user, 'enrollment_number', '') or getattr(user, 'username', 'N.A.') or 'N.A.'
            full_name = user.get_full_name() if hasattr(user, 'get_full_name') else ''
            name = full_name.strip() or getattr(user, 'display_name', '') or getattr(user, 'username', '') or enrollment
            if not name:
                name = 'N.A.'

            if getattr(user, 'is_guest_demo', False) or getattr(user, 'username', '') == 'GUEST_DEMO':
                user_type = 'guest'
            elif getattr(user, 'verified', False) or getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
                user_type = 'verified'
            else:
                user_type = 'fastrack'

            register_client_identity(
                client_key=client_key,
                enrollment=enrollment,
                name=name,
                user_type=user_type,
            )
            scope['client_identity'] = {
                'enrollment': enrollment,
                'name': name,
                'user_type': user_type,
                'real_client': client_key or '127.0.0.1',
            }

        return await super().__call__(scope, receive, send)


def TokenOrSessionAuthMiddlewareStack(inner):
    """Stack TokenOrSessionAuthMiddleware on top of standard AuthMiddlewareStack with WebSocket identity logging."""
    from channels.auth import AuthMiddlewareStack
    return TokenOrSessionAuthMiddleware(AuthMiddlewareStack(WebSocketIdentityLoggerMiddleware(inner)))


class MobileAndAllowedHostsOriginValidator(OriginValidator):
    """Origin validator that permits native mobile clients without an Origin header,
    while validating Origin for browser clients against configured allowed hosts.
    """

    def valid_origin(self, parsed_origin):
        if parsed_origin is None:
            # Native Android/iOS mobile WebSocket clients do not supply an Origin header
            return True
        return super().valid_origin(parsed_origin)
