from decimal import Decimal
from typing import Optional
from django.db import transaction
from django.utils import timezone
from psycopg.types.range import Range

from accounts.models import User, log_audit_event
from accommodate.exceptions import (
    CapacityRequiredError,
    GroupStateError,
    OverlapConflictError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationGroup,
    AccommodationMembership,
    AccommodationJoinApplication,
    AccommodationStayLocation,
    AdmissionMode,
    ApplicationStatus,
    CostBasis,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)


@transaction.atomic
def create_group(
    creator: User,
    title: str,
    destination_city: str,
    location_within_city: str,
    creator_planned_stay: Range,
    lodging_name: str = '',
    maps_url: str = '',
    description: str = '',
    member_limit: Optional[int] = None,
    admission_mode: str = AdmissionMode.MANUAL,
    stay_location_id: Optional[str] = None,
    stay_location: Optional[AccommodationStayLocation] = None,
    estimated_cost_amount: Optional[Decimal] = None,
    estimated_cost_basis: Optional[str] = None,
    currency_code: str = 'INR',
    cost_note: str = '',
) -> AccommodationGroup:
    """Create an accommodation group and admit the initiator as its first active occupant atomically."""
    # 1. Lock creator user row to serialize cross-group commitments
    User.objects.select_for_update().get(id=creator.id)

    # 2. Check for active membership date conflict across all groups
    has_conflict = AccommodationMembership.objects.filter(
        user=creator,
        status=MembershipStatus.ACTIVE,
        planned_stay__overlap=creator_planned_stay
    ).exists()
    if has_conflict:
        raise OverlapConflictError(
            "You cannot create a group with a stay period that overlaps an existing active membership."
        )

    # 3. Resolve optional stay location
    if not stay_location and stay_location_id:
        try:
            stay_location = AccommodationStayLocation.objects.get(id=stay_location_id)
        except AccommodationStayLocation.DoesNotExist:
            stay_location = None

    if stay_location:
        stay_location.usage_count += 1
        stay_location.save(update_fields=['usage_count', 'updated_at'])

    # 4. Create the group
    group = AccommodationGroup.objects.create(
        title=title.strip(),
        destination_city=destination_city.strip(),
        location_within_city=location_within_city.strip(),
        stay_location=stay_location,
        lodging_name=lodging_name.strip(),
        maps_url=maps_url.strip(),
        description=description.strip(),
        initiated_by=creator,
        current_owner=creator,
        member_limit=member_limit if (member_limit and member_limit >= 1) else None,
        admission_mode=admission_mode,
        recruitment_status=RecruitmentStatus.OPEN,
        group_status=GroupStatus.ACTIVE,
        estimated_cost_amount=estimated_cost_amount,
        estimated_cost_basis=estimated_cost_basis,
        currency_code=currency_code,
        cost_note=cost_note.strip(),
    )

    # 5. Create creator's active membership episode
    AccommodationMembership.objects.create(
        group=group,
        user=creator,
        planned_stay=creator_planned_stay,
        status=MembershipStatus.ACTIVE,
        joined_at=timezone.now(),
    )

    # 6. Auto-rescind creator's other pending/waitlisted applications that overlap this stay
    AccommodationJoinApplication.objects.filter(
        applicant=creator,
        status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED],
        requested_stay__overlap=creator_planned_stay,
    ).update(
        status=ApplicationStatus.AUTO_RESCINDED,
        resolution_code='OVERLAPPING_MEMBERSHIP_ACCEPTED',
        resolved_at=timezone.now(),
    )

    # 7. Audit event
    enrollment = getattr(creator, 'enrollment_number', str(creator.id))
    log_audit_event(
        event_type='ACCOMMODATE_GROUP_CREATED',
        actor_enrollment=enrollment,
        description=f"Formed group '{group.title}' for {group.destination_city} (ID: {group.id})",
    )

    return group


