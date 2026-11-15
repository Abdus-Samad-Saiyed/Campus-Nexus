from django.db import transaction
from django.utils import timezone
from psycopg.types.range import Range

from accounts.models import User
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
    AdmissionMode,
    ApplicationStatus,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)


@transaction.atomic
def submit_application(
    group_id,
    applicant: User,
    requested_stay: Range,
    message: str = '',
) -> AccommodationJoinApplication:
    """Submit a join application for a group."""
    group = AccommodationGroup.objects.get(id=group_id)

    if group.group_status != GroupStatus.ACTIVE:
        raise GroupStateError("Cannot apply to an ended group.")

    if group.recruitment_status != RecruitmentStatus.OPEN:
        raise GroupStateError("Recruitment for this group is currently closed.")

    # 1. Applicant cannot be an existing active member of this group
    is_already_member = group.memberships.filter(
        user=applicant,
        status=MembershipStatus.ACTIVE
    ).exists()
    if is_already_member:
        raise ApplicationStateError("You are already an active member of this group.")

    # 2. Check if applicant has an active membership in ANY group with overlapping dates
    has_conflict = AccommodationMembership.objects.filter(
        user=applicant,
        status=MembershipStatus.ACTIVE,
        planned_stay__overlap=requested_stay
    ).exists()
    if has_conflict:
        raise OverlapConflictError(
            "You cannot apply for a stay that conflicts with an existing confirmed membership."
        )

    # 3. Check for existing unresolved application in this group
    unresolved_exists = AccommodationJoinApplication.objects.filter(
        group=group,
        applicant=applicant,
        status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED]
    ).exists()
    if unresolved_exists:
        raise ApplicationStateError("You already have an active application pending for this group.")

    # 4. In MANUAL mode, applications always enter PENDING state
    initial_status = ApplicationStatus.PENDING
    if group.admission_mode == AdmissionMode.AUTO:
        # In AUTO mode, check capacity under lock or mark WAITLISTED
        # Delegated to admission service in AUTO mode
        initial_status = ApplicationStatus.WAITLISTED

    application = AccommodationJoinApplication.objects.create(
        group=group,
        applicant=applicant,
        requested_stay=requested_stay,
        message=message.strip(),
        objection_round=1,
        status=initial_status,
    )

    if group.admission_mode == AdmissionMode.AUTO:
        from accommodate.services.admission_service import process_auto_queue
        process_auto_queue(group.id)
        application.refresh_from_db()

    return application


@transaction.atomic
def withdraw_application(application_id, applicant: User) -> AccommodationJoinApplication:
    """Withdraw own pending or waitlisted application."""
    application = AccommodationJoinApplication.objects.select_for_update().get(id=application_id)

    if application.applicant != applicant and not applicant.is_staff:
        raise PermissionDeniedError("You can only withdraw your own applications.")

    if application.status not in (ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED):
        raise ApplicationStateError(
            f"Cannot withdraw an application with status '{application.get_status_display()}'."
        )

    application.status = ApplicationStatus.WITHDRAWN
    application.resolved_at = timezone.now()
    application.save(update_fields=['status', 'resolved_at', 'updated_at'])
    return application


@transaction.atomic
def reject_application(application_id, actor: User, reason: str = '') -> AccommodationJoinApplication:
    """Owner rejects a pending MANUAL application."""
    application = AccommodationJoinApplication.objects.select_for_update().get(id=application_id)
    group = application.group

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDeniedError("Only the current group owner can reject applications.")

    if application.status != ApplicationStatus.PENDING:
        raise ApplicationStateError(
            f"Cannot reject an application with status '{application.get_status_display()}'."
        )

    application.status = ApplicationStatus.REJECTED
    application.resolved_at = timezone.now()
    application.resolved_by = actor
    application.resolution_code = reason.strip() or 'OWNER_REJECTED'
    application.save(update_fields=['status', 'resolved_at', 'resolved_by', 'resolution_code', 'updated_at'])
    return application
