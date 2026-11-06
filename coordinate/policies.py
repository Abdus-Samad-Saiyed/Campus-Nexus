"""Centralized authorization policies and affirmative consent enforcement for COORDINATE.

Policy rules:
1. is_verified_student: Account must be authenticated, verified, active, and not soft-deleted.
2. can_view_student_directory: Accessible only by verified students or administrative staff.
3. can_view_profile_field:
   - Directory fields (name, enrollment, gender, branch, degree): visible to verified students.
   - Contact fields (phone, email): visible only to self, staff, or connected friends.
   - Profile photo: user-controllable (hidden if hide_from_search / privacy toggle unless self/friends/staff).
4. can_view_ride: Audience comfort gating (MIXED, BOYS, GIRLS, FRIENDS) + kicked user isolation.
5. can_view_ride_contact:
   - Expose contact ONLY to confirmed participants of an active, non-cancelled ride
     where both the viewer and the target student have granted affirmative RideConsent.
"""

from typing import Optional
from django.db import models
from django.utils import timezone

from accounts.models import Friendship, User


def is_verified_student(user: Optional[User]) -> bool:
    """Return True if user is an active, verified student in good standing."""
    if not user or not user.is_authenticated:
        return False
    from accounts.demo import is_guest_demo
    if is_guest_demo(user):
        return False
    from django.conf import settings
    is_verified = user.verified or getattr(settings, 'EARLY_LAUNCH_MODE', False)
    return bool(user.is_active and is_verified and not getattr(user, 'is_deleted', False))


def can_view_student_directory(user: Optional[User]) -> bool:
    """Directory browsing is restricted strictly to verified students and staff."""
    if not user or not user.is_authenticated:
        return False
    from accounts.demo import is_guest_demo
    if is_guest_demo(user):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return is_verified_student(user)


def can_view_profile_field(viewer: Optional[User], target_user: User, field_name: str) -> bool:
    """Determine field-level visibility on student profile pages."""
    if not viewer or not viewer.is_authenticated:
        return False

    from accounts.demo import is_guest_demo
    if is_guest_demo(viewer):
        return False

    # Staff and self can view everything
    if viewer == target_user or viewer.is_staff or viewer.is_superuser:
        return True

    # If viewer is not a verified student, they cannot view internal fields
    if not is_verified_student(viewer):
        return False

    # Check friendship
    is_friend = Friendship.objects.filter(
        models.Q(user1=viewer, user2=target_user) | models.Q(user1=target_user, user2=viewer)
    ).exists()

    # Direct contact fields require friendship and show_contact_on_profile toggle
    if field_name in ('phone_number', 'email'):
        return is_friend and getattr(target_user, 'show_contact_on_profile', True)

    # Public institutional fields (name, enrollment, gender, branch, degree, on-time rating)
    if field_name in ('full_name', 'enrollment_number', 'gender', 'branch', 'degree', 'joining_year', 'on_time_percentage'):
        return True

    # Profile photo visibility respects user privacy toggle
    if field_name == 'profile_photo':
        if getattr(target_user, 'hide_profile_photo', False):
            return False
        if getattr(target_user, 'hide_from_search', False) and not is_friend:
            return False
        return True

    # Friends list visibility
    if field_name == 'friends':
        return getattr(target_user, 'show_friends_publicly', True) or is_friend

    return False


def can_view_ride(viewer: Optional[User], ride) -> bool:
    """Enforce audience comfort and membership visibility for rides."""
    if not viewer or not viewer.is_authenticated:
        return False

    from accounts.demo import is_guest_demo
    if is_guest_demo(viewer):
        # Guests can only access full room view for their own ephemeral demo ride
        return bool(getattr(ride, 'demo_session_key', None) and ride.creator_id == viewer.id)

    # Admin override
    if viewer.is_staff or viewer.is_superuser:
        return True

    # Must be a verified student
    if not is_verified_student(viewer):
        return False

    # Kicked users cannot see the ride (check both M2M and unified status)
    from .models import RideParticipant, ParticipantStatus
    if RideParticipant.all_rows.filter(ride=ride, user=viewer, status=ParticipantStatus.KICKED).exists():
        return False
    if hasattr(ride, 'kicked_users') and ride.kicked_users.filter(id=viewer.id).exists():
        return False

    # Creator and confirmed active participants can always view
    if ride.creator_id == viewer.id:
        return True
    if ride.participants.filter(user=viewer, status=ParticipantStatus.CONFIRMED).exists():
        return True

    # Audience visibility rules
    v = ride.visibility
    if v == 'MIXED':
        return True
    if v == 'BOYS':
        return viewer.gender == 'M'
    if v == 'GIRLS':
        return viewer.gender == 'F'
    if v == 'FRIENDS':
        return Friendship.objects.filter(
            models.Q(user1=viewer, user2=ride.creator) | models.Q(user1=ride.creator, user2=viewer)
        ).exists()

    return False



