from typing import Any, Dict, List, Optional

from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationReputationSnapshot,
    AccommodationStayLocation,
    MembershipStatus,
)


def serialize_stay_location(stay: Optional[AccommodationStayLocation]) -> Optional[Dict[str, Any]]:
    if not stay:
        return None
    return {
        "id": str(stay.id),
        "name": stay.name,
        "city": stay.city,
        "location_within_city": stay.location_within_city,
        "formatted_address": stay.formatted_address,
        "google_place_id": stay.google_place_id,
        "latitude": float(stay.latitude) if stay.latitude is not None else None,
        "longitude": float(stay.longitude) if stay.longitude is not None else None,
        "usage_count": stay.usage_count,
    }


def serialize_membership_user(user) -> Dict[str, Any]:
    return {
        "id": user.id,
        "enrollment_number": getattr(user, "enrollment_number", str(user.id)),
        "display_name": user.get_full_name() or user.username,
        "branch": getattr(user, "branch", ""),
        "joining_year": getattr(user, "joining_year", None),
    }


def serialize_membership(m: AccommodationMembership) -> Dict[str, Any]:
    return {
        "id": str(m.id),
        "user": serialize_membership_user(m.user),
        "planned_stay": {
            "start": m.planned_stay.lower.isoformat() if m.planned_stay and m.planned_stay.lower else None,
            "end": m.planned_stay.upper.isoformat() if m.planned_stay and m.planned_stay.upper else None,
        },
        "status": m.status,
        "joined_at": m.joined_at.isoformat() if m.joined_at else None,
        "left_at": m.left_at.isoformat() if m.left_at else None,
        "departure_type": m.departure_type,
    }


def serialize_group_summary(group: AccommodationGroup) -> Dict[str, Any]:
    active_count = getattr(group, "active_count", None)
    if active_count is None:
        active_count = group.memberships.filter(status=MembershipStatus.ACTIVE).count()

    return {
        "id": str(group.id),
        "title": group.title,
        "destination_city": group.destination_city,
        "location_within_city": group.location_within_city,
        "lodging_name": group.lodging_name,
        "maps_url": group.maps_url,
        "member_limit": group.member_limit,
        "active_occupants_count": active_count,
        "has_available_capacity": (group.member_limit is None or active_count < group.member_limit),
        "admission_mode": group.admission_mode,
        "recruitment_status": group.recruitment_status,
        "group_status": group.group_status,
        "estimated_cost": {
            "amount": float(group.estimated_cost_amount) if group.estimated_cost_amount is not None else None,
            "basis": group.estimated_cost_basis,
            "currency": group.currency_code,
        },
        "stay_location": serialize_stay_location(group.stay_location),
        "created_at": group.created_at.isoformat() if group.created_at else None,
    }


def serialize_group_detail(group: AccommodationGroup, viewer=None) -> Dict[str, Any]:
    data = serialize_group_summary(group)
    data["description"] = group.description
    data["cost_note"] = group.cost_note
    data["initiated_by"] = serialize_membership_user(group.initiated_by)
    data["current_owner"] = serialize_membership_user(group.current_owner) if group.current_owner else None

    active_members = list(
        group.memberships.filter(status=MembershipStatus.ACTIVE).select_related("user")
    )
    data["occupants"] = [serialize_membership(m) for m in active_members]

    if viewer and viewer.is_authenticated:
        data["viewer_relationship"] = {
            "is_owner": (group.current_owner_id == viewer.id),
            "is_active_member": any(m.user_id == viewer.id for m in active_members),
        }

    return data


def serialize_application(app: AccommodationJoinApplication) -> Dict[str, Any]:
    return {
        "id": str(app.id),
        "group_id": str(app.group_id),
        "applicant": serialize_membership_user(app.applicant),
        "requested_stay": {
            "start": app.requested_stay.lower.isoformat() if app.requested_stay and app.requested_stay.lower else None,
            "end": app.requested_stay.upper.isoformat() if app.requested_stay and app.requested_stay.upper else None,
        },
        "message": app.message,
        "status": app.status,
        "objection_round": app.objection_round,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "resolved_at": app.resolved_at.isoformat() if app.resolved_at else None,
        "resolution_code": app.resolution_code,
    }


def serialize_reputation_snapshot(snapshot: Optional[AccommodationReputationSnapshot]) -> Optional[Dict[str, Any]]:
    if not snapshot:
        return None
    return {
        "composite_score": float(snapshot.composite_score) if snapshot.composite_score is not None else None,
        "review_count": snapshot.review_count,
        "group_count": snapshot.group_count,
        "has_limited_history": snapshot.has_limited_history,
        "scores": {
            "reliability": float(snapshot.reliability_average) if snapshot.reliability_average is not None else None,
            "communication": float(snapshot.communication_average) if snapshot.communication_average is not None else None,
            "cooperation": float(snapshot.cooperation_average) if snapshot.cooperation_average is not None else None,
            "respect": float(snapshot.respect_average) if snapshot.respect_average is not None else None,
        },
        "computed_at": snapshot.computed_at.isoformat() if snapshot.computed_at else None,
    }
