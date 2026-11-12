"""Campus Nexus Versioned REST API Views (v1).

Implements web/API parity for core platform resources:
- Service Catalogue
- Authentication & Session Management
- User Profile & Safety Stats
- COORDINATE Ride Discovery, Detail, Creation, Join, Leave, and History
"""

import datetime
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.sessions.models import Session
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from accounts.models import User
from accounts.throttling import rate_limit
from accounts.views import enforce_single_session_login
from campus.services import get_all_services, get_service_by_slug
from coordinate.models import RideParticipant, RideRequest
from coordinate.policies import (
    is_verified_student,
    record_ride_consent,
    revoke_ride_consent,
)
from coordinate.services import (
    approve_ride_join_request,
    decline_ride_join_request,
    leave_ride_service,
    request_to_join_ride,
    user_has_overlapping_ride,
)
from coordinate.views import (
    _are_friends,
    _parse_date,
    _parse_time,
    _promote_next_waitlist_passenger,
    _user_can_join_ride,
    _user_can_see_ride,
    _user_has_active_ride,
)

from .auth import (
    api_auth_required,
    get_api_user,
    json_error,
    json_success,
    parse_json_body,
)
from .serializers import (
    serialize_ride,
    serialize_ride_detail,
    serialize_user,
)


# ---------------------------------------------------------------------------
# API Root & Service Catalogue
# ---------------------------------------------------------------------------

@require_GET
def api_root(request: HttpRequest) -> JsonResponse:
    """API entry point returning service metadata and versioned endpoints."""
    return json_success({
        "platform": "Campus Nexus",
        "version": "v1",
        "endpoints": {
            "catalogue": "/api/v1/catalogue/",
            "login": "/api/v1/auth/login/",
            "logout": "/api/v1/auth/logout/",
            "me": "/api/v1/me/",
            "me_photo": "/api/v1/me/photo/",
            "rides": "/api/v1/rides/",
            "my_rides": "/api/v1/my-rides/",
        },
    })


@require_GET
def api_catalogue(request: HttpRequest) -> JsonResponse:
    """Return all platform services in the Campus Nexus directory."""
    services = get_all_services()
    return json_success({"services": services})


@require_GET
def api_service_detail(request: HttpRequest, slug: str) -> JsonResponse:
    """Return detailed capability and readiness metadata for a specific service."""
    service = get_service_by_slug(slug)
    if not service:
        return json_error(f"Service '{slug}' not found.", status=404, code="SERVICE_NOT_FOUND")
    return json_success({"service": service})


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
@rate_limit(key_prefix='api_login', limit=10, period_seconds=60)
def api_login(request: HttpRequest) -> JsonResponse:
    """Authenticate student credentials and return a session token."""
    body, err = parse_json_body(request)
    if err:
        return err

    enrollment = str(body.get("enrollment_number", "")).strip().upper()
    password = str(body.get("password", ""))

    if not enrollment or not password:
        return json_error(
            "Both 'enrollment_number' and 'password' are required.",
            status=400,
            code="MISSING_CREDENTIALS",
        )

    user = authenticate(request, username=enrollment, password=password)
    if not user or not user.is_active:
        return json_error(
            "Invalid enrollment number or password, or account unverified.",
            status=401,
            code="INVALID_CREDENTIALS",
        )

    # 2FA Enforcement for students with 2FA enabled, staff, superusers (unless debug account)
    requires_2fa = (user.two_factor_enabled or user.is_staff or user.is_superuser) and not user.is_debug_account
    if requires_2fa:
        otp_code = str(body.get("otp", "")).strip()
        if not otp_code:
            from accounts.two_factor import can_resend_otp, create_phone_otp, get_active_otp, emit_otp_console_log
            can_resend, wait_sec = can_resend_otp(user, purpose="2FA")
            if can_resend:
                create_phone_otp(user, purpose="2FA")
            else:
                active_otp = get_active_otp(user, purpose="2FA")
                if active_otp:
                    request._active_otp_info = f"[OTP: {active_otp} (Cooldown: {wait_sec}s)]"
                    emit_otp_console_log(active_otp, user, purpose="2FA", reason=f"API 2FA resend limit (wait {wait_sec}s)", wait_seconds=wait_sec)
            return json_error(
                "Two-factor authentication required. An OTP has been dispatched.",
                status=403,
                code="2FA_REQUIRED",
                details={"two_factor_required": True},
            )
        from accounts.two_factor import verify_phone_otp
        ok, msg = verify_phone_otp(user, submitted_code=otp_code, purpose="2FA")
        if not ok:
            return json_error(
                msg or "Invalid or expired 2FA OTP code.",
                status=401,
                code="INVALID_2FA_OTP",
            )

    # Ensure a session is instantiated and single active session is enforced
    if not request.session.session_key:
        request.session.create()
    login(request, user)
    enforce_single_session_login(request, user)
    request.session['auth_timestamp'] = timezone.now().timestamp()
    request.session.save()

    token = request.session.session_key
    return json_success(
        {
            "token": token,
            "token_type": "Bearer",
            "user": serialize_user(user, include_stats=True),
        },
        status=200,
    )