@transaction.atomic
def update_group_details(
    group_id,
    actor: User,
    title: Optional[str] = None,
    destination_city: Optional[str] = None,
    location_within_city: Optional[str] = None,
    lodging_name: Optional[str] = None,
    maps_url: Optional[str] = None,
    description: Optional[str] = None,
    estimated_cost_amount: Optional[Decimal] = None,
    estimated_cost_basis: Optional[str] = None,
    cost_note: Optional[str] = None,
) -> AccommodationGroup:
    """Update descriptive metadata of a group. Restricted to current owner."""
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can modify group settings.")

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot modify an ended group.")

    if title is not None:
        group.title = title.strip()
    if destination_city is not None:
        group.destination_city = destination_city.strip()
    if location_within_city is not None:
        group.location_within_city = location_within_city.strip()
    if lodging_name is not None:
        group.lodging_name = lodging_name.strip()
    if maps_url is not None:
        group.maps_url = maps_url.strip()
    if description is not None:
        group.description = description.strip()
    if estimated_cost_amount is not None:
        group.estimated_cost_amount = estimated_cost_amount
    if estimated_cost_basis is not None:
        group.estimated_cost_basis = estimated_cost_basis
    if cost_note is not None:
        group.cost_note = cost_note.strip()

    group.version += 1
    group.save()
    return group


@transaction.atomic
def update_member_limit(group_id, actor: User, new_limit: Optional[int]) -> AccommodationGroup:
    """Update or remove the group's active member limit.
    Reducing below current active count blocks new admissions without ejecting anyone."""
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can change the member limit.")

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot change limit on an ended group.")

    if new_limit is not None and new_limit < 1:
        raise ValueError("Member limit must be at least 1 or None (unlimited).")

    group.member_limit = new_limit
    group.version += 1
    group.save(update_fields=['member_limit', 'version', 'updated_at'])

    if group.admission_mode == AdmissionMode.AUTO and group.recruitment_status == RecruitmentStatus.OPEN:
        from accommodate.services.admission_service import process_auto_queue
        process_auto_queue(group.id)

    enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    log_audit_event(
        event_type='ACCOMMODATE_LIMIT_MODIFIED',
        actor_enrollment=enrollment,
        description=f"Updated member limit to {new_limit or 'Unlimited'} for group {group.id}",
    )
    return group


@transaction.atomic
def set_admission_mode(group_id, actor: User, new_mode: str) -> AccommodationGroup:
    """Toggle admission mode between MANUAL and AUTO."""
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can change the admission mode.")

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot change admission mode on an ended group.")

    if new_mode not in (AdmissionMode.MANUAL, AdmissionMode.AUTO):
        raise ValueError(f"Invalid admission mode: {new_mode}")

    if group.admission_mode == new_mode:
        return group

    old_mode = group.admission_mode
    group.admission_mode = new_mode
    group.version += 1
    group.save(update_fields=['admission_mode', 'version', 'updated_at'])

    if new_mode == AdmissionMode.AUTO:
        # Convert pending requests to waitlist and process queue
        AccommodationJoinApplication.objects.filter(
            group=group,
            status=ApplicationStatus.PENDING
        ).update(status=ApplicationStatus.WAITLISTED)
        from accommodate.services.admission_service import process_auto_queue
        process_auto_queue(group.id)
    else:
        # Convert waitlisted requests back to pending and increment objection round
        from django.db.models import F
        AccommodationJoinApplication.objects.filter(
            group=group,
            status=ApplicationStatus.WAITLISTED
        ).update(
            status=ApplicationStatus.PENDING,
            objection_round=F('objection_round') + 1,
        )

    enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    log_audit_event(
        event_type='ACCOMMODATE_MODE_TOGGLED',
        actor_enrollment=enrollment,
        description=f"Toggled admission mode to {new_mode} for group {group.id}",
    )
    return group


@transaction.atomic
def toggle_recruitment(group_id, actor: User, status: str) -> AccommodationGroup:
    """Open or close group recruitment. Closing expires unresolved applications."""
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can toggle recruitment.")

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot alter recruitment on an ended group.")

    if status not in (RecruitmentStatus.OPEN, RecruitmentStatus.CLOSED):
        raise ValueError(f"Invalid recruitment status: {status}")

    if status == RecruitmentStatus.OPEN:
        # Reopening requires an active member to be owner
        if not group.current_owner:
            raise GroupStateError("Cannot open recruitment without an active owner.")
        is_owner_active = group.memberships.filter(
            user=group.current_owner,
            status=MembershipStatus.ACTIVE
        ).exists()
        if not is_owner_active:
            raise GroupStateError("Current owner is not an active occupant of the group.")

    group.recruitment_status = status
    group.version += 1
    group.save(update_fields=['recruitment_status', 'version', 'updated_at'])

    # If closed, bulk expire pending and waitlisted requests
    if status == RecruitmentStatus.CLOSED:
        AccommodationJoinApplication.objects.filter(
            group=group,
            status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED]
        ).update(
            status=ApplicationStatus.EXPIRED,
            resolution_code='GROUP_CLOSED',
            resolved_at=timezone.now(),
        )

    return group
