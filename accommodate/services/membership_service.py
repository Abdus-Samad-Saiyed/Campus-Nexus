from django.db import transaction
from django.utils import timezone
from psycopg.types.range import Range

from accounts.models import User, log_audit_event
from accommodate.exceptions import (
    ApplicationStateError,
    GroupStateError,
    OverlapConflictError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationMembershipDateChange,
    AdmissionMode,
    ApplicationStatus,
    DepartureReason,
    DepartureType,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)


@transaction.atomic
def update_planned_stay(
    membership_id,
    actor: User,
    new_stay: Range,
) -> AccommodationMembershipDateChange:
    """Active occupant updates their declared stay period directly.
    
    Verifies cross-group non-overlap and auto-rescinds any now-overlapping pending requests.
    """
    # 1. Lock user row to serialize date changes
    User.objects.select_for_update().get(id=actor.id)

    membership = AccommodationMembership.objects.select_for_update().get(id=membership_id)

    if membership.user != actor and not actor.is_staff:
        raise PermissionDeniedError("You can only modify your own stay period.")

    if membership.status != MembershipStatus.ACTIVE:
        raise ApplicationStateError("Can only modify planned dates for an active membership.")

    # 2. Check for overlap with any other active membership belonging to this user
    has_conflict = AccommodationMembership.objects.filter(
        user=membership.user,
        status=MembershipStatus.ACTIVE,
        planned_stay__overlap=new_stay
    ).exclude(id=membership.id).exists()
    if has_conflict:
        raise OverlapConflictError(
            "The updated stay period overlaps another confirmed active membership you hold."
        )

    old_stay = membership.planned_stay
    membership.planned_stay = new_stay
    membership.save(update_fields=['planned_stay', 'updated_at'])

    # 3. Record date change audit
    change_log = AccommodationMembershipDateChange.objects.create(
        membership=membership,
        old_period=old_stay,
        new_period=new_stay,
        changed_by=actor,
        changed_at=timezone.now(),
    )

    # 4. Cascade auto-rescind: Any pending/waitlisted applications now overlapping the new period
    AccommodationJoinApplication.objects.filter(
        applicant=membership.user,
        status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED],
        requested_stay__overlap=new_stay,
    ).update(
        status=ApplicationStatus.AUTO_RESCINDED,
        resolution_code='OVERLAPPING_MEMBERSHIP_ACCEPTED',
        resolved_at=timezone.now(),
    )

    enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    log_audit_event(
        event_type='ACCOMMODATE_DATES_UPDATED',
        actor_enrollment=enrollment,
        description=f"Updated stay dates in group {membership.group_id} to {new_stay}",
    )

    return change_log