@csrf_exempt
@require_POST
def api_logout(request: HttpRequest) -> JsonResponse:
    """Invalidate session and logout current user."""
    auth_header = request.headers.get("Authorization", "") or request.META.get("HTTP_AUTHORIZATION", "")
    token = None
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            token = parts[1].strip()

    if token:
        try:
            user = get_api_user(request)
            Session.objects.filter(session_key=token).delete()
            if user and user.current_session_key == token:
                user.current_session_key = ""
                user.save(update_fields=["current_session_key"])
        except Exception:
            pass

    logout(request)
    return json_success({"message": "Successfully logged out."})


# ---------------------------------------------------------------------------
# Current User Profile
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["GET", "PATCH", "PUT"])
@api_auth_required
def api_me(request: HttpRequest) -> JsonResponse:
    """Return or update profile and active ride for the authenticated user."""
    user = request.api_user

    if request.method in ("PATCH", "PUT"):
        body, err = parse_json_body(request)
        if err:
            return err

        allowed_fields = {
            "bio", "phone_number", "gender",
            "show_friends_publicly", "hide_online_status",
            "hide_from_search", "hide_profile_photo",
            "show_contact_on_profile",
        }
        updated = []
        for field in allowed_fields:
            if field in body:
                val = body[field]
                if field == "bio":
                    user.bio = str(val)[:500]
                elif field == "phone_number":
                    user.phone_number = str(val)[:15]
                elif field == "gender" and str(val).upper() in ("M", "F"):
                    user.gender = str(val).upper()
                elif field in (
                    "show_friends_publicly", "hide_online_status",
                    "hide_from_search", "hide_profile_photo",
                    "show_contact_on_profile"
                ):
                    setattr(user, field, bool(val))
                updated.append(field)

        if updated:
            user.save(update_fields=updated)

    user_data = serialize_user(user, include_stats=True)

    active_p = (
        RideParticipant.objects
        .filter(user=user, ride__status__in=["OPEN", "FULL"])
        .select_related("ride", "ride__creator")
        .first()
    )

    active_ride_data = None
    if active_p:
        active_ride_data = serialize_ride(active_p.ride, request_user=user)

    return json_success({
        "user": user_data,
        "active_ride": active_ride_data,
    })


@csrf_exempt
@require_POST
@api_auth_required
def api_me_photo_upload(request: HttpRequest) -> JsonResponse:
    """Upload or update profile photo for authenticated user from mobile or web client."""
    user = request.api_user
    photo = request.FILES.get("profile_photo")
    if not photo:
        return json_error("No 'profile_photo' file found in upload.", status=400, code="MISSING_FILE")

    if photo.size > 5 * 1024 * 1024:
        return json_error("Profile photo must be less than 5MB.", status=400, code="FILE_TOO_LARGE")

    valid_content_types = {"image/jpeg", "image/png", "image/webp"}
    if photo.content_type not in valid_content_types:
        return json_error("Unsupported image format. Allowed: JPEG, PNG, WEBP.", status=400, code="INVALID_IMAGE_TYPE")

    user.profile_photo = photo
    user.save(update_fields=["profile_photo"])

    return json_success({
        "message": "Profile photo uploaded successfully.",
        "profile_photo_url": user.profile_photo.url if user.profile_photo else None,
        "user": serialize_user(user, include_stats=True),
    })


