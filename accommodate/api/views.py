from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from psycopg.types.range import Range

from accounts.models import User
from campus.api.auth import api_auth_required, get_api_user, json_error, json_success, parse_json_body
from accommodate.exceptions import (
    ApplicationStateError,
    CapacityRequiredError,
    GroupStateError,
    OverlapConflictError,
    PermissionDeniedError as AccommodatePermissionDenied,
)
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationReputationSnapshot,
    AccommodationStayLocation,
    AdmissionMode,
    CostBasis,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)
from accommodate.selectors.application_selectors import get_group_inbox
from accommodate.selectors.group_selectors import (
    get_active_cities,
    get_group_detail,
    get_popular_stays,
    search_groups,
)
from accommodate.services.admission_service import approve_manual
from accommodate.services.application_service import (
    reject_application,
    submit_application,
    withdraw_application,
)
from accommodate.services.group_service import (
    create_group,
    set_admission_mode,
    toggle_recruitment,
    update_group_details,
    update_member_limit,
)
from accommodate.services.membership_service import leave_group, update_planned_stay
from .serializers import (
    serialize_application,
    serialize_group_detail,
    serialize_group_summary,
    serialize_membership,
    serialize_reputation_snapshot,
    serialize_stay_location,
)


@require_GET
def api_cities(request: HttpRequest) -> JsonResponse:
    """Return active destination cities with recruiting group counts."""
    cities = get_active_cities()
    return json_success({"cities": cities})


