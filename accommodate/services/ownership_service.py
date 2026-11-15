from django.db import transaction
from django.utils import timezone

from accounts.models import User, log_audit_event
from accommodate.exceptions import (
    GroupStateError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationGroup,
    AccommodationGroupOwnershipEvent,
    AccommodationMembership,
    GroupStatus,
    MembershipStatus,
    OwnershipEventType,
)


@transaction.atomic
def transfer_ownership(
    group_id,
    actor: User,
    new_owner: User,
) -> AccommodationGroup:
    """Owner transfers management authority to another active occupant of the group."""
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can transfer ownership.")

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot transfer ownership of an ended group.")

    # Target must be an active member of this group
    is_target_active = group.memberships.filter(
        user=new_owner,
        status=MembershipStatus.ACTIVE
    ).exists()
    if not is_target_active:
        raise PermissionDeniedError("Ownership can only be transferred to a current active occupant.")

    old_owner = group.current_owner
    group.current_owner = new_owner
    group.version += 1
    group.save(update_fields=['current_owner', 'version', 'updated_at'])

    AccommodationGroupOwnershipEvent.objects.create(
        group=group,
        from_user=old_owner,
        to_user=new_owner,
        event_type=OwnershipEventType.TRANSFERRED,
        actor=actor,
        created_at=timezone.now(),
    )

    actor_enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    new_owner_enrollment = getattr(new_owner, 'enrollment_number', str(new_owner.id))
    log_audit_event(
        event_type='ACCOMMODATE_OWNER_TRANSFERRED',
        actor_enrollment=actor_enrollment,
        description=f"Transferred ownership of '{group.title}' to {new_owner_enrollment}",
    )

    return group


@transaction.atomic
def claim_ownership(
    group_id,
    claimant: User,
) -> AccommodationGroup:
    """An active occupant claims management of an ownerless active group."""
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot claim management of an ended group.")

    if group.current_owner is not None:
        raise GroupStateError("This group already has an active owner.")

    # Claimant must be an active occupant of this group
    is_active_occupant = group.memberships.filter(
        user=claimant,
        status=MembershipStatus.ACTIVE
    ).exists()
    if not is_active_occupant:
        raise PermissionDeniedError("Only active occupants of this group can claim ownership.")

    group.current_owner = claimant
    group.version += 1
    group.save(update_fields=['current_owner', 'version', 'updated_at'])

    AccommodationGroupOwnershipEvent.objects.create(
        group=group,
        from_user=None,
        to_user=claimant,
        event_type=OwnershipEventType.CLAIMED,
        actor=claimant,
        created_at=timezone.now(),
    )

    claimant_enrollment = getattr(claimant, 'enrollment_number', str(claimant.id))
    log_audit_event(
        event_type='ACCOMMODATE_OWNER_CLAIMED',
        actor_enrollment=claimant_enrollment,
        description=f"Claimed ownership of ownerless group '{group.title}' (ID: {group.id})",
    )

    return group