# ---------------------------------------------------------------------------
# COORDINATE Ride Endpoints
# ---------------------------------------------------------------------------

@require_GET
def api_ride_list(request: HttpRequest) -> JsonResponse:
    """List open rides respecting filters and audience comfort gates for verified students."""
    user = get_api_user(request)
    if not user or not (user.is_staff or user.is_superuser or is_verified_student(user)):
        return json_error("Authentication as a verified student required.", status=401, code="AUTH_REQUIRED")

    now = timezone.now()

    from django.db.models import Prefetch, Count
    from coordinate.models import RideParticipant, ParticipantStatus

    confirmed_prefetch = Prefetch(
        'participants',
        queryset=RideParticipant.objects.filter(status=ParticipantStatus.CONFIRMED).select_related('user'),
        to_attr='confirmed_participants_list'
    )

    rides = (
        RideRequest.objects
        .filter(status="OPEN", departure_time__gt=now)
        .select_related("creator")
        .prefetch_related(confirmed_prefetch)
        .annotate(participants_total=Count('participants', filter=Q(participants__status=ParticipantStatus.CONFIRMED)))
        .order_by("departure_time")
    )

    # Route and location filtering
    route = request.GET.get("route", "").strip().upper()
    if route:
        rides = rides.filter(route=route)

    origin = request.GET.get("origin", "").strip()
    if origin:
        rides = rides.filter(
            Q(custom_from_location__icontains=origin) |
            Q(route__icontains=origin)
        )

    destination = request.GET.get("destination", "").strip()
    if destination:
        rides = rides.filter(
            Q(custom_to_location__icontains=destination) |
            Q(route__icontains=destination)
        )

    visibility = request.GET.get("visibility", "").strip().upper()
    if visibility:
        rides = rides.filter(visibility=visibility)

    # Date-time range filtering
    date_from_str = request.GET.get("date_from", "").strip() or request.GET.get("date", "").strip()
    date_to_str = request.GET.get("date_to", "").strip()
    time_from_str = request.GET.get("time_from", "").strip() or request.GET.get("time", "").strip()
    time_to_str = request.GET.get("time_to", "").strip()

    parsed_date_from = _parse_date(date_from_str)
    parsed_date_to = _parse_date(date_to_str)
    parsed_time_from = _parse_time(time_from_str)
    parsed_time_to = _parse_time(time_to_str)

    current_tz = timezone.get_current_timezone()

    if parsed_date_from:
        start_t = parsed_time_from or datetime.time(0, 0, 0)
        start_dt = timezone.make_aware(datetime.datetime.combine(parsed_date_from, start_t), current_tz)
        rides = rides.filter(departure_time__gte=start_dt)

    if parsed_date_to:
        end_t = parsed_time_to or datetime.time(23, 59, 59)
        end_dt = timezone.make_aware(datetime.datetime.combine(parsed_date_to, end_t), current_tz)
        rides = rides.filter(departure_time__lte=end_dt)
    elif parsed_date_from and not parsed_date_to and not request.GET.get("date_from"):
        # Single 'date' parameter passed: constrain to that day
        end_t = parsed_time_to or datetime.time(23, 59, 59)
        end_dt = timezone.make_aware(datetime.datetime.combine(parsed_date_from, end_t), current_tz)
        rides = rides.filter(departure_time__lte=end_dt)

    # Server-authoritative audience comfort gating
    try:
        limit = min(max(int(request.GET.get("limit", 25)), 1), 100)
    except (ValueError, TypeError):
        limit = 25
    try:
        page = max(int(request.GET.get("page", 1)), 1)
    except (ValueError, TypeError):
        page = 1

    visible_rides = []
    for ride in rides:
        if _user_can_see_ride(user, ride):
            visible_rides.append(serialize_ride(ride, request_user=user))

    total_count = len(visible_rides)
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_rides = visible_rides[start_idx:end_idx]

    return json_success({
        "count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": (total_count + limit - 1) // limit if total_count > 0 else 1,
        "rides": paginated_rides,
    })


