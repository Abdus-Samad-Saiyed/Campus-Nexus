from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accommodate.exceptions import (
    ApplicationStateError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationApplicationObjection,
    AccommodationJoinApplication,
    AccommodationMembership,
    AdmissionMode,
    ApplicationStatus,
    MembershipStatus,
)


@transaction.atomic
def record_objection(
    application_id,
    objector_user: User,
    reason: str = '',
) -> AccommodationApplicationObjection:
    """Active occupant submits an advisory objection against a pending MANUAL application."""
    application = AccommodationJoinApplication.objects.select_for_update().get(id=application_id)
    group = application.group

    if application.status != ApplicationStatus.PENDING:
        raise ApplicationStateError("Can only object to pending applications.")

    if group.admission_mode != AdmissionMode.MANUAL:
        raise ApplicationStateError("Objections are only available in MANUAL admission mode.")

    # Objector must be an active member of the same group
    objector_mem = AccommodationMembership.objects.filter(
        group=group,
        user=objector_user,
        status=MembershipStatus.ACTIVE
    ).first()
    if not objector_mem:
        raise PermissionDeniedError("Only active group members may lodge advisory objections.")

    objection, _ = AccommodationApplicationObjection.objects.update_or_create(
        application=application,
        objector_membership=objector_mem,
        objection_round=application.objection_round,
        defaults={
            'reason': reason.strip(),
            'withdrawn_at': None,
        }
    )
    return objection


@transaction.atomic
def withdraw_objection(
    objection_id,
    objector_user: User,
) -> AccommodationApplicationObjection:
    """Withdraw a previously filed objection while the application remains pending."""
    objection = AccommodationApplicationObjection.objects.select_for_update().get(id=objection_id)

    if objection.objector_membership.user != objector_user and not objector_user.is_staff:
        raise PermissionDeniedError("You can only withdraw your own objection.")

    if objection.application.status != ApplicationStatus.PENDING:
        raise ApplicationStateError("Cannot withdraw objection on a resolved application.")

    objection.withdrawn_at = timezone.now()
    objection.save(update_fields=['withdrawn_at'])
    return objection
