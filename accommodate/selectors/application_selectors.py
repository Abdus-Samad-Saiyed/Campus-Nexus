from typing import Any, Dict, List
from django.core.exceptions import PermissionDenied

from accounts.models import User
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    ApplicationStatus,
    MembershipStatus,
)


def get_group_inbox(group_id, actor: User) -> Dict[str, Any]:
    """Retrieve application inbox for a group owner."""
    group = AccommodationGroup.objects.get(id=group_id)

    if group.current_owner != actor and not actor.is_staff:
        raise PermissionDenied("Only the group owner can view the application inbox.")

    pending_apps = list(
        AccommodationJoinApplication.objects.filter(
            group=group,
            status=ApplicationStatus.PENDING
        ).select_related('applicant').prefetch_related(
            'objections__objector_membership__user'
        ).order_by('created_at')
    )

    waitlisted_apps = list(
        AccommodationJoinApplication.objects.filter(
            group=group,
            status=ApplicationStatus.WAITLISTED
        ).select_related('applicant').order_by('created_at')
    )

    past_apps = list(
        AccommodationJoinApplication.objects.filter(
            group=group
        ).exclude(
            status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED]
        ).select_related('applicant').order_by('-resolved_at')[:20]
    )

    return {
        'group': group,
        'pending_applications': pending_apps,
        'waitlisted_applications': waitlisted_apps,
        'past_applications': past_apps,
    }


def get_user_applications(user: User) -> List[AccommodationJoinApplication]:
    """Retrieve all outgoing applications submitted by a student."""
    return list(
        AccommodationJoinApplication.objects.filter(
            applicant=user
        ).select_related('group').order_by('-created_at')
    )


def get_user_memberships(user: User) -> Dict[str, List[AccommodationMembership]]:
    """Retrieve active and historical group memberships for a student."""
    memberships = AccommodationMembership.objects.filter(
        user=user
    ).select_related('group__current_owner', 'group__stay_location').order_by('-joined_at')

    active = []
    past = []
    for m in memberships:
        if m.status == MembershipStatus.ACTIVE:
            active.append(m)
        else:
            past.append(m)

    return {
        'active_memberships': active,
        'past_memberships': past,
    }