@require_GET
def api_ride_detail(request: HttpRequest, ride_id: int) -> JsonResponse:
    """Return full details and participant list for a specific ride."""
    user = get_api_user(request)
    if not user or not (user.is_staff or user.is_superuser or is_verified_student(user)):
        return json_error("Authentication as a verified student required.", status=401, code="AUTH_REQUIRED")

    ride = (
        RideRequest.objects
        .filter(pk=ride_id)
        .select_related("creator")
        .prefetch_related("participants__user")
        .first()
    )
    if not ride:
        return json_error("Ride not found.", status=404, code="RIDE_NOT_FOUND")

    # Visibility check
    if not _user_can_see_ride(user, ride):
        return json_error("You do not have permission to view this ride.", status=403, code="FORBIDDEN")

    return json_success({"ride": serialize_ride_detail(ride, request_user=user)})


@csrf_exempt
@require_POST
@api_auth_required
def api_ride_create(request: HttpRequest) -> JsonResponse:
    """Create a new ride request adhering to all domain invariants."""
    user = request.api_user
    if not is_verified_student(user):
        return json_error("Authentication as a verified student required.", status=403, code="FORBIDDEN")

    body, err = parse_json_body(request)
    if err:
        return err

    route = str(body.get("route", "")).strip().upper()
    origin = str(body.get("origin", "")).strip()
    destination = str(body.get("destination", "")).strip()
    custom_from = str(body.get("custom_from_location", "")).strip() or origin
    custom_to = str(body.get("custom_to_location", "")).strip() or destination
    dep_str = str(body.get("departure_time", "")).strip()
    total_spots = body.get("total_spots", 3)
    visibility = str(body.get("visibility", "MIXED")).strip().upper()

    if not dep_str:
        return json_error(
            "'departure_time' is required.",
            status=400,
            code="MISSING_DEPARTURE",
        )

    valid_routes = [c[0] for c in RideRequest.ROUTE_CHOICES]
    if route:
        if route in valid_routes:
            resolved_route = route
        else:
            return json_error(
                f"Invalid route: '{route}'. Must be one of {valid_routes}.",
                status=400,
                code="INVALID_ROUTE",
            )
    elif "AIRPORT" in custom_to.upper() and ("NIT" in custom_from.upper() or not custom_from):
        resolved_route = "NIT_TO_AIRPORT"
    elif "AIRPORT" in custom_from.upper() and ("NIT" in custom_to.upper() or not custom_to):
        resolved_route = "AIRPORT_TO_NIT"
    elif ("STATION" in custom_to.upper() or "RAILWAY" in custom_to.upper()) and ("NIT" in custom_from.upper() or not custom_from):
        resolved_route = "NIT_TO_STATION"
    elif ("STATION" in custom_from.upper() or "RAILWAY" in custom_from.upper()) and ("NIT" in custom_to.upper() or not custom_to):
        resolved_route = "STATION_TO_NIT"
    elif custom_from and custom_to:
        resolved_route = "CUSTOM"
    else:
        resolved_route = "NIT_TO_AIRPORT"

    if len(custom_from) > 255 or len(custom_to) > 255:
        return json_error("Location names cannot exceed 255 characters.", status=400, code="LOCATION_TOO_LONG")

    if resolved_route == "CUSTOM" and (not custom_from or not custom_to):
        return json_error(
            "For custom routes, both origin and destination are required.",
            status=400,
            code="MISSING_FIELDS",
        )

    # Validate departure_time
    dep_time = parse_datetime(dep_str)
    if not dep_time:
        return json_error(
            "Invalid 'departure_time' format. Use ISO 8601 (YYYY-MM-DDTHH:MM[:SS]).",
            status=400,
            code="INVALID_DATETIME",
        )

    if timezone.is_naive(dep_time):
        dep_time = timezone.make_aware(dep_time, timezone.get_current_timezone())

    now = timezone.now()
    if dep_time <= now:
        return json_error("Departure time must be in the future.", status=400, code="PAST_DEPARTURE")

    max_horizon = now + datetime.timedelta(days=365)
    if dep_time > max_horizon:
        return json_error("Departure cannot be scheduled more than 1 year in advance.", status=400, code="HORIZON_EXCEEDED")

    # Catalogue overload protection: limit active rides to 10 per account
    active_rides_count = RideRequest.objects.filter(
        creator=user,
        status__in=["OPEN", "FULL"],
    ).count()
    if active_rides_count >= 10:
        return json_error(
            "Ride creation limit reached: you cannot have more than 10 active rides in the catalogue.",
            status=400,
            code="RIDE_LIMIT_EXCEEDED",
        )

    # Validate spots
    try:
        total_spots = int(total_spots)
        if not (1 <= total_spots <= 6):
            raise ValueError()
    except (ValueError, TypeError):
        return json_error("'total_spots' must be an integer between 1 and 6.", status=400, code="INVALID_SPOTS")

    # Validate visibility choices & creator eligibility
    valid_vis = [c[0] for c in RideRequest.VISIBILITY_CHOICES]
    if visibility not in valid_vis:
        return json_error(f"'visibility' must be one of {valid_vis}.", status=400, code="INVALID_VISIBILITY")

    if visibility == "GIRLS" and user.gender != "F":
        return json_error("Only female students can create Girls Only rides.", status=403, code="GENDER_MISMATCH")
    if visibility == "BOYS" and user.gender != "M":
        return json_error("Only male students can create Boys Only rides.", status=403, code="GENDER_MISMATCH")

    # Enforce affirmative identity consent BEFORE opening atomic transaction
    disclose_id = body.get("disclose_identity", True)
    share_cont = body.get("share_contact", True) is not False
    if disclose_id is False:
        return json_error("Affirmative consent to disclose identity is required.", status=400, code="CONSENT_REQUIRED")

    if user_has_overlapping_ride(user, dep_time):
        return json_error(
            "You already have another confirmed ride departing within 2 hours of this time.",
            status=409,
            code="ACTIVE_RIDE_EXISTS",
        )

    with transaction.atomic():
        User.objects.select_for_update().filter(id=user.id).first()
        if user_has_overlapping_ride(user, dep_time):
            return json_error(
                "You already have another confirmed ride departing within 2 hours of this time.",
                status=409,
                code="ACTIVE_RIDE_EXISTS",
            )
        ride = RideRequest.objects.create(
            creator=user,
            route=resolved_route,
            custom_from_location=custom_from if resolved_route == "CUSTOM" else "",
            custom_to_location=custom_to if resolved_route == "CUSTOM" else "",
            departure_time=dep_time,
            total_spots=total_spots,
            visibility=visibility,
            status="OPEN",
        )
        RideParticipant.objects.create(ride=ride, user=user)
        record_ride_consent(
            ride,
            user,
            request=request,
            disclose_identity=True,
            share_contact=share_cont,
        )
        ride.sync_status()

    return json_success({"ride": serialize_ride_detail(ride, request_user=user)}, status=201)