@require_GET
def api_popular_stays(request: HttpRequest) -> JsonResponse:
    """Return top seeded and frequently used student stay locations."""
    city = request.GET.get("city", "").strip() or None
    limit = int(request.GET.get("limit", 15))
    stays = get_popular_stays(city=city, limit=limit)
    return json_success({"stays": [serialize_stay_location(s) for s in stays]})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_groups_list_create(request: HttpRequest) -> JsonResponse:
    """GET: Search accommodation groups. POST: Form a new accommodation group."""
    if request.method == "GET":
        city = request.GET.get("city", "").strip() or None
        location = request.GET.get("location", "").strip() or None
        q = request.GET.get("q", "").strip() or None
        has_slots_val = request.GET.get("has_slots", "").strip()
        has_slots = True if has_slots_val in ["1", "true", "True"] else None
        mode = request.GET.get("mode", "").strip()
        admission_mode = mode if mode in [AdmissionMode.MANUAL, AdmissionMode.AUTO] else None

        limit = min(int(request.GET.get("limit", 20)), 50)
        page = max(int(request.GET.get("page", 1)), 1)
        offset = (page - 1) * limit

        groups, total_count = search_groups(
            city=city,
            location=location,
            q=q,
            has_slots=has_slots,
            admission_mode=admission_mode,
            limit=limit,
            offset=offset,
        )

        return json_success({
            "groups": [serialize_group_summary(g) for g in groups],
            "total_count": total_count,
            "page": page,
            "limit": limit,
        })

    # POST: Create group
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    body, err = parse_json_body(request)
    if err:
        return err

    title = body.get("title", "").strip()
    destination_city = body.get("destination_city", "").strip()
    location_within_city = body.get("location_within_city", "").strip()
    start_str = body.get("start_date", "").strip()
    end_str = body.get("end_date", "").strip()

    if not title or not destination_city or not location_within_city or not start_str or not end_str:
        return json_error("title, destination_city, location_within_city, start_date, and end_date are required.")

    try:
        start_d = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_str, "%Y-%m-%d").date()
        if end_d <= start_d:
            return json_error("end_date must be strictly after start_date.")
        planned_stay = Range(start_d, end_d, bounds="[]")
    except ValueError:
        return json_error("Dates must be in YYYY-MM-DD format.")

    lodging_name = body.get("lodging_name", "").strip()
    maps_url = body.get("maps_url", "").strip()
    description = body.get("description", "").strip()
    member_limit = body.get("member_limit")
    if member_limit is not None:
        try:
            member_limit = int(member_limit)
            if member_limit < 1:
                return json_error("member_limit must be >= 1.")
        except (ValueError, TypeError):
            return json_error("member_limit must be an integer or null.")

    admission_mode = body.get("admission_mode", AdmissionMode.MANUAL)
    stay_location_id = body.get("stay_location_id")

    estimated_cost = None
    cost_val = body.get("estimated_cost_amount")
    if cost_val is not None:
        try:
            estimated_cost = Decimal(str(cost_val))
        except (InvalidOperation, ValueError):
            return json_error("Invalid estimated_cost_amount.")

    cost_basis = body.get("estimated_cost_basis")
    cost_note = body.get("cost_note", "").strip()

    try:
        group = create_group(
            creator=user,
            title=title,
            destination_city=destination_city,
            location_within_city=location_within_city,
            creator_planned_stay=planned_stay,
            lodging_name=lodging_name,
            maps_url=maps_url,
            description=description,
            member_limit=member_limit,
            admission_mode=admission_mode,
            stay_location_id=stay_location_id,
            estimated_cost_amount=estimated_cost,
            estimated_cost_basis=cost_basis,
            cost_note=cost_note,
        )
        return json_success({"group": serialize_group_detail(group, viewer=user)}, status=201)
    except OverlapConflictError as e:
        return json_error(str(e), status=409, code="OVERLAP_CONFLICT")
    except Exception as e:
        return json_error(str(e), status=400)


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
def api_group_detail_update(request: HttpRequest, group_id) -> JsonResponse:
    """GET: Retrieve group details. PATCH: Update group settings (Owner only)."""
    user = get_api_user(request)

    try:
        group = AccommodationGroup.objects.select_related(
            "initiated_by", "current_owner", "stay_location"
        ).prefetch_related("memberships__user").get(id=group_id)
    except AccommodationGroup.DoesNotExist:
        return json_error("Accommodation group not found.", status=404, code="NOT_FOUND")

    if request.method == "GET":
        return json_success({"group": serialize_group_detail(group, viewer=user)})

    # PATCH
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")
    if group.current_owner != user and not user.is_staff:
        return json_error("Only the group owner can update group settings.", status=403, code="FORBIDDEN")

    body, err = parse_json_body(request)
    if err:
        return err

    try:
        # Update metadata if supplied
        update_group_details(
            group_id=group.id,
            actor=user,
            title=body.get("title"),
            destination_city=body.get("destination_city"),
            location_within_city=body.get("location_within_city"),
            lodging_name=body.get("lodging_name"),
            maps_url=body.get("maps_url"),
            description=body.get("description"),
            estimated_cost_amount=Decimal(str(body["estimated_cost_amount"])) if "estimated_cost_amount" in body else None,
            estimated_cost_basis=body.get("estimated_cost_basis"),
            cost_note=body.get("cost_note"),
        )

        if "member_limit" in body:
            limit = int(body["member_limit"]) if body["member_limit"] is not None else None
            update_member_limit(group.id, user, limit)

        if "admission_mode" in body:
            set_admission_mode(group.id, user, body["admission_mode"])

        if "recruitment_status" in body:
            toggle_recruitment(group.id, user, body["recruitment_status"])

        group.refresh_from_db()
        return json_success({"group": serialize_group_detail(group, viewer=user)})
    except Exception as e:
        return json_error(str(e), status=400)


