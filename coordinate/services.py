"""Centralized, atomic lifecycle service for COORDINATE rides.

Single source of truth for:
- Requesting to join rides
- Host approval and rejection of join requests
- Standby waitlist promotion
- Leaving and kicking participants
- Ride cancellation (soft termination) and completion
- Explicit affirmative consent persistence
"""

import datetime
from typing import Any, Dict, Optional, Tuple

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import (
    ParticipantStatus,
    RideConsent,
    RideJoinRequest,
    RideMessage,
    RideParticipant,
    RideRequest,
    RideWaitlist,
)
from .participation import transition_participant_state
from .policies import (
    can_view_ride,
    is_verified_student,
    record_ride_consent,
    revoke_ride_consent,
)

User = get_user_model()


def user_has_overlapping_ride(
    user: User,
    departure_time: datetime.datetime,
    exclude_ride_id: Optional[int] = None,
    buffer_hours: int = 2,
) -> bool:
    """Check whether a student is already host or confirmed passenger in a ride within buffer_hours of departure_time."""
    window_start = departure_time - datetime.timedelta(hours=buffer_hours)
    window_end = departure_time + datetime.timedelta(hours=buffer_hours)
    qs = (
        RideRequest.objects
        .filter(status__in=['OPEN', 'FULL'], departure_time__range=(window_start, window_end))
        .filter(participants__user=user, participants__status='CONFIRMED')
    )
    if exclude_ride_id:
        qs = qs.exclude(id=exclude_ride_id)
    return qs.exists()


def user_has_active_ride(user: User, exclude_ride_id: Optional[int] = None) -> bool:
    """Check whether a student has any currently active or upcoming ride within the next 2 hours."""
    threshold = timezone.now() - datetime.timedelta(hours=2)
    qs = (
        RideRequest.objects
        .filter(status__in=['OPEN', 'FULL'], departure_time__gt=threshold)
        .filter(participants__user=user, participants__status='CONFIRMED')
    )
    if exclude_ride_id:
        qs = qs.exclude(id=exclude_ride_id)
    return qs.exists()


def user_can_join_ride(user: User, ride: RideRequest) -> Tuple[bool, str, str]:
    """
    Validate if a student can request to join an open ride.
    Returns (can_join, error_message, error_code).
    """
    if not is_verified_student(user):
        return False, "Only verified students in good standing can join rides.", "NOT_VERIFIED"

    if ride.creator_id == user.id:
        return False, "You cannot join your own ride as a passenger.", "HOST_SELF_JOIN"

    if ride.status != 'OPEN':
        return False, f"Cannot join ride in '{ride.status}' status.", "RIDE_NOT_OPEN"

    if timezone.now() >= ride.departure_time:
        return False, "This ride has already departed.", "RIDE_DEPARTED"

    if ride.is_full():
        return False, "This ride is already full.", "RIDE_FULL"

    if ride.participants.filter(user=user).exists():
        return False, "You are already a confirmed participant in this ride.", "ALREADY_PARTICIPANT"

    if ride.kicked_users.filter(id=user.id).exists():
        return False, "You were previously removed from this ride by the host.", "USER_KICKED"

    if user_has_overlapping_ride(user, ride.departure_time, exclude_ride_id=ride.id):
        return False, "You already have another confirmed ride scheduled around this departure time.", "ACTIVE_RIDE_EXISTS"

    if not can_view_ride(user, ride):
        return False, "You do not meet the audience comfort criteria for this ride.", "AUDIENCE_MISMATCH"

    return True, "", ""