@csrf_exempt
@require_POST
@api_auth_required
def api_ride_join(request: HttpRequest, ride_id: int) -> JsonResponse:
    """Submit a request to join a ride awaiting host approval."""
    user = request.api_user
    if not (user.is_staff or user.is_superuser or is_verified_student(user)):
        return json_error("Authentication as a verified student required.", status=403, code="FORBIDDEN")
    body, _ = parse_json_body(request)
    disclose_identity = True
    share_contact = True
    if body and isinstance(body, dict):
        if body.get("disclose_identity") is False:
            return json_error("Explicit consent to associate your identity with this ride is required.", status=400, code="CONSENT_REQUIRED")
        disclose_identity = bool(body.get("disclose_identity", True) is not False)
        share_contact = bool(body.get("share_contact", True) is not False)

    ok, msg, details = request_to_join_ride(
        ride_id=ride_id,
        user=user,
        request=request,
        disclose_identity=disclose_identity,
        share_contact=share_contact,
    )
    if not ok:
        code = details.get("code", "JOIN_REJECTED")
        if code == "NOT_FOUND":
            return json_error(msg, status=404, code=code)
        if code in ("AUDIENCE_MISMATCH", "USER_KICKED", "HOST_SELF_JOIN", "NOT_VERIFIED"):
            return json_error(msg, status=403, code=code)
        if code in ("ALREADY_PENDING", "ALREADY_PARTICIPANT", "ACTIVE_RIDE_EXISTS", "OVERLAPPING_RIDE_EXISTS", "RIDE_NOT_OPEN", "RIDE_FULL", "RIDE_DEPARTED"):
            return json_error(msg, status=409, code=code)
        return json_error(msg, status=400, code=code)

    return json_success({
        "status": "pending",
        "message": msg,
        "request_id": details.get("request_id"),
        "ride_id": details.get("ride_id"),
    }, status=202)


