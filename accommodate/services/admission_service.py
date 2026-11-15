from django.db import transaction
from django.utils import timezone

from accounts.models import User, log_audit_event
from accommodate.exceptions import (
    ApplicationStateError,
    CapacityRequiredError,
    GroupStateError,
    OverlapConflictError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AdmissionMode,
    ApplicationStatus,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)


@transaction.atomic
def approve_manual(application_id, actor: User) -> AccommodationMembership:
    """Manual admission workflow for group owner.
    
    Adheres strictly to the lock discipline:
    Group Row Lock -> Applicant User Row Lock -> Application Row Lock.
    """
    # 1. Unlocked read of application to get keys
    app_pre = AccommodationJoinApplication.objects.values('id', 'group_id', 'applicant_id').get(id=application_id)
    group_id = app_pre['group_id']
    applicant_id = app_pre['applicant_id']

    # 2. Lock target group row (serializes group capacity & admissions)
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    # Validate owner authorization
    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can approve applications.")

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot approve applications for an ended group.")

    if group.recruitment_status != RecruitmentStatus.OPEN:
        raise GroupStateError("Cannot approve applications while recruitment is closed.")

    # 3. Check group member limit capacity under group lock
    active_count = group.memberships.filter(status=MembershipStatus.ACTIVE).count()
    if group.member_limit is not None and active_count >= group.member_limit:
        raise CapacityRequiredError(
            f"Group is full ({active_count}/{group.member_limit} members). "
            "Please increase or remove the member limit before approving."
        )

    # 4. Lock applicant user row (serializes all cross-group admissions for this student)
    applicant = User.objects.select_for_update().get(id=applicant_id)

    # 5. Lock and re-read the application row
    application = AccommodationJoinApplication.objects.select_for_update().get(id=application_id)
    if application.status != ApplicationStatus.PENDING:
        raise ApplicationStateError(
            f"Cannot approve application with status '{application.get_status_display()}'."
        )

    # 6. Verify applicant has no overlapping active membership in any group
    has_conflict = AccommodationMembership.objects.filter(
        user=applicant,
        status=MembershipStatus.ACTIVE,
        planned_stay__overlap=application.requested_stay
    ).exists()
    if has_conflict:
        application.status = ApplicationStatus.AUTO_RESCINDED
        application.resolution_code = 'OVERLAPPING_MEMBERSHIP_CONFLICT'
        application.resolved_at = timezone.now()
        application.save(update_fields=['status', 'resolution_code', 'resolved_at', 'updated_at'])
        raise OverlapConflictError(
            "Applicant has acquired a confirmed stay in another group that overlaps this requested period."
        )

    # 7. Check if there were advisory objections in this round
    had_objections = application.objections.filter(
        objection_round=application.objection_round,
        withdrawn_at__isnull=True
    ).exists()

    # 8. Create membership
    membership = AccommodationMembership.objects.create(
        group=group,
        user=applicant,
        planned_stay=application.requested_stay,
        status=MembershipStatus.ACTIVE,
        joined_at=timezone.now(),
    )

    # 9. Update application record
    application.status = ApplicationStatus.APPROVED
    application.membership = membership
    application.resolved_at = timezone.now()
    application.resolved_by = actor
    application.resolution_code = 'APPROVED_WITH_OBJECTIONS' if had_objections else 'APPROVED'
    application.save(update_fields=['status', 'membership', 'resolved_at', 'resolved_by', 'resolution_code', 'updated_at'])

    # 10. Rescission cascade: Auto-rescind applicant's other pending/waitlisted applications that overlap this stay
    AccommodationJoinApplication.objects.filter(
        applicant=applicant,
        status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED],
        requested_stay__overlap=application.requested_stay,
    ).exclude(id=application.id).update(
        status=ApplicationStatus.AUTO_RESCINDED,
        resolution_code='OVERLAPPING_MEMBERSHIP_ACCEPTED',
        resolved_at=timezone.now(),
    )

    # 11. Audit event
    actor_enrollment = getattr(actor, 'enrollment_number', str(actor.id))
    applicant_enrollment = getattr(applicant, 'enrollment_number', str(applicant.id))
    log_audit_event(
        event_type='ACCOMMODATE_MEMBER_ADMITTED',
        actor_enrollment=actor_enrollment,
        description=(
            f"Admitted student {applicant_enrollment} to group '{group.title}' "
            f"(objections_present={had_objections})"
        ),
    )

    return membership


