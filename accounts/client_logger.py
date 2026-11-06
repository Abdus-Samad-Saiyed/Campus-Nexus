"""
Client request identity tracking and console log enrichment for Daphne and Django.

Maintains a thread-safe registry of in-flight client requests keyed by client connection address
(host:port). Enriches HTTP and WebSocket request logs with:
  [timestamp] HTTP %(method)s %(path)s %(status)s [%(time_taken).2f, %(client)s, %(enrollment)s, %(name)s]
where unauthenticated requests display N.A. for enrollment and name.
"""

import collections
import datetime
import logging
import os
import threading
import time

logger = logging.getLogger("django.channels.server")

# Maximum lifetime of a queued client record in seconds (prevents memory accumulation on dropped sockets)
CLIENT_META_TTL_SECONDS = 120
SESSION_CACHE_TTL_SECONDS = 86400  # 24 hours matching SESSION_COOKIE_AGE
CONNECTION_CACHE_TTL_SECONDS = 600  # 10 minutes for active TCP connection

# Thread-safe mapping: client_key (e.g. '127.0.0.1:65521') -> collections.deque of request metadata dicts
_CLIENT_REGISTRY = collections.defaultdict(collections.deque)
# Session key -> dict(enrollment, name, real_client, ts)
_SESSION_IDENTITY_CACHE = {}
# Client connection key (ip:port) -> dict(enrollment, name, real_client, ts)
_CONNECTION_IDENTITY_CACHE = {}
# WebSocket client connection key -> dict(enrollment, name, real_client, ts, path)
_WEBSOCKET_REGISTRY = {}

_REGISTRY_LOCK = threading.Lock()

# Tracks whether Daphne's custom Command.log_action is active in the current process
_DAPHNE_ACTION_LOGGER_ACTIVE = False


def set_action_logger_active(active: bool = True):
    """Mark Daphne's custom Command.log_action as active to coordinate with middleware logging."""
    global _DAPHNE_ACTION_LOGGER_ACTIVE
    _DAPHNE_ACTION_LOGGER_ACTIVE = active
    if active:
        os.environ['DAPHNE_ACTION_LOGGER_ACTIVE'] = '1'
    else:
        os.environ.pop('DAPHNE_ACTION_LOGGER_ACTIVE', None)


def is_action_logger_active() -> bool:
    """Return True if Daphne's custom Command.log_action is currently driving console request logging."""
    return _DAPHNE_ACTION_LOGGER_ACTIVE or os.environ.get('DAPHNE_ACTION_LOGGER_ACTIVE') == '1'


def resolve_client_display(client_key: str | None, real_ip: str | None = None) -> str:
    """Format client connection address to reflect the client IP (and port if known).
    
    If real_ip is provided (from headers like CF-Connecting-IP or X-Forwarded-For) and is not
    loopback, it replaces the loopback IP while preserving the client connection port.
    """
    if not client_key:
        return real_ip or "127.0.0.1"

    port = None
    if ":" in client_key:
        parts = client_key.rsplit(":", 1)
        if parts[1].isdigit():
            port = parts[1]

    if real_ip and real_ip not in ("127.0.0.1", "::1", "localhost"):
        return f"{real_ip}:{port}" if port else real_ip

    return client_key


def register_client_identity(
    session_key: str | None = None,
    client_key: str | None = None,
    enrollment: str = "N.A.",
    name: str = "N.A.",
    real_client: str | None = None,
):
    """Register authenticated identity in memory caches for instant ASGI lookups."""
    if not enrollment or enrollment == "N.A.":
        return

    now = time.time()
    payload = {
        "enrollment": enrollment,
        "name": name or enrollment,
        "real_client": real_client or client_key or "127.0.0.1",
        "ts": now,
    }
    with _REGISTRY_LOCK:
        if session_key:
            _SESSION_IDENTITY_CACHE[session_key] = payload
        if client_key:
            _CONNECTION_IDENTITY_CACHE[client_key] = payload
            if client_key in _WEBSOCKET_REGISTRY:
                _WEBSOCKET_REGISTRY[client_key].update({
                    "enrollment": enrollment,
                    "name": name or enrollment,
                    "ts": now,
                })


def clear_client_identity(session_key: str | None = None, client_key: str | None = None):
    """Evict cached identities on user logout or session revocation."""
    with _REGISTRY_LOCK:
        if session_key:
            _SESSION_IDENTITY_CACHE.pop(session_key, None)
        if client_key:
            _CONNECTION_IDENTITY_CACHE.pop(client_key, None)
            _WEBSOCKET_REGISTRY.pop(client_key, None)


