"""Campus Nexus API Authentication and Response Helpers.

Provides token and session authentication for versioned REST API endpoints.
Mobile clients use Bearer tokens (Django session keys) in the Authorization header.
Web clients can use either Bearer tokens or standard session cookies.
"""

import json
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

User = get_user_model()


def json_success(data: Any = None, status: int = 200, **kwargs) -> JsonResponse:
    """Return a standardized JSON success response."""
    payload: Dict[str, Any] = {
        "status": "success",
    }
    if data is not None:
        payload["data"] = data
    for k, v in kwargs.items():
        payload[k] = v
    return JsonResponse(payload, status=status)


def json_error(
    message: str,
    status: int = 400,
    code: Optional[str] = None,
    details: Optional[Any] = None,
) -> JsonResponse:
    """Return a standardized JSON error response."""
    payload: Dict[str, Any] = {
        "status": "error",
        "error": message,
        "code": code or f"ERR_{status}",
    }
    if details is not None:
        payload["details"] = details
    return JsonResponse(payload, status=status)


def get_api_user(request: HttpRequest, allow_cookie: bool = True) -> Optional[Any]:
    """Resolve user from Authorization header or standard session."""
    from accounts.two_factor import hash_otp

    # 1. Check Authorization header: 'Bearer <token>' or 'Token <token>'
    auth_header = request.headers.get("Authorization", "") or request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            token = parts[1].strip()
            try:
                session = Session.objects.filter(session_key=token).first()
                if session and session.expire_date > timezone.now():
                    session_data = session.get_decoded()
                    user_id = session_data.get("_auth_user_id")
                    if user_id:
                        user = User.objects.filter(pk=user_id, is_active=True).first()
                        if user and not getattr(user, "is_deleted", False):
                            token_hash = hash_otp(token)
                            if hasattr(user, 'is_session_valid'):
                                active_keys = user.get_active_session_keys()
                                if not active_keys or user.is_session_valid(token):
                                    return user
                            elif not user.current_session_key:
                                return user
                            else:
                                active_csv = [k.strip() for k in user.current_session_key.split(',') if k.strip()]
                                if token in active_csv or token_hash in active_csv:
                                    return user
            except Exception:
                pass
        return None

    # 2. Check standard cookie session authentication only if allowed
    if allow_cookie and getattr(request, "user", None) and request.user.is_authenticated and request.user.is_active and not getattr(request.user, "is_deleted", False):
        actual_key = request.session.session_key
        if actual_key:
            actual_hash = hash_otp(actual_key)
            if hasattr(request.user, 'is_session_valid'):
                active_keys = request.user.get_active_session_keys()
                if active_keys and not request.user.is_session_valid(actual_key):
                    return None
            elif request.user.current_session_key:
                active_csv = [k.strip() for k in request.user.current_session_key.split(',') if k.strip()]
                if actual_key not in active_csv and actual_hash not in active_csv:
                    return None
        return request.user

    return None


def api_auth_required(view_func: Optional[Callable] = None, *, allow_cookie: Optional[bool] = None) -> Callable:
    """Decorator requiring valid user authentication for API endpoints.
    For mutating requests (POST, PUT, DELETE, PATCH), allow_cookie defaults to False to prevent CSRF.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapped_view(request: HttpRequest, *args, **kwargs):
            can_use_cookie = allow_cookie if allow_cookie is not None else (request.method in ("GET", "HEAD", "OPTIONS"))
            user = get_api_user(request, allow_cookie=can_use_cookie)
            if not user:
                return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")
            request.api_user = user
            return fn(request, *args, **kwargs)
        return wrapped_view

    if view_func is not None:
        return decorator(view_func)
    return decorator


def parse_json_body(request: HttpRequest) -> Tuple[Optional[Dict[str, Any]], Optional[JsonResponse]]:
    """Helper to parse JSON request bodies safely."""
    if not request.body:
        return {}, None
    try:
        data = json.loads(request.body.decode("utf-8"))
        if not isinstance(data, dict):
            return None, json_error("Request body must be a JSON object.", status=400, code="INVALID_JSON")
        return data, None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, json_error("Malformed JSON payload.", status=400, code="MALFORMED_JSON")