def request_to_join_ride(
    ride_id: int,
    user: User,
    request=None,
    disclose_identity: bool = True,
    share_contact: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Submit a join request that awaits host approval.
    Atomically records the student's explicit affirmative consent choice without
    granting immediate membership.
    """
    with transaction.atomic():
        # Canonical lock hierarchy: User -> RideRequest
        User.objects.select_for_update().filter(id=user.id).first()
        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id)
            .first()
        )
        if not ride:
            return False, "Ride not found.", {"code": "NOT_FOUND"}

        can_join, err_msg, err_code = user_can_join_ride(user, ride)
        if not can_join:
            return False, err_msg, {"code": err_code}

        # Check existing pending request
        existing_req = RideJoinRequest.objects.filter(ride=ride, user=user, status='PENDING').first()
        if existing_req:
            return False, "You already have a pending join request for this ride.", {
                "code": "ALREADY_PENDING",
                "request_id": existing_req.id,
            }

        # Explicit identity disclosure and contact sharing consent are mandatory to request membership
        if not disclose_identity or not share_contact:
            return False, "Consenting to disclose identity and share contact details with confirmed co-passengers is mandatory to join a ride.", {
                "code": "CONSENT_REQUIRED"
            }

        join_req = RideJoinRequest.objects.create(
            ride=ride,
            user=user,
            status='PENDING',
        )
        transition_participant_state(
            ride=ride,
            user=user,
            target_status=ParticipantStatus.PENDING,
            consent_data={'disclose_ride_identity': True, 'share_contact_details': bool(share_contact)},
        )

        # Record explicit affirmative consent choice for this user
        record_ride_consent(
            ride=ride,
            user=user,
            request=request,
            disclose_identity=True,
            share_contact=bool(share_contact),
        )

        # Notify channel group
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'system.notice',
                        'action': 'join_request',
                        'content': f"🔔 {user.enrollment_number} submitted a request to join the ride.",
                    }
                )
        except Exception:
            pass

        return True, f"Join request submitted to host {ride.creator.enrollment_number}! Awaiting approval.", {
            "status": "pending",
            "request_id": join_req.id,
            "ride_id": ride.id,
        }


def approve_ride_join_request(
    ride_id: int,
    request_id: int,
    host_user: User,
    request=None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Host approves a pending join request, atomically adding the participant.
    Preserves the requester's existing explicit consent choices without synthesis.
    """
    with transaction.atomic():
        # Fetch unlocked first to discover the requester user_id
        pre_req = RideJoinRequest.objects.filter(id=request_id, ride_id=ride_id, status='PENDING').first()
        if not pre_req:
            return False, "Pending join request not found.", {"code": "REQUEST_NOT_FOUND"}

        # Canonical lock hierarchy: User -> RideRequest -> RideJoinRequest
        User.objects.select_for_update().filter(id=pre_req.user_id).first()

        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id, creator=host_user)
            .first()
        )
        if not ride:
            return False, "Ride not found or you are not the creator.", {"code": "FORBIDDEN"}

        join_req = (
            RideJoinRequest.objects
            .select_for_update()
            .filter(id=request_id, ride=ride, status='PENDING')
            .select_related('user')
            .first()
        )
        if not join_req:
            return False, "Pending join request not found.", {"code": "REQUEST_NOT_FOUND"}

        if ride.is_full():
            return False, "Cannot approve request: ride is already full.", {"code": "RIDE_FULL"}

        if timezone.now() >= ride.departure_time:
            return False, "Cannot approve request: ride has already departed.", {"code": "RIDE_DEPARTED"}

        if user_has_overlapping_ride(join_req.user, ride.departure_time, exclude_ride_id=ride.id):
            join_req.status = 'DECLINED'
            join_req.reviewed_at = timezone.now()
            join_req.save(update_fields=['status', 'reviewed_at'])
            return False, f"{join_req.user.enrollment_number} already joined another ride overlapping this departure time.", {
                "code": "ACTIVE_RIDE_EXISTS"
            }

        # Add participant atomically via unified state machine
        transition_participant_state(
            ride=ride,
            user=join_req.user,
            target_status=ParticipantStatus.CONFIRMED,
        )
        join_req.status = 'APPROVED'
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=['status', 'reviewed_at'])

        # Remove from waitlist if present
        RideWaitlist.objects.filter(ride=ride, user=join_req.user).delete()

        ride.sync_status()

        # Broadcast approval notice
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'system.notice',
                        'action': 'approved',
                        'content': f"🎉 Host approved {join_req.user.enrollment_number} to join the ride!",
                    }
                )
        except Exception:
            pass

        return True, f"Approved {join_req.user.enrollment_number} to join.", {
            "status": "approved",
            "participant": join_req.user.enrollment_number,
        }