@transaction.atomic
def process_auto_queue(group_id) -> int:
    """Process FIFO queue of waitlisted applications for an AUTO group.
    
    Adheres strictly to the lock discipline:
    Group Lock -> Candidate User Lock -> Application Lock.
    Returns the number of candidates admitted.
    """
    group = AccommodationGroup.objects.select_for_update().get(id=group_id)

    if group.group_status != GroupStatus.ACTIVE or group.recruitment_status != RecruitmentStatus.OPEN:
        return 0

    if group.admission_mode != AdmissionMode.AUTO:
        return 0

    admitted_count = 0

    while True:
        # Check current capacity
        active_count = group.memberships.filter(status=MembershipStatus.ACTIVE).count()
        if group.member_limit is not None and active_count >= group.member_limit:
            break

        # Select oldest waitlisted candidate without row lock first (to prevent lock inversion)
        candidate = AccommodationJoinApplication.objects.filter(
            group=group,
            status=ApplicationStatus.WAITLISTED
        ).order_by('created_at', 'id').first()

        if not candidate:
            break

        # 1. Lock candidate applicant user row
        applicant = User.objects.select_for_update().get(id=candidate.applicant_id)

        # 2. Lock candidate application row
        application = AccommodationJoinApplication.objects.select_for_update().get(id=candidate.id)

        # Re-check status under lock
        if application.status != ApplicationStatus.WAITLISTED:
            continue

        # Check if candidate acquired overlapping membership elsewhere while in queue
        has_conflict = AccommodationMembership.objects.filter(
            user=applicant,
            status=MembershipStatus.ACTIVE,
            planned_stay__overlap=application.requested_stay
        ).exists()

        if has_conflict:
            # Auto-rescind conflicting application and move to next
            application.status = ApplicationStatus.AUTO_RESCINDED
            application.resolution_code = 'OVERLAPPING_MEMBERSHIP_CONFLICT'
            application.resolved_at = timezone.now()
            application.save(update_fields=['status', 'resolution_code', 'resolved_at', 'updated_at'])
            continue

        # Admit candidate
        membership = AccommodationMembership.objects.create(
            group=group,
            user=applicant,
            planned_stay=application.requested_stay,
            status=MembershipStatus.ACTIVE,
            joined_at=timezone.now(),
        )

        application.status = ApplicationStatus.APPROVED
        application.membership = membership
        application.resolved_at = timezone.now()
        application.resolution_code = 'AUTO_ADMITTED'
        application.save(update_fields=['status', 'membership', 'resolved_at', 'resolution_code', 'updated_at'])

        # Cascade auto-rescind other overlapping pending/waitlisted applications
        AccommodationJoinApplication.objects.filter(
            applicant=applicant,
            status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED],
            requested_stay__overlap=application.requested_stay,
        ).exclude(id=application.id).update(
            status=ApplicationStatus.AUTO_RESCINDED,
            resolution_code='OVERLAPPING_MEMBERSHIP_ACCEPTED',
            resolved_at=timezone.now(),
        )

        admitted_count += 1

        enrollment = getattr(applicant, 'enrollment_number', str(applicant.id))
        log_audit_event(
            event_type='ACCOMMODATE_MEMBER_ADMITTED',
            actor_enrollment=enrollment,
            description=f"Auto-admitted from FIFO queue to group '{group.title}' (ID: {group.id})",
        )

    return admitted_count

