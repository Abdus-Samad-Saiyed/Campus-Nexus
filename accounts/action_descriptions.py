"""
Action Description Resolver for Campus Nexus Descriptive Console Mode.

Transforms raw HTTP and WebSocket requests into a structured, human-readable 2-line
feed with 5 distinct ANSI color tiers, suppressing background noise and asset requests.

Line 1: [Timestamp] [User Name | Enrollment | Client IP | Latency ms]
Line 2: Context-rich narrative describing the student's real-time campus action.
"""

import re
import time
from typing import Optional, Tuple
from django.conf import settings

# ANSI Color Codes for 5 Distinct Action Tiers
TIER_AUTH = "\033[1;36m"        # Cyan: Authentication, Identity & Passkeys
TIER_COMMUTE = "\033[1;32m"     # Bright Green: Commute, Rides & Cab shares
TIER_SOCIAL = "\033[1;33m"      # Gold / Yellow: Social, Profiles, Friends & Chat
TIER_ADMIN = "\033[1;35m"       # Magenta: Admin, KYC Operations & Management
TIER_ALERT = "\033[1;31m"       # Red / Coral: Incidents, Security Alerts & Errors
COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[2m"

# Paths and assets to suppress completely from descriptive console feed
IGNORED_EXACT_PATHS = {
    "/favicon.ico",
    "/robots.txt",
    "/llms.txt",
    "/ard.json",
    "/manifest.json",
    "/sw.js",
    "/service-worker.js",
    "/offline/",
    "/api/v1/notifications/recent/",
    "/api/v1/notifications/unread-count/",
    "/coordinate/api/notifications/unread-count/",
}

IGNORED_PREFIXES = (
    "/static/",
    "/media/",
    "/.well-known/",
    "/cdn-cgi/",
    "/__debug__/",
)

IGNORED_EXTENSIONS = (
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".ico", ".webp", ".woff", ".woff2", ".ttf", ".eot",
    ".otf", ".map", ".mp4", ".webm",
)

# Lightweight cache for student names by enrollment
_ENROLLMENT_NAME_CACHE: dict[str, str] = {}


def get_cached_student_name(enrollment: str) -> str:
    """Retrieve full name for enrollment number with memory caching."""
    if not enrollment or enrollment == "N.A." or enrollment == "GUEST_DEMO":
        return ""
    if enrollment in _ENROLLMENT_NAME_CACHE:
        return _ENROLLMENT_NAME_CACHE[enrollment]
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.filter(enrollment_number=enrollment).only("first_name", "last_name", "username").first()
        if user:
            name = f"{user.first_name} {user.last_name}".strip() or user.username
            _ENROLLMENT_NAME_CACHE[enrollment] = name
            return name
    except Exception:
        pass
    return ""


def should_suppress_request(path: str, protocol: str = "http", ws_action: str = "") -> bool:
    """Check if the request is static/noise/heartbeat and should be omitted from the descriptive feed."""
    if not path:
        return True

    clean_path = path.split("?")[0].lower()

    if clean_path in IGNORED_EXACT_PATHS:
        return True

    for pfx in IGNORED_PREFIXES:
        if clean_path.startswith(pfx):
            return True

    for ext in IGNORED_EXTENSIONS:
        if clean_path.endswith(ext):
            return True

    # Suppress WebSocket presence lease and typing indicators
    if protocol == "websocket" and clean_path.startswith("/ws/presence/"):
        return True

    return False


def resolve_action_narrative(
    path: str,
    method: str = "GET",
    status: int = 200,
    protocol: str = "http",
    ws_action: str = "",
    enrollment: str = "N.A.",
    name: str = "N.A."
) -> Tuple[str, str]:
    """
    Map request parameters to (tier_color, narrative_text).
    Returns (color_code, description_string).
    """
    clean_path = path.split("?")[0]
    p_lower = clean_path.lower()

    # 1. Error / Failure condition check -> Alert Tier
    if status >= 400:
        if status == 404:
            return TIER_ALERT, f"Attempted to access missing resource [{clean_path}] (404 Not Found)"
        if status == 403:
            return TIER_ALERT, f"Access denied to protected route [{clean_path}] (403 Forbidden)"
        if status == 429:
            return TIER_ALERT, f"Rate-limit circuit triggered on [{clean_path}] (429 Too Many Requests)"
        if status >= 500:
            return TIER_ALERT, f"Server error encountered while requesting [{clean_path}] ({status} Error)"
        return TIER_ALERT, f"Request failed on [{clean_path}] ({status})"

    # 2. WebSocket actions
    if protocol == "websocket":
        if ws_action in ("rejected", "error"):
            return TIER_ALERT, f"WebSocket connection rejected on {clean_path}"
        
        # Ride group chat
        ride_match = re.search(r"/ws/chat/ride/(\d+)/?", p_lower)
        if ride_match:
            ride_id = ride_match.group(1)
            verb = "Connected to" if ws_action == "CONNECT" else "Left"
            return TIER_COMMUTE, f"{verb} real-time ride group discussion #[{ride_id}]"

        # Direct student chat
        direct_match = re.search(r"/ws/chat/direct/([a-zA-Z0-9_\-]+)/?", p_lower)
        if direct_match:
            target_enroll = direct_match.group(1).upper()
            target_name = get_cached_student_name(target_enroll)
            partner_label = f"[{target_name} | {target_enroll}]" if target_name else f"[{target_enroll}]"
            verb = "Connected to" if ws_action == "CONNECT" else "Closed"
            return TIER_SOCIAL, f"{verb} live direct message channel with {partner_label}"

        return TIER_AUTH, f"WebSocket {ws_action} {clean_path}"

    # 3. Authentication, Identity & Passkeys (Cyan Tier)
    if p_lower.startswith("/login/2fa/"):
        return TIER_AUTH, "Submitted two-factor authentication security code"
    if p_lower == "/login/" and method == "POST":
        return TIER_AUTH, "Submitted credentials for student sign-in"
    if p_lower == "/login/":
        return TIER_AUTH, "Viewed student login portal"
    if p_lower == "/logout/":
        return TIER_AUTH, "Signed out of session"
    if p_lower.startswith("/auth/passkey/authenticate/challenge/"):
        return TIER_AUTH, "Initiated biometric passkey authentication challenge"
    if p_lower.startswith("/auth/passkey/authenticate/verify/"):
        return TIER_AUTH, "Completed biometric passkey sign-in verification"
    if p_lower.startswith("/auth/passkey/register/challenge/"):
        return TIER_AUTH, "Requested biometric passkey registration challenge"
    if p_lower.startswith("/auth/passkey/register/verify/"):
        return TIER_AUTH, "Enrolled new biometric passkey credential"
    if p_lower.startswith("/auth/passkey/step-up/"):
        return TIER_AUTH, "Verified biometric passkey for high-security profile modification"
    if p_lower.startswith("/otp/verify-security/"):
        return TIER_AUTH, "Verified SMS OTP code for security step-up"
    if "/passkey/" in p_lower and "/delete/" in p_lower:
        return TIER_AUTH, "Removed a saved passkey credential"
    if p_lower.startswith("/password_change/"):
        return TIER_AUTH, "Updated account password"
    if p_lower.startswith("/apply/") or p_lower.startswith("/apply-account/"):
        if "submit" in p_lower or method == "POST":
            return TIER_AUTH, "Submitted application for verified permanent student account"
        if "send-otp" in p_lower:
            return TIER_AUTH, "Requested mobile verification OTP for account application"
        return TIER_AUTH, "Viewed verified permanent student account application"

    # 4. Operations, Admin & KYC Verification (Magenta Tier)
    if p_lower.startswith(("/admin-nexus/", "/operations/", "/admin/")):
        if "/export/" in p_lower or "csv" in p_lower:
            return TIER_ADMIN, "Exported platform audit dataset (CSV)"
        if "/announcements/" in p_lower:
            return TIER_ADMIN, "Administered campus-wide announcements"
        if "/applications/" in p_lower:
            return TIER_ADMIN, "Reviewed pending student KYC applications queue"
        if "/verify/" in p_lower or "/resolve/" in p_lower:
            return TIER_ADMIN, "Performed student physical identity verification"
        return TIER_ADMIN, "Accessed Campus Operations administrative console"

    # 5. Commute & Rides Module (Green Tier)
    if p_lower.startswith(("/coordinate/", "/rides/")):
        if "/create/" in p_lower:
            if method == "POST":
                return TIER_COMMUTE, "Published new cab share / ride offer"
            return TIER_COMMUTE, "Opened ride creation form"
        
        # Ride join / leave / cancel / approve actions
        join_match = re.search(r"/(?:coordinate|rides)/(\d+)/join/?", p_lower)
        if join_match:
            return TIER_COMMUTE, f"Sent request to join ride #[{join_match.group(1)}]"

        leave_match = re.search(r"/(?:coordinate|rides)/(\d+)/leave/?", p_lower)
        if leave_match:
            return TIER_COMMUTE, f"Left ride #[{leave_match.group(1)}]"

        cancel_match = re.search(r"/(?:coordinate|rides)/(\d+)/cancel/?", p_lower)
        if cancel_match:
            return TIER_ALERT, f"Cancelled ride listing #[{cancel_match.group(1)}]"

        approve_match = re.search(r"/(?:coordinate|rides)/(\d+)/approve/(\d+)/?", p_lower)
        if approve_match:
            return TIER_COMMUTE, f"Approved passenger join request for ride #[{approve_match.group(1)}]"

        reject_match = re.search(r"/(?:coordinate|rides)/(\d+)/reject/(\d+)/?", p_lower)
        if reject_match:
            return TIER_ALERT, f"Declined passenger join request for ride #[{reject_match.group(1)}]"

        safety_match = re.search(r"/(?:coordinate|rides)/safety/?", p_lower)
        if safety_match:
            return TIER_ALERT, "Reported ride safety incident / concern"

        detail_match = re.search(r"/(?:coordinate|rides)/(\d+)/?", p_lower)
        if detail_match:
            return TIER_COMMUTE, f"Viewed ride details #[{detail_match.group(1)}]"

        if "search" in p_lower:
            return TIER_COMMUTE, "Searched available rides with route/time filters"

        return TIER_COMMUTE, "Browsed active campus cab shares & ride listings"

    # 6. Social & Networking (Gold / Yellow Tier)
    profile_match = re.search(r"^/profile/([a-zA-Z0-9_\-]+)/?$", clean_path)
    if profile_match:
        target_enroll = profile_match.group(1).upper()
        if target_enroll in ("EDIT", "UPDATE", "SECURITY"):
            return TIER_AUTH, "Managed personal account settings & security credentials"
        target_name = get_cached_student_name(target_enroll)
        target_label = f"[{target_name}|{target_enroll}]" if target_name else f"[{target_enroll}]"
        return TIER_SOCIAL, f"Viewed profile - {target_label}"

    if p_lower == "/profile/":
        return TIER_SOCIAL, "Viewed personal student profile"

    if p_lower.startswith("/messages/"):
        msg_match = re.search(r"^/messages/([a-zA-Z0-9_\-]+)/?$", clean_path)
        if msg_match:
            partner_enroll = msg_match.group(1).upper()
            partner_name = get_cached_student_name(partner_enroll)
            partner_label = f"[{partner_name}|{partner_enroll}]" if partner_name else f"[{partner_enroll}]"
            return TIER_SOCIAL, f"Opened direct message conversation with {partner_label}"
        return TIER_SOCIAL, "Opened direct messages conversations inbox"

    if p_lower.startswith("/friends/"):
        if "/request/" in p_lower:
            return TIER_SOCIAL, "Sent friend / study partner request"
        if "/accept/" in p_lower:
            return TIER_SOCIAL, "Accepted friend request"
        if "/reject/" in p_lower:
            return TIER_SOCIAL, "Declined friend request"
        return TIER_SOCIAL, "Browsed student friends & connections list"

    if p_lower.startswith("/search/"):
        return TIER_SOCIAL, "Searched campus student directory"

    # 7. Student Dashboard & Service Hubs
    if p_lower == "/dashboard/":
        return TIER_COMMUTE, "Viewed student personal dashboard & active departures"

    if p_lower.startswith("/learn-more/"):
        topic = clean_path.strip("/").split("/")[-1]
        return TIER_AUTH, f"Read technical guide & troubleshooting documentation: [{topic}]"

    if p_lower.startswith("/services/"):
        return TIER_COMMUTE, f"Accessed Campus Services gateway: [{clean_path.strip('/').split('/')[-1]}]"

    if p_lower.startswith(("/feedback/", "/report-bug/")):
        return TIER_ALERT, "Submitted feedback or bug report to developer desk"

    if p_lower == "/":
        return TIER_COMMUTE, "Visited Campus Nexus landing welcome portal"

    # Default fallback
    return TIER_COMMUTE, f"Visited {method} {clean_path}"


def format_descriptive_log_lines(
    server_time: str,
    name: str,
    enrollment: str,
    client_ip: str,
    latency_ms: float,
    path: str,
    method: str = "GET",
    status: int = 200,
    protocol: str = "http",
    ws_action: str = ""
) -> Optional[str]:
    """
    Format a 2-line colored descriptive message if not suppressed.
    Line 1: [Timestamp] [Name|Enrollment|ClientIP|Latency]
    Line 2: Contextual narrative
    """
    if should_suppress_request(path, protocol=protocol, ws_action=ws_action):
        return None

    name_str = name if name and name != "N.A." else "Guest / Anonymous"
    enroll_str = enrollment if enrollment and enrollment != "N.A." else "N.A."
    lat_str = f"{int(round(latency_ms))}ms" if latency_ms is not None else "0ms"

    # Resolve narrative and tier color
    color_code, narrative = resolve_action_narrative(
        path=path,
        method=method,
        status=status,
        protocol=protocol,
        ws_action=ws_action,
        enrollment=enroll_str,
        name=name_str
    )

    line1 = f"[{server_time}] [{name_str}|{enroll_str}|{client_ip}|{lat_str}]"
    line2 = narrative

    return f"{color_code}{line1}\n{line2}{COLOR_RESET}"
