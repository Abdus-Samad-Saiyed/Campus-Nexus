"""
Client request identity tracking and console log enrichment for Daphne and Django.

Maintains a thread-safe registry of in-flight client requests keyed by client connection address
(host:port). Enriches HTTP and WebSocket request logs with:
  [timestamp] HTTP %(method)s %(path)s %(status)s [%(time_taken).2f, %(client)s, %(enrollment)s, %(name)s]
where unauthenticated requests display N.A. for enrollment and name.

Terminal Console Output Colour Coding:
- Verified students  -> Green   (\033[92m)
- Guests             -> Blue    (\033[94m)
- Fastrack students  -> Yellow  (\033[93m)
- Errors             -> Red     (\033[91m)
- Unregistered users -> White   (\033[97m)
- Asset requests     -> Cyan    (\033[96m)
"""

import collections
import datetime
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("django.channels.server")

# ------------------------------------------------------------------------------
# ANSI Console Color Engine
# ------------------------------------------------------------------------------

class ConsoleColors:
    RESET = "\033[0m"
    RED = "\033[91m"       # Errors (status >= 400, rejected websocket)
    GREEN = "\033[92m"     # Verified students
    YELLOW = "\033[93m"    # Fastrack students (unverified)
    BLUE = "\033[94m"      # Guests (guest demo)
    CYAN = "\033[96m"      # Asset requests rather than user requests
    MAGENTA = "\033[95m"   # Unregistered / anonymous public requests
    WHITE = "\033[97m"     # Plain white fallback
    BOLD = "\033[1m"