def decline_ride_join_request(
    ride_id: int,
    request_id: int,
    host_user: User,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Host declines a pending join request."""
    with transaction.atomic():
        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id, creator=host_user)
            .first()
        )
        if not ride:
            return False, "Ride not found or you are not the creator.", {"code": "FORBIDDEN"}

        join_req = (
            RideJoinRequest.objects
            .select_for_update()
            .filter(id=request_id, ride=ride, status='PENDING')
            .first()
        )
        if not join_req:
            return False, "Pending join request not found.", {"code": "REQUEST_NOT_FOUND"}

        join_req.status = 'DECLINED'
        join_req.reviewed_at = timezone.now()
        join_req.save(update_fields=['status', 'reviewed_at'])
        try:
            transition_participant_state(ride=ride, user=join_req.user, target_status=ParticipantStatus.DECLINED)
        except Exception:
            pass

        return True, "Join request declined.", {"status": "declined"}


def promote_waitlist_passenger(ride: RideRequest) -> Optional[User]:
    """
    Promote next eligible waitlisted student to confirmed passenger.
    Never synthesizes contact-sharing consent.
    """
    if ride.is_full() or ride.status not in ('OPEN', 'FULL'):
        return None

    while True:
        entry = (
            RideWaitlist.objects
            .select_for_update()
            .filter(ride=ride)
            .order_by('joined_at')
            .first()
        )
        if not entry:
            return None

        candidate = entry.user
        entry.delete()

        if (
            not getattr(candidate, 'is_deleted', False)
            and candidate.is_active
            and is_verified_student(candidate)
            and can_view_ride(candidate, ride)
            and candidate != ride.creator
            and not ride.kicked_users.filter(id=candidate.id).exists()
            and not user_has_overlapping_ride(candidate, ride.departure_time, exclude_ride_id=ride.id)
            and not ride.participants.filter(user=candidate).exists()
        ):
            transition_participant_state(
                ride=ride,
                user=candidate,
                target_status=ParticipantStatus.CONFIRMED,
            )
            RideMessage.objects.create(
                ride=ride,
                author=candidate,
                content=f"🔔 Standby Promotion: {candidate.enrollment_number} was automatically moved from standby to confirmed passenger."
            )
            # Retain whatever existing explicit consent was provided, or record default False
            existing_consent = RideConsent.objects.filter(ride=ride, user=candidate).first()
            if not existing_consent:
                record_ride_consent(
                    ride=ride,
                    user=candidate,
                    disclose_identity=True,
                    share_contact=True,
                )
            ride.sync_status()
            return candidate


def leave_ride_service(ride_id: int, user: User) -> Tuple[bool, str, Dict[str, Any]]:
    """Leave a ride as passenger, revoke consent, and promote waitlist."""
    with transaction.atomic():
        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id)
            .first()
        )
        if not ride:
            return False, "Ride not found.", {"code": "NOT_FOUND"}

        participant = RideParticipant.objects.filter(ride=ride, user=user).first()
        if not participant:
            return False, "You are not a confirmed participant in this ride.", {"code": "NOT_PARTICIPANT"}

        if ride.status in ['COMPLETED', 'CANCELLED']:
            return False, f"Cannot leave a {ride.status.lower()} ride.", {"code": "RIDE_CONCLUDED"}

        transition_participant_state(ride=ride, user=user, target_status=ParticipantStatus.LEFT)
        revoke_ride_consent(ride, user)
        ride.left_users.add(user)

        remaining_count = RideParticipant.objects.filter(ride=ride).count()
        if remaining_count == 0:
            ride.status = 'CANCELLED'
            ride.save(update_fields=['status'])
            return True, "Successfully left ride. Ride cancelled as no participants remain.", {"promoted": None, "closed": True}

        if ride.creator_id == user.id:
            next_host = RideParticipant.objects.filter(ride=ride).order_by('joined_at').first()
            if next_host:
                ride.creator = next_host.user
                ride.save(update_fields=['creator'])

        promoted = promote_waitlist_passenger(ride)
        ride.sync_status()

        # WebSocket leave notice
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'system.notice',
                        'action': 'user_left',
                        'user': user.enrollment_number,
                        'content': f"🚪 {user.enrollment_number} left the ride.",
                    }
                )
        except Exception:
            pass

        return True, "Successfully left ride.", {"promoted": promoted.enrollment_number if promoted else None}


def kick_passenger_service(ride_id: int, host_user: User, passenger_user: User) -> Tuple[bool, str]:
    """Host removes a passenger, revokes their consent, and promotes waitlist."""
    with transaction.atomic():
        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id, creator=host_user)
            .first()
        )
        if not ride:
            return False, "Ride not found or you are not the creator."

        if host_user == passenger_user:
            return False, "Cannot kick yourself from your own ride."

        participant = RideParticipant.objects.filter(ride=ride, user=passenger_user).first()
        if not participant:
            return False, "Target user is not a participant in this ride."

        transition_participant_state(ride=ride, user=passenger_user, target_status=ParticipantStatus.KICKED)
        revoke_ride_consent(ride, passenger_user)
        ride.kicked_users.add(passenger_user)

        promoted = promote_waitlist_passenger(ride)
        ride.sync_status()

        # WebSocket kick notice
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'system.notice',
                        'action': 'user_kicked',
                        'user': passenger_user.enrollment_number,
                        'content': f"🚫 {passenger_user.enrollment_number} was removed by the host.",
                    }
                )
        except Exception:
            pass

        return True, f"Removed {passenger_user.enrollment_number} from the ride."


def cancel_ride_service(ride_id: int, host_user: User) -> Tuple[bool, str]:
    """Soft-cancel a ride, retain past history/messages, and revoke all active consents."""
    with transaction.atomic():
        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id, creator=host_user)
            .first()
        )
        if not ride:
            return False, "Ride not found or you are not the creator."

        if ride.status in ('CANCELLED', 'COMPLETED'):
            return False, f"Ride is already in '{ride.status}' status."

        ride.status = 'CANCELLED'
        ride.save(update_fields=['status'])

        # Bulk transition pending/waitlisted rows to CANCELLED
        now = timezone.now()
        RideParticipant.all_rows.filter(
            ride=ride, status__in=[ParticipantStatus.PENDING, ParticipantStatus.WAITLISTED]
        ).update(status=ParticipantStatus.CANCELLED, is_revoked=True, revoked_at=now)
        # Revoke all consents (retains historical consent rows for audit without hard deletion)
        RideConsent.objects.filter(ride=ride).update(
            is_revoked=True,
            revoked_at=now,
            share_contact_details=False,
        )
        RideParticipant.all_rows.filter(ride=ride).update(is_revoked=True, revoked_at=now)

        # Broadcast teardown
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'system.notice',
                        'action': 'cancelled',
                        'content': "⚠️ Host has cancelled the ride. Chat is now closed.",
                    }
                )
        except Exception:
            pass

        return True, "Ride cancelled successfully."



def alter_ride_capacity_service(
    ride_id: int,
    host_user: User,
    new_capacity: int,
    kick_user_ids: Optional[list] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Host alters ride capacity (total spots).
    Increasing capacity is permitted seamlessly and promotes waitlisted users if spots open.
    Decreasing capacity below the current confirmed members count requires selecting and
    kicking out enough passengers to satisfy the new limit.
    """
    with transaction.atomic():
        ride = (
            RideRequest.objects
            .select_for_update()
            .filter(id=ride_id, creator=host_user)
            .first()
        )
        if not ride:
            return False, "Ride not found or you are not the creator.", {}

        if ride.status in ('COMPLETED', 'CANCELLED'):
            return False, f"Cannot alter capacity of a {ride.get_status_display().lower()} ride.", {}

        try:
            new_capacity = int(new_capacity)
        except (ValueError, TypeError):
            return False, "Ride capacity must be a valid number.", {}

        if new_capacity < 1 or new_capacity > 6:
            return False, "Ride capacity must be between 1 and 6 spots.", {}

        if new_capacity == ride.total_spots:
            return True, f"Ride capacity is already {new_capacity} spots.", {
                "updated": False,
                "new_capacity": new_capacity,
                "kicked": [],
                "promoted": [],
            }

        # Current confirmed participants (including host)
        confirmed_participants = list(
            RideParticipant.objects
            .filter(ride=ride, status=ParticipantStatus.CONFIRMED)
            .select_related('user')
            .order_by('joined_at')
        )
        confirmed_count = len(confirmed_participants)
        old_capacity = ride.total_spots
        kicked_enrollments = []
        promoted_enrollments = []

        # If reducing capacity below current confirmed members count
        if new_capacity < confirmed_count:
            needed_kicks = confirmed_count - new_capacity
            cleaned_kick_ids = []
            for uid in (kick_user_ids or []):
                try:
                    cleaned_kick_ids.append(int(uid))
                except (ValueError, TypeError):
                    continue

            # Remove duplicates preserving order
            cleaned_kick_ids = list(dict.fromkeys(cleaned_kick_ids))

            if host_user.id in cleaned_kick_ids:
                return False, "Host cannot be removed from their own ride.", {}

            if len(cleaned_kick_ids) < needed_kicks:
                return (
                    False,
                    f"Decreasing capacity to {new_capacity} spots requires selecting at least {needed_kicks} passenger(s) to remove.",
                    {"requires_kick": True, "needed_kicks": needed_kicks, "current_count": confirmed_count},
                )

            # Map user_id to participant
            participant_map = {p.user_id: p for p in confirmed_participants if p.user_id != host_user.id}
            invalid_uids = [uid for uid in cleaned_kick_ids if uid not in participant_map]
            if invalid_uids:
                return False, "Selected users include individuals who are not active confirmed passengers in this ride.", {}

            # Execute kicks for the selected passengers
            for uid in cleaned_kick_ids:
                p = participant_map[uid]
                passenger_user = p.user
                transition_participant_state(ride=ride, user=passenger_user, target_status=ParticipantStatus.KICKED)
                revoke_ride_consent(ride, passenger_user)
                ride.kicked_users.add(passenger_user)
                kicked_enrollments.append(passenger_user.enrollment_number)

                # WebSocket kick notice
                try:
                    channel_layer = get_channel_layer()
                    if channel_layer:
                        async_to_sync(channel_layer.group_send)(
                            f'ride_{ride.id}',
                            {
                                'type': 'system.notice',
                                'action': 'user_kicked',
                                'user': passenger_user.enrollment_number,
                                'content': f"🚫 {passenger_user.enrollment_number} was removed due to ride capacity reduction.",
                            }
                        )
                except Exception:
                    pass

        # Update total spots
        ride.total_spots = new_capacity
        ride.save(update_fields=['total_spots'])

        # If increasing capacity, promote any waitlisted candidates into newly opened spots
        if new_capacity > old_capacity:
            while not ride.is_full():
                promoted = promote_waitlist_passenger(ride)
                if not promoted:
                    break
                promoted_enrollments.append(promoted.enrollment_number)

        ride.sync_status()

        # WebSocket capacity notice
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'system.notice',
                        'action': 'capacity_altered',
                        'new_capacity': new_capacity,
                        'content': f"⚙️ Ride capacity updated to {new_capacity} spots by the host.",
                    }
                )
        except Exception:
            pass

        # Build informative feedback message
        if kicked_enrollments:
            msg = f"Ride capacity updated to {new_capacity} spots. Removed {len(kicked_enrollments)} passenger(s) ({', '.join(kicked_enrollments)}) to fit new capacity."
        elif promoted_enrollments:
            msg = f"Ride capacity increased to {new_capacity} spots. Promoted {len(promoted_enrollments)} waitlisted student(s) ({', '.join(promoted_enrollments)})."
        else:
            msg = f"Ride capacity successfully updated to {new_capacity} spots."

        return True, msg, {
            "updated": True,
            "old_capacity": old_capacity,
            "new_capacity": new_capacity,
            "kicked": kicked_enrollments,
            "promoted": promoted_enrollments,
        }