@csrf_exempt
@require_POST
def api_group_apply(request: HttpRequest, group_id) -> JsonResponse:
    """Submit join application for an accommodation group."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    body, err = parse_json_body(request)
    if err:
        return err

    start_str = body.get("start_date", "").strip()
    end_str = body.get("end_date", "").strip()
    message = body.get("message", "").strip()

    if not start_str or not end_str:
        return json_error("start_date and end_date are required.")

    try:
        start_d = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_str, "%Y-%m-%d").date()
        if end_d <= start_d:
            return json_error("end_date must be after start_date.")
        stay_range = Range(start_d, end_d, bounds="[]")
    except ValueError:
        return json_error("Dates must be in YYYY-MM-DD format.")

    try:
        app = submit_application(group_id, user, stay_range, message=message)
        return json_success({"application": serialize_application(app)}, status=201)
    except OverlapConflictError as e:
        return json_error(str(e), status=409, code="OVERLAP_CONFLICT")
    except (GroupStateError, ApplicationStateError, AccommodatePermissionDenied) as e:
        return json_error(str(e), status=400)


@require_GET
def api_group_inbox(request: HttpRequest, group_id) -> JsonResponse:
    """Owner's view of incoming applications."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    try:
        inbox = get_group_inbox(group_id, user)
        return json_success({
            "pending_applications": [serialize_application(a) for a in inbox["pending_applications"]],
            "waitlisted_applications": [serialize_application(a) for a in inbox["waitlisted_applications"]],
            "past_applications": [serialize_application(a) for a in inbox["past_applications"]],
        })
    except PermissionDenied as e:
        return json_error(str(e), status=403, code="FORBIDDEN")


@csrf_exempt
@require_POST
def api_application_approve(request: HttpRequest, application_id) -> JsonResponse:
    """Group owner approves manual application."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    try:
        membership = approve_manual(application_id, user)
        return json_success({"membership": serialize_membership(membership)})
    except CapacityRequiredError as e:
        return json_error(str(e), status=409, code="CAPACITY_REQUIRED")
    except Exception as e:
        return json_error(str(e), status=400)


@csrf_exempt
@require_POST
def api_application_reject(request: HttpRequest, application_id) -> JsonResponse:
    """Group owner rejects application."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    body, _ = parse_json_body(request)
    reason = body.get("reason", "").strip() if body else ""

    try:
        app = reject_application(application_id, user, reason)
        return json_success({"application": serialize_application(app)})
    except Exception as e:
        return json_error(str(e), status=400)


@csrf_exempt
@require_POST
def api_application_withdraw(request: HttpRequest, application_id) -> JsonResponse:
    """Applicant withdraws their application."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    try:
        app = withdraw_application(application_id, user)
        return json_success({"application": serialize_application(app)})
    except Exception as e:
        return json_error(str(e), status=400)


@csrf_exempt
@require_POST
def api_membership_leave(request: HttpRequest, membership_id) -> JsonResponse:
    """Member voluntarily departs group."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    try:
        membership = leave_group(membership_id, user)
        return json_success({"membership": serialize_membership(membership)})
    except Exception as e:
        return json_error(str(e), status=400)


@csrf_exempt
@require_POST
def api_membership_dates(request: HttpRequest, membership_id) -> JsonResponse:
    """Member updates declared stay period."""
    user = get_api_user(request)
    if not user:
        return json_error("Authentication required.", status=401, code="AUTH_REQUIRED")

    body, err = parse_json_body(request)
    if err:
        return err

    start_str = body.get("start_date", "").strip()
    end_str = body.get("end_date", "").strip()

    if not start_str or not end_str:
        return json_error("start_date and end_date are required.")

    try:
        start_d = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_str, "%Y-%m-%d").date()
        if end_d <= start_d:
            return json_error("end_date must be after start_date.")
        new_stay = Range(start_d, end_d, bounds="[]")
    except ValueError:
        return json_error("Dates must be in YYYY-MM-DD format.")

    try:
        change = update_planned_stay(membership_id, user, new_stay)
        membership = change.membership
        return json_success({"membership": serialize_membership(membership)})
    except OverlapConflictError as e:
        return json_error(str(e), status=409, code="OVERLAP_CONFLICT")
    except Exception as e:
        return json_error(str(e), status=400)


@require_GET
def api_reputation_detail(request: HttpRequest, enrollment: str) -> JsonResponse:
    """Get a student's accommodation reputation snapshot."""
    user = get_object_or_404(User, enrollment_number=enrollment)
    snapshot = AccommodationReputationSnapshot.objects.filter(user=user).first()
    return json_success({
        "enrollment_number": enrollment,
        "reputation": serialize_reputation_snapshot(snapshot),
    })