@transaction.atomic
def leave_group(membership_id, actor: User) -> AccommodationMembership:
    """Voluntary exit from an accommodation group."""
    # 1. Determine group and lock in correct hierarchy: Group -> Membership
    group_id = AccommodationMembership.objects.filter(id=membership_id).values_list('group_id', flat=True).first()
    if not group_id:
        raise ApplicationStateError("Membership not found.")

    group = AccommodationGroup.objects.select_for_update().get(id=group_id)
    membership = AccommodationMembership.objects.select_for_update().get(id=membership_id)

    if membership.user != actor and not actor.is_staff:
        raise PermissionDeniedError("You can only depart from your own membership.")

    if membership.status != MembershipStatus.ACTIVE:
        raise ApplicationStateError("Membership is already inactive.")

    # If owner leaves while recruitment is OPEN, block unless transferred or recruitment closed
    if group.current_owner == actor and group.recruitment_status == RecruitmentStatus.OPEN:
        active_peer_count = group.memberships.filter(
            status=MembershipStatus.ACTIVE
        ).exclude(id=membership.id).count()
        if active_peer_count > 0:
            raise GroupStateError(
                "You are the group owner. You must transfer ownership to another active member "
                "or close recruitment before leaving."
            )

    membership.status = MembershipStatus.LEFT
    membership.left_at = timezone.now()
    membership.departure_type = DepartureType.VOLUNTARY
    membership.save(update_fields=['status', 'left_at', 'departure_type', 'updated_at'])

    # If owner leaves, clear current owner
    if group.current_owner == actor:
        group.current_owner = None

    # Check remaining active occupants
    remaining_active = group.memberships.filter(status=MembershipStatus.ACTIVE).count()
    if remaining_active == 0:
        group.group_status = GroupStatus.ENDED
        group.recruitment_status = RecruitmentStatus.CLOSED
        group.current_owner = None
        # Bulk-expire any remaining pending/waitlisted applications
        AccommodationJoinApplication.objects.filter(
            group=group,
            status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED]
        ).update(
            status=ApplicationStatus.EXPIRED,
            resolution_code='GROUP_ENDED',
            resolved_at=timezone.now(),
        )

    group.version += 1
    group.save()

    # If capacity opened up in an active AUTO group, process the FIFO queue
    if (
        group.group_status == GroupStatus.ACTIVE
        and group.admission_mode == AdmissionMode.AUTO
        and group.recruitment_status == RecruitmentStatus.OPEN
    ):
        from accommodate.services.admission_service import process_auto_queue
        process_auto_queue(group.id)

    # Trigger review invitations
    from accommodate.services.reputation_service import issue_review_invitations
    issue_review_invitations(membership)

    enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    log_audit_event(
        event_type='ACCOMMODATE_MEMBER_DEPARTED',
        actor_enrollment=enrollment,
        description=f"Voluntarily left group '{group.title}' (ID: {group.id})",
    )

    return membership


@transaction.atomic
def remove_member(
    membership_id,
    actor: User,
    reason_code: str,
    reason_text: str = '',
) -> AccommodationMembership:
    """Owner removes an occupant from the group with mandatory reason category."""
    # 1. Determine group and lock in correct hierarchy: Group -> Membership
    group_id = AccommodationMembership.objects.filter(id=membership_id).values_list('group_id', flat=True).first()
    if not group_id:
        raise ApplicationStateError("Membership not found.")

    group = AccommodationGroup.objects.select_for_update().get(id=group_id)
    membership = AccommodationMembership.objects.select_for_update().get(id=membership_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can remove members.")

    if membership.user == actor:
        raise PermissionDeniedError("Owner cannot self-remove via remove_member. Use leave_group instead.")

    if membership.status != MembershipStatus.ACTIVE:
        raise ApplicationStateError("Cannot remove an inactive member.")

    if reason_code not in DepartureReason.values:
        raise ValueError(f"Invalid departure reason category: {reason_code}")

    membership.status = MembershipStatus.REMOVED
    membership.left_at = timezone.now()
    membership.departure_type = DepartureType.REMOVED
    membership.departure_reason_code = reason_code
    membership.departure_reason_text = reason_text.strip()
    membership.removed_by = actor
    membership.save(update_fields=[
        'status', 'left_at', 'departure_type',
        'departure_reason_code', 'departure_reason_text',
        'removed_by', 'updated_at'
    ])

    group.version += 1
    group.save(update_fields=['version', 'updated_at'])

    # If capacity opened up in an active AUTO group, process the FIFO queue
    if (
        group.group_status == GroupStatus.ACTIVE
        and group.admission_mode == AdmissionMode.AUTO
        and group.recruitment_status == RecruitmentStatus.OPEN
    ):
        from accommodate.services.admission_service import process_auto_queue
        process_auto_queue(group.id)

    # Trigger review invitations
    from accommodate.services.reputation_service import issue_review_invitations
    issue_review_invitations(membership)

    actor_enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    member_enrollment = getattr(membership.user, 'enrollment_number', str(membership.user.id))
    log_audit_event(
        event_type='ACCOMMODATE_MEMBER_REMOVED',
        actor_enrollment=actor_enrollment,
        description=f"Removed {member_enrollment} from '{group.title}' for {reason_code}",
    )

    return membership
