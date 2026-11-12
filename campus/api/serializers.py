"""Campus Nexus API Serializers.

Provides clean serialization of accounts, rides, and catalogue entries
with strict field minimization and privacy preservation.
"""

from typing import Any, Dict, List, Optional
from accounts.models import User
from coordinate.models import RideRequest
from coordinate.policies import can_view_ride_identity


def serialize_user(user: User, include_stats: bool = False) -> Dict[str, Any]:
    """Serialize user profile with sensitive fields redacted."""
    data = {
        "id": user.id,
        "enrollment_number": user.enrollment_number,
        "name": user.display_name,
        "branch": user.branch,
        "degree": user.degree,
        "gender": user.gender,
        "joining_year": user.joining_year,
        "is_verified": user.verified,
        "bio": getattr(user, "bio", ""),
        "profile_photo_url": user.profile_photo.url if (getattr(user, "profile_photo", None) and user.profile_photo.name) else None,
        "show_friends_publicly": getattr(user, "show_friends_publicly", True),
        "hide_online_status": getattr(user, "hide_online_status", False),
        "show_contact_on_profile": getattr(user, "show_contact_on_profile", True),
    }
    if include_stats:
        data.update({
            "on_time_percentage": float(user.on_time_percentage),
            "rides_completed": user.rides_completed,
            "on_time_count": user.on_time_count,
            "late_count": user.late_count,
            "no_show_count": user.no_show_count,
        })
    return data


def serialize_ride(ride: RideRequest, request_user: Optional[User] = None) -> Dict[str, Any]:
    """Serialize ride summary for lists and cards, including host and joined participants."""
    participants = []
    # Use preloaded participants if available to prevent N+1 queries
    p_source = getattr(ride, 'confirmed_participants_list', None)
    if p_source is None:
        p_source = ride.participants.filter(status='CONFIRMED').select_related('user')
    for p in p_source:
        u = p.user
        can_see_id = not request_user or can_view_ride_identity(request_user, ride, u)
        participants.append({
            "enrollment_number": u.enrollment_number if can_see_id else "PROTECTED",
            "name": u.display_name if can_see_id else (f"Student ({u.gender})" if u.gender else "Student"),
            "gender": u.gender,
            "is_creator": u.pk == ride.creator_id,
        })

    data = {
        "id": ride.id,
        "route": ride.route,
        "route_display": ride.route_display,
        "custom_from_location": ride.custom_from_location or "",
        "custom_to_location": ride.custom_to_location or "",
        "departure_time": ride.departure_time.isoformat(),
        "status": ride.status,
        "visibility": ride.visibility,
        "visibility_display": ride.get_visibility_display(),
        "total_spots": ride.total_spots,
        "available_spots": ride.spots_remaining(),
        "creator": {
            "enrollment_number": ride.creator.enrollment_number if not request_user or can_view_ride_identity(request_user, ride, ride.creator) else "PROTECTED",
            "name": ride.creator.display_name if not request_user or can_view_ride_identity(request_user, ride, ride.creator) else (f"Student ({ride.creator.gender})" if ride.creator.gender else "Student"),
            "gender": ride.creator.gender,
        },
        "participants": participants,
    }
    if request_user:
        data["is_user_participant"] = any(p["enrollment_number"] == getattr(request_user, 'enrollment_number', None) for p in participants)
        data["is_user_creator"] = ride.creator_id == request_user.pk
    return data


def serialize_ride_detail(ride: RideRequest, request_user: Optional[User] = None) -> Dict[str, Any]:
    """Serialize full ride details including confirmed participants with affirmative consent contact controls."""
    from coordinate.policies import can_view_ride_contact

    data = serialize_ride(ride, request_user=request_user)
    data["participant_count"] = len(data.get("participants", []))

    # Add contact details for authorized participants
    for p_info, p in zip(data["participants"], ride.participants.filter(status='CONFIRMED').select_related('user')):
        u = p.user
        if request_user and can_view_ride_contact(request_user, ride, u):
            p_info["phone_number"] = u.phone_number
            p_info["email"] = u.email

    return data