def _clean_expired_entries_locked(now: float):
    """Prune stale request records older than TTL limits."""
    # 1. Clean in-flight client queues
    to_delete = []
    for key, q in _CLIENT_REGISTRY.items():
        while q and (now - q[0]["ts"] > CLIENT_META_TTL_SECONDS):
            q.popleft()
        if not q:
            to_delete.append(key)
    for key in to_delete:
        _CLIENT_REGISTRY.pop(key, None)

    # 2. Clean stale connection cache
    for key in [k for k, v in _CONNECTION_IDENTITY_CACHE.items() if now - v["ts"] > CONNECTION_CACHE_TTL_SECONDS]:
        _CONNECTION_IDENTITY_CACHE.pop(key, None)

    # 3. Clean stale session cache
    for key in [k for k, v in _SESSION_IDENTITY_CACHE.items() if now - v["ts"] > SESSION_CACHE_TTL_SECONDS]:
        _SESSION_IDENTITY_CACHE.pop(key, None)

    # 4. Clean stale WebSocket registry entries
    for key in [k for k, v in _WEBSOCKET_REGISTRY.items() if now - v["ts"] > SESSION_CACHE_TTL_SECONDS]:
        _WEBSOCKET_REGISTRY.pop(key, None)


def seed_client_request(
    client_key: str | None,
    enrollment: str = "N.A.",
    name: str = "N.A.",
    real_client: str | None = None,
    received_at: float | None = None,
    path: str | None = None,
    method: str | None = None,
    scope_type: str | None = None,
):
    """Pre-seed client metadata when an ASGI connection or request scope is initialized."""
    if not client_key:
        client_key = "DEFAULT"

    now = time.time()
    rec = received_at or now
    item = {
        "enrollment": enrollment or "N.A.",
        "name": name or "N.A.",
        "real_client": real_client or client_key,
        "received_at": rec,
        "path": path,
        "method": method,
        "scope_type": scope_type or "http",
        "ts": now,
    }
    with _REGISTRY_LOCK:
        _clean_expired_entries_locked(now)
        _CLIENT_REGISTRY[client_key].append(item)
        if scope_type == "websocket":
            _WEBSOCKET_REGISTRY[client_key] = item


def update_or_record_client_request(
    client_key: str | None,
    enrollment: str = "N.A.",
    name: str = "N.A.",
    real_client: str | None = None,
    received_at: float | None = None,
    path: str | None = None,
):
    """Update the in-flight request entry with authenticated user details from Django middleware."""
    if not client_key:
        client_key = "DEFAULT"

    now = time.time()
    with _REGISTRY_LOCK:
        _clean_expired_entries_locked(now)

        # Update persistent connection cache if authenticated
        if enrollment and enrollment != "N.A.":
            _CONNECTION_IDENTITY_CACHE[client_key] = {
                "enrollment": enrollment,
                "name": name or enrollment,
                "real_client": real_client or client_key,
                "ts": now,
            }
            if client_key in _WEBSOCKET_REGISTRY:
                _WEBSOCKET_REGISTRY[client_key].update({
                    "enrollment": enrollment,
                    "name": name or enrollment,
                    "ts": now,
                })

        queue = _CLIENT_REGISTRY[client_key]
        target_item = None
        if queue:
            # If path specified, find matching in-flight item
            if path:
                for item in reversed(queue):
                    if item.get("path") == path:
                        target_item = item
                        break
            if not target_item:
                target_item = queue[-1]

            if enrollment and enrollment != "N.A.":
                target_item["enrollment"] = enrollment
            elif not target_item.get("enrollment"):
                target_item["enrollment"] = "N.A."

            if name and name != "N.A.":
                target_item["name"] = name
            elif not target_item.get("name"):
                target_item["name"] = "N.A."

            if real_client:
                target_item["real_client"] = real_client
            if received_at and not target_item.get("received_at"):
                target_item["received_at"] = received_at
            target_item["ts"] = now
        else:
            queue.append({
                "enrollment": enrollment or "N.A.",
                "name": name or "N.A.",
                "real_client": real_client or client_key,
                "received_at": received_at or now,
                "path": path,
                "ts": now,
            })