@csrf_exempt
@require_POST
@api_auth_required
def api_ride_approve_request(request: HttpRequest, ride_id: int, request_id: int) -> JsonResponse:
    """Host approves a pending join request via API."""
    user = request.api_user
    ok, msg, details = approve_ride_join_request(
        ride_id=ride_id,
        request_id=request_id,
        host_user=user,
        request=request,
    )
    if not ok:
        code = details.get("code", "APPROVAL_FAILED")
        status = 404 if code in ("FORBIDDEN", "REQUEST_NOT_FOUND") else 400
        return json_error(msg, status=status, code=code)

    ride = RideRequest.objects.get(id=ride_id)
    return json_success({
        "message": msg,
        "ride": serialize_ride_detail(ride, request_user=user),
    })


@csrf_exempt
@require_POST
@api_auth_required
def api_ride_decline_request(request: HttpRequest, ride_id: int, request_id: int) -> JsonResponse:
    """Host declines a pending join request via API."""
    user = request.api_user
    ok, msg, details = decline_ride_join_request(
        ride_id=ride_id,
        request_id=request_id,
        host_user=user,
    )
    if not ok:
        code = details.get("code", "DECLINE_FAILED")
        status = 404 if code in ("FORBIDDEN", "REQUEST_NOT_FOUND") else 400
        return json_error(msg, status=status, code=code)

    return json_success({"message": msg})


@csrf_exempt
@require_POST
@api_auth_required
def api_ride_leave(request: HttpRequest, ride_id: int) -> JsonResponse:
    """Atomically leave a ride."""
    user = request.api_user
    if not (user.is_staff or user.is_superuser or is_verified_student(user)):
        return json_error("Authentication as a verified student required.", status=403, code="FORBIDDEN")
    ok, msg, details = leave_ride_service(ride_id=ride_id, user=user)
    if not ok:
        code = details.get("code", "LEAVE_FAILED")
        status = 404 if code == "NOT_FOUND" else (409 if code == "RIDE_CONCLUDED" else 400)
        return json_error(msg, status=status, code=code)

    if details.get("closed"):
        return json_success({"message": "Ride cancelled and cleared."})

    ride = RideRequest.objects.get(id=ride_id)
    return json_success({
        "message": msg,
        "ride": serialize_ride(ride, request_user=user),
    })


@require_GET
@api_auth_required
def api_my_rides(request: HttpRequest) -> JsonResponse:
    """Return user's active ride and historical rides."""
    user = request.api_user
    if not (user.is_staff or user.is_superuser or is_verified_student(user)):
        return json_error("Authentication as a verified student required.", status=403, code="FORBIDDEN")
    participations = (
        RideParticipant.objects
        .filter(user=user)
        .select_related("ride", "ride__creator")
        .order_by("-ride__departure_time")
    )

    now = timezone.now()
    two_hours_ago = now - datetime.timedelta(hours=2)

    active_rides = []
    past_rides = []

    for p in participations:
        ride = p.ride
        serialized = serialize_ride(ride, request_user=user)
        if ride.status in ["OPEN", "FULL"] and ride.departure_time > two_hours_ago:
            active_rides.append(serialized)
        else:
            past_rides.append(serialized)

    return json_success({
        "active_rides": active_rides,
        "past_rides": past_rides,
    })