def can_view_ride_identity(viewer: Optional[User], ride, target_user: User) -> bool:
    """Determine whether viewer can see target_user's identity within a ride."""
    if not viewer or not viewer.is_authenticated:
        return False
    if viewer == target_user or viewer.is_staff or viewer.is_superuser:
        return True
    if not can_view_ride(viewer, ride):
        return False

    from .models import RideConsent, RideParticipant, ParticipantStatus

    # Fail closed if participant record indicates explicit revocation
    rp = RideParticipant.all_rows.filter(ride=ride, user=target_user, status=ParticipantStatus.CONFIRMED).first()
    if rp and rp.is_revoked:
        return False

    consent = RideConsent.objects.filter(ride=ride, user=target_user, is_revoked=False).first()
    if consent and not consent.disclose_ride_identity:
        return False

    return True

def can_view_ride_contact(viewer: Optional[User], ride, target_user: User) -> bool:
    """
    Expose contact (phone/email) ONLY to confirmed participants of an active,
    non-cancelled ride where both viewer and target have granted affirmative RideConsent,
    within the allowed coordination window (departure + 2 hours).
    """
    if not viewer or not viewer.is_authenticated:
        return False

    if viewer == target_user:
        return True

    if viewer.is_staff or viewer.is_superuser:
        return True

    # Ride must not be cancelled or completed
    if ride.status in ('CANCELLED', 'COMPLETED'):
        return False

    # Coordination window: cannot retrieve contacts 2+ hours after departure
    from datetime import timedelta
    if timezone.now() > ride.departure_time + timedelta(hours=2):
        return False

    # Both must be confirmed participants
    viewer_is_participant = ride.participants.filter(user=viewer).exists()
    target_is_participant = ride.participants.filter(user=target_user).exists()
    if not (viewer_is_participant and target_is_participant):
        return False

    # Both must have granted active, non-revoked consent to share contact details
    from .models import RideConsent
    viewer_has_consent = RideConsent.objects.filter(
        ride=ride, user=viewer, share_contact_details=True, is_revoked=False
    ).exists()
    target_has_consent = RideConsent.objects.filter(
        ride=ride, user=target_user, share_contact_details=True, is_revoked=False
    ).exists()

    return bool(viewer_has_consent and target_has_consent)


def record_ride_consent(
    ride,
    user: User,
    request=None,
    disclose_identity: bool = True,
    share_contact: bool = True,
    consent_version: str = 'v2.0',
) -> 'RideConsent':
    """Record or update explicit affirmative consent for a ride."""
    from .models import RideConsent
    ip_addr = None
    if request:
        from accounts.throttling import get_client_ip
        ip_addr = get_client_ip(request)

    consent, _ = RideConsent.objects.update_or_create(
        ride=ride,
        user=user,
        defaults={
            'consent_version': consent_version,
            'disclose_ride_identity': bool(disclose_identity),
            'share_contact_details': bool(share_contact),
            'is_revoked': False,
            'revoked_at': None,
            'ip_address': ip_addr,
        }
    )
    from .models import RideParticipant
    RideParticipant.all_rows.filter(ride=ride, user=user).update(
        consent_version=consent_version,
        disclose_ride_identity=bool(disclose_identity),
        share_contact_details=bool(share_contact),
        is_revoked=False,
        revoked_at=None,
        ip_address=ip_addr,
    )
    return consent


def revoke_ride_consent(ride, user: User) -> None:
    """Revoke ride consent when a user leaves or is removed from a ride."""
    from .models import RideConsent, RideParticipant
    now = timezone.now()
    RideConsent.objects.filter(ride=ride, user=user).update(
        is_revoked=True,
        revoked_at=now,
        share_contact_details=False,
    )
    RideParticipant.all_rows.filter(ride=ride, user=user).update(
        is_revoked=True,
        revoked_at=now,
        share_contact_details=False,
    )