def pop_client_request(
    client_key: str | None,
    path: str | None = None,
    is_websocket: bool = False,
    action: str | None = None,
) -> dict:
    """Retrieve and pop the earliest recorded metadata for the client connection."""
    if not client_key:
        client_key = "DEFAULT"

    now = time.time()
    with _REGISTRY_LOCK:
        _clean_expired_entries_locked(now)

        # WebSocket lifecycle handling
        if is_websocket:
            ws_item = _WEBSOCKET_REGISTRY.get(client_key)
            if ws_item:
                result = dict(ws_item)
                if action in ("disconnected", "rejected"):
                    _WEBSOCKET_REGISTRY.pop(client_key, None)
                return result

        queue = _CLIENT_REGISTRY.get(client_key)
        item = None
        if queue and len(queue) > 0:
            if path:
                # Find matching entry by path
                for idx, entry in enumerate(queue):
                    if entry.get("path") == path:
                        item = entry
                        del queue[idx]
                        break
            if not item:
                item = queue.popleft()
            if not queue:
                _CLIENT_REGISTRY.pop(client_key, None)

        if not item:
            # 1. Fallback: match by host address without port (for backwards compatibility)
            host = client_key.rsplit(":", 1)[0] if ":" in client_key else client_key
            for k, q in list(_CLIENT_REGISTRY.items()):
                k_host = k.rsplit(":", 1)[0] if ":" in k else k
                if (k_host == host or k == client_key) and q:
                    item = q.popleft()
                    if not q:
                        _CLIENT_REGISTRY.pop(k, None)
                    break



        if not item:
            item = {
                "enrollment": "N.A.",
                "name": "N.A.",
                "real_client": client_key if client_key != "DEFAULT" else "127.0.0.1",
                "received_at": now,
                "ts": now,
            }

        # If identity is unpopulated (e.g. static asset or early pop), check connection cache
        if item.get("enrollment") in ("N.A.", "", None):
            conn_cached = _CONNECTION_IDENTITY_CACHE.get(client_key)
            if conn_cached:
                item["enrollment"] = conn_cached["enrollment"]
                item["name"] = conn_cached["name"]

        return item


class ClientLoggingASGIWrapper:
    """ASGI application wrapper that seeds client identity metadata before
    the request reaches static file handlers or Django middleware.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type in ("http", "websocket"):
            received_at = time.time()
            client = scope.get("client")
            if client:
                client_key = f"{client[0]}:{client[1]}"
                raw_headers = scope.get("headers", [])
                headers = {}
                session_key = None
                token_key = None

                for k, v in raw_headers:
                    k_lower = k.lower()
                    headers[k_lower] = v
                    if k_lower == b"cookie":
                        try:
                            cookie_str = v.decode("latin1", "replace")
                            for part in cookie_str.split(";"):
                                part = part.strip()
                                if part.startswith("sessionid="):
                                    session_key = part.split("=", 1)[1].strip()
                                    break
                        except Exception:
                            pass
                    elif k_lower == b"authorization":
                        try:
                            auth_str = v.decode("latin1", "replace")
                            parts = auth_str.split()
                            if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
                                token_key = parts[1].strip()
                        except Exception:
                            pass

                # Check query string for token if not in headers
                if not session_key and not token_key and scope.get("query_string"):
                    try:
                        qs = scope["query_string"].decode("latin1", "replace")
                        if "token=" in qs:
                            for param in qs.split("&"):
                                if param.startswith("token="):
                                    token_key = param.split("=", 1)[1].strip()
                                    break
                    except Exception:
                        pass

                real_ip = None
                if b"cf-connecting-ip" in headers:
                    real_ip = headers[b"cf-connecting-ip"].decode("latin1").strip()
                elif b"x-forwarded-for" in headers:
                    real_ip = headers[b"x-forwarded-for"].decode("latin1").split(",")[0].strip()

                real_client = resolve_client_display(client_key, real_ip)
                user = scope.get("user")
                enrollment = "N.A."
                name = "N.A."

                # 1. Direct user on scope
                if user and getattr(user, "is_authenticated", False):
                    enrollment = getattr(user, "enrollment_number", "") or getattr(user, "username", "N.A.") or "N.A."
                    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
                    name = full_name.strip() or getattr(user, "display_name", "") or getattr(user, "username", "") or enrollment
                    if not name:
                        name = "N.A."
                    register_client_identity(
                        session_key=session_key,
                        client_key=client_key,
                        enrollment=enrollment,
                        name=name,
                        real_client=real_client,
                    )
                else:
                    # 2. Check session cache
                    cached = None
                    if session_key or token_key:
                        with _REGISTRY_LOCK:
                            cached = _SESSION_IDENTITY_CACHE.get(session_key or token_key)
                    if not cached:
                        # 3. Check connection cache
                        with _REGISTRY_LOCK:
                            cached = _CONNECTION_IDENTITY_CACHE.get(client_key)

                    if cached:
                        enrollment = cached["enrollment"]
                        name = cached["name"]
                        register_client_identity(
                            client_key=client_key,
                            enrollment=enrollment,
                            name=name,
                            real_client=real_client,
                        )

                seed_client_request(
                    client_key,
                    enrollment=enrollment,
                    name=name,
                    real_client=real_client,
                    received_at=received_at,
                    path=scope.get("path"),
                    method=scope.get("method"),
                    scope_type=scope_type,
                )

        return await self.inner(scope, receive, send)