@require_GET
def api_recent_notifications(request: HttpRequest) -> JsonResponse:
    """Retrieve recent active notifications, announcements, and requests for authenticated user."""
    user = get_api_user(request, allow_cookie=True)
    if not user or not user.is_authenticated:
        return json_error("Authentication required.", status=401, code="UNAUTHORIZED")

    notifications = []

    # 1. Active Announcements
    try:
        from accounts.models import Announcement, AnnouncementReceipt
        active_anns = Announcement.get_active_for_user(user)
        seen_ids = set(
            AnnouncementReceipt.objects.filter(user=user, dismissed=True).values_list('announcement_id', flat=True)
        )
        for ann in active_anns[:10]:
            notifications.append({
                'id': f'ann_{ann.id}',
                'category': 'announcement',
                'title': ann.title,
                'content': ann.content,
                'priority': ann.priority,
                'icon': 'bi-megaphone-fill',
                'badge': ann.get_priority_display() if hasattr(ann, 'get_priority_display') else 'Announcement',
                'badge_class': (
                    'bg-primary-subtle text-primary border border-primary-subtle' if ann.priority == 'INFO' else (
                        'bg-danger-subtle text-danger border border-danger-subtle' if ann.priority == 'CRITICAL' else (
                            'bg-warning-subtle text-warning border border-warning-subtle'
                        )
                    )
                ),
                'url': ann.action_url if (ann.action_url and ann.action_url.startswith('/')) else '/dashboard/',
                'timestamp': ann.created_at.strftime('%d %b, %H:%M') if ann.created_at else '',
                'read': ann.id in seen_ids,
            })
    except Exception:
        pass

    # 2. Incoming Friend Requests
    try:
        from accounts.models import FriendRequest
        frs = FriendRequest.objects.filter(receiver=user).select_related('sender')[:10]
        for fr in frs:
            sender = fr.sender
            notifications.append({
                'id': f'fr_{fr.id}',
                'category': 'social',
                'title': f'Friend Request from {sender.get_full_name() or sender.enrollment_number}',
                'content': f'{sender.enrollment_number} wants to connect with you.',
                'icon': 'bi-person-plus-fill',
                'badge': 'Friend Request',
                'badge_class': 'bg-info-subtle text-info border border-info-subtle',
                'url': f'/profile/{sender.enrollment_number}/',
                'timestamp': fr.created_at.strftime('%d %b, %H:%M') if hasattr(fr, 'created_at') and fr.created_at else '',
                'read': False,
            })
    except Exception:
        pass

    # 3. Active Ride Updates / Requests
    try:
        from coordinate.models import RideJoinRequest
        host_reqs = RideJoinRequest.objects.filter(
            ride__creator=user,
            ride__status='OPEN',
            status='PENDING'
        ).select_related('user', 'ride')[:10]
        for req in host_reqs:
            notifications.append({
                'id': f'rjr_{req.id}',
                'category': 'ride',
                'title': f'Ride Application: {req.ride.get_route_display() if hasattr(req.ride, "get_route_display") else req.ride.route}',
                'content': f'{req.user.enrollment_number} applied to join your ride.',
                'icon': 'bi-car-front-fill',
                'badge': 'Pending Approval',
                'badge_class': 'bg-warning-subtle text-warning border border-warning-subtle',
                'url': f'/coordinate/{req.ride_id}/',
                'timestamp': req.created_at.strftime('%d %b, %H:%M') if hasattr(req, 'created_at') and req.created_at else '',
                'read': False,
            })
    except Exception:
        pass

    unread = len([n for n in notifications if not n.get('read', False)])
    return json_success(
        data={
            'notifications': notifications,
            'unread_count': unread,
        },
        notifications=notifications,
        unread_count=unread,
    )