def _enable_windows_vt():
    """Enable ANSI virtual terminal processing on Windows consoles if supported."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            hStdOut = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(hStdOut, ctypes.byref(mode)):
                kernel32.SetConsoleMode(hStdOut, mode.value | 0x0004)
        except Exception:
            pass


_enable_windows_vt()


def colorize(text: str, color_code: str) -> str:
    """Wrap text in ANSI color escape codes unless NO_COLOR=1 is set."""
    if os.environ.get("NO_COLOR") == "1":
        return text
    return f"{color_code}{text}{ConsoleColors.RESET}"


def is_asset_path(path: str | None) -> bool:
    """Return True if path represents a static/media asset or service manifest rather than a user request."""
    if not path:
        return False
    p = path.split("?")[0].lower()
    # Dynamic user-facing application routes are user requests, not assets
    if p.startswith(("/api/", "/admin/", "/coordinate/", "/rides/", "/messages/", "/dashboard/", "/profile/", "/login/", "/apply/")):
        return False
    # Static asset and media directories
    if p.startswith(("/static/", "/media/", "/.well-known/", "/cdn-cgi/")):
        return True
    # Common root-level static asset files
    asset_files = {
        "/favicon.ico", "/robots.txt", "/llms.txt", "/manifest.json",
        "/sw.js", "/service-worker.js", "/apple-touch-icon.png",
        "/clientaccesspolicy.xml", "/crossdomain.xml",
    }
    if p in asset_files:
        return True
    # Standard static asset file extensions
    asset_exts = (
        ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg",
        ".ico", ".webp", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".map", ".mp4", ".webm"
    )
    return p.endswith(asset_exts)


def get_request_color(
    status: int | None = 200,
    path: str | None = None,
    user_type: str | None = None,
    action: str | None = None,
) -> str:
    """Determine the ANSI color code matching the user category and request type:
    - Errors -> Red (\033[91m)
    - Asset requests -> Cyan (\033[96m)
    - Guests -> Blue (\033[94m)
    - Fastrack students -> Yellow (\033[93m)
    - Verified students -> Green (\033[92m)
    - Unregistered users -> White (\033[97m)
    """
    # 1. Errors: status >= 400 or rejected/error websocket action
    if status is not None and status >= 400:
        return ConsoleColors.RED
    if action in ("rejected", "error"):
        return ConsoleColors.RED

    # 2. Asset requests (status < 400)
    if is_asset_path(path):
        return ConsoleColors.CYAN

    # 3. Guests
    if user_type == "guest":
        return ConsoleColors.BLUE

    # 4. Fastrack students (authenticated, verified=False)
    if user_type == "fastrack":
        return ConsoleColors.YELLOW

    # 5. Verified students (authenticated, verified=True or admin)
    if user_type == "verified":
        return ConsoleColors.GREEN

    # 6. Unregistered users (unauthenticated / anonymous)
    return ConsoleColors.MAGENTA


# ------------------------------------------------------------------------------
# Request Identity Registry & ASGI State Engine
# ------------------------------------------------------------------------------

# Maximum lifetime of a queued client record in seconds (prevents memory accumulation on dropped sockets)
CLIENT_META_TTL_SECONDS = 120
SESSION_CACHE_TTL_SECONDS = 86400  # 24 hours matching SESSION_COOKIE_AGE
CONNECTION_CACHE_TTL_SECONDS = 600  # 10 minutes for active TCP connection

# Thread-safe mapping: client_key (e.g. '127.0.0.1:65521') -> collections.deque of request metadata dicts
_CLIENT_REGISTRY = collections.defaultdict(collections.deque)
# Session key -> dict(enrollment, name, real_client, user_type, ts)
_SESSION_IDENTITY_CACHE = {}
# Client connection key (ip:port) -> dict(enrollment, name, real_client, user_type, ts)
_CONNECTION_IDENTITY_CACHE = {}
# WebSocket client connection key -> dict(enrollment, name, real_client, user_type, ts, path)
_WEBSOCKET_REGISTRY = {}

_REGISTRY_LOCK = threading.Lock()

# Tracks whether Daphne's custom Command.log_action is active in the current process
_DAPHNE_ACTION_LOGGER_ACTIVE = False
_ASGI_LOGGER_ACTIVE = True


def set_asgi_logger_active(active: bool = True):
    global _ASGI_LOGGER_ACTIVE
    _ASGI_LOGGER_ACTIVE = active


def is_asgi_logger_active() -> bool:
    return _ASGI_LOGGER_ACTIVE


def emit_console_log(colored_msg: str, status_code: int = 200):
    """Write colored log line directly to stdout without duplication."""
    try:
        sys.stdout.write(colored_msg + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            print(colored_msg, flush=True)
        except Exception:
            pass


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
    user_type: str = "unregistered",
):
    """Register authenticated identity in memory caches for instant ASGI lookups."""
    if not enrollment or enrollment == "N.A.":
        return

    now = time.time()
    payload = {
        "enrollment": enrollment,
        "name": name or enrollment,
        "real_client": real_client or client_key or "127.0.0.1",
        "user_type": user_type or "unregistered",
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
                    "user_type": user_type or "unregistered",
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
    user_type: str = "unregistered",
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
        "user_type": user_type or "unregistered",
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
    user_type: str = "unregistered",
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
                "user_type": user_type or "unregistered",
                "ts": now,
            }
            if client_key in _WEBSOCKET_REGISTRY:
                _WEBSOCKET_REGISTRY[client_key].update({
                    "enrollment": enrollment,
                    "name": name or enrollment,
                    "user_type": user_type or "unregistered",
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
            if user_type and user_type != "unregistered":
                target_item["user_type"] = user_type
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
                "user_type": user_type or "unregistered",
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
                "user_type": "unregistered",
                "ts": now,
            }

        # If identity is unpopulated (e.g. static asset or early pop), check connection cache
        if item.get("enrollment") in ("N.A.", "", None):
            conn_cached = _CONNECTION_IDENTITY_CACHE.get(client_key)
            if conn_cached:
                item["enrollment"] = conn_cached["enrollment"]
                item["name"] = conn_cached["name"]
                item["user_type"] = conn_cached.get("user_type", "unregistered")

        if item.get("enrollment") == "GUEST_DEMO":
            item["user_type"] = "guest"

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
            client_key = f"{client[0]}:{client[1]}" if client else "DEFAULT"
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
            user_type = "unregistered"

            # 1. Direct user on scope
            if user and getattr(user, "is_authenticated", False):
                enrollment = getattr(user, "enrollment_number", "") or getattr(user, "username", "N.A.") or "N.A."
                full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
                name = full_name.strip() or getattr(user, "display_name", "") or getattr(user, "username", "") or enrollment
                if not name:
                    name = "N.A."

                if getattr(user, "is_guest_demo", False) or getattr(user, "username", "") == "GUEST_DEMO":
                    user_type = "guest"
                elif getattr(user, "verified", False) or getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
                    user_type = "verified"
                else:
                    user_type = "fastrack"

                register_client_identity(
                    session_key=session_key,
                    client_key=client_key,
                    enrollment=enrollment,
                    name=name,
                    real_client=real_client,
                    user_type=user_type,
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
                    user_type = cached.get("user_type", "unregistered")
                    register_client_identity(
                        client_key=client_key,
                        enrollment=enrollment,
                        name=name,
                        real_client=real_client,
                        user_type=user_type,
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
                user_type=user_type,
            )

            auth_token = session_key or token_key

            if scope_type == "http":
                resp_status = [200]

                async def wrapped_send(message):
                    m_type = message.get("type")
                    if m_type == "http.response.start":
                        resp_status[0] = message.get("status", 200)
                    await send(message)
                    if m_type == "http.response.body" and not message.get("more_body", False):
                        ClientLoggingASGIWrapper._log_http_event(
                            scope=scope,
                            client_key=client_key,
                            real_client=real_client,
                            received_at=received_at,
                            status_code=resp_status[0],
                            auth_token=auth_token,
                        )

                return await self.inner(scope, receive, wrapped_send)

            elif scope_type == "websocket":
                path = scope.get("path") or ""

                async def wrapped_send(message):
                    m_type = message.get("type")
                    if m_type == "websocket.accept":
                        ClientLoggingASGIWrapper._log_ws_event(
                            action_name="CONNECT",
                            path=path,
                            client_key=client_key,
                            real_client=real_client,
                            status_type="connected",
                            auth_token=auth_token,
                            scope=scope,
                        )
                    elif m_type == "websocket.close":
                        ClientLoggingASGIWrapper._log_ws_event(
                            action_name="REJECTED",
                            path=path,
                            client_key=client_key,
                            real_client=real_client,
                            status_type="rejected",
                            auth_token=auth_token,
                            scope=scope,
                        )
                    await send(message)

                async def wrapped_receive():
                    msg = await receive()
                    if msg.get("type") == "websocket.disconnect":
                        ClientLoggingASGIWrapper._log_ws_event(
                            action_name="DISCONNECT",
                            path=path,
                            client_key=client_key,
                            real_client=real_client,
                            status_type="disconnected",
                            auth_token=auth_token,
                            scope=scope,
                        )
                    return msg

                return await self.inner(scope, wrapped_receive, wrapped_send)

        return await self.inner(scope, receive, send)

    @staticmethod
    def _log_http_event(scope, client_key, real_client, received_at, status_code, auth_token=None):
        if is_action_logger_active():
            return
        path = scope.get("path") or "/"
        method = scope.get("method") or "GET"
        now = time.time()
        duration = now - (received_at or now)
        import zoneinfo
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        server_time = datetime.datetime.fromtimestamp(received_at or now, tz=ist_tz).strftime("%d/%b/%Y %H:%M:%S")

        # 1. Prefer direct client identity stamped on scope by middleware
        scope_ident = scope.get("client_identity") if scope else None
        if scope_ident:
            enrollment = scope_ident.get("enrollment", "N.A.") or "N.A."
            name = scope_ident.get("name", "N.A.") or "N.A."
            user_type = scope_ident.get("user_type", "unregistered")
            display_client = scope_ident.get("real_client") or real_client or "127.0.0.1"
            # Drain matching entry from client queue if present
            pop_client_request(client_key, path=path)
        else:
            meta = pop_client_request(client_key, path=path)
            enrollment = meta.get("enrollment", "N.A.") or "N.A."
            name = meta.get("name", "N.A.") or "N.A."
            user_type = meta.get("user_type", "unregistered")
            display_client = real_client or meta.get("real_client") or client_key or "127.0.0.1"

        if enrollment == "N.A." and auth_token:
            with _REGISTRY_LOCK:
                cached = _SESSION_IDENTITY_CACHE.get(auth_token)
                if cached:
                    enrollment = cached["enrollment"]
                    name = cached["name"]
                    user_type = cached.get("user_type", "unregistered")

        if enrollment == "N.A." and client_key:
            with _REGISTRY_LOCK:
                cached = _CONNECTION_IDENTITY_CACHE.get(client_key)
                if cached:
                    enrollment = cached["enrollment"]
                    name = cached["name"]
                    user_type = cached.get("user_type", "unregistered")

        display_client = real_client or meta.get("real_client") or client_key or "127.0.0.1"
        msg = f"[{server_time}] HTTP {method} {path} {status_code} [{duration:.2f}, {display_client}, {enrollment}, {name}]"
        color = get_request_color(status=status_code, path=path, user_type=user_type)
        colored_msg = colorize(msg, color)
        emit_console_log(colored_msg, status_code)

    @staticmethod
    def _log_ws_event(action_name, path, client_key, real_client, status_type, auth_token=None, scope=None):
        if is_action_logger_active():
            return
        now = time.time()
        import zoneinfo
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        server_time = datetime.datetime.fromtimestamp(now, tz=ist_tz).strftime("%d/%b/%Y %H:%M:%S")

        scope_ident = scope.get("client_identity") if scope else None
        if scope_ident:
            enrollment = scope_ident.get("enrollment", "N.A.") or "N.A."
            name = scope_ident.get("name", "N.A.") or "N.A."
            user_type = scope_ident.get("user_type", "unregistered")
            display_client = scope_ident.get("real_client") or real_client or "127.0.0.1"
            pop_client_request(client_key, path=path, is_websocket=True, action=status_type)
        else:
            meta = pop_client_request(client_key, path=path, is_websocket=True, action=status_type)
            enrollment = meta.get("enrollment", "N.A.") or "N.A."
            name = meta.get("name", "N.A.") or "N.A."
            user_type = meta.get("user_type", "unregistered")
            display_client = real_client or meta.get("real_client") or client_key or "127.0.0.1"

        if enrollment == "N.A." and auth_token:
            with _REGISTRY_LOCK:
                cached = _SESSION_IDENTITY_CACHE.get(auth_token)
                if cached:
                    enrollment = cached["enrollment"]
                    name = cached["name"]
                    user_type = cached.get("user_type", "unregistered")

        if enrollment == "N.A." and client_key:
            with _REGISTRY_LOCK:
                cached = _CONNECTION_IDENTITY_CACHE.get(client_key)
                if cached:
                    enrollment = cached["enrollment"]
                    name = cached["name"]
                    user_type = cached.get("user_type", "unregistered")

        display_client = real_client or meta.get("real_client") or client_key or "127.0.0.1"
        msg = f"[{server_time}] WebSocket {action_name} {path} [{display_client}, {enrollment}, {name}]"
        status = 400 if status_type == "rejected" else 200
        color = get_request_color(status=status, path=path, user_type=user_type, action=status_type)
        colored_msg = colorize(msg, color)
        emit_console_log(colored_msg, status)
