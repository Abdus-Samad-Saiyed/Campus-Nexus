from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.db.models import Count, F, Q
from django.contrib.postgres.search import TrigramSimilarity

from accounts.models import User
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationStayLocation,
    ApplicationStatus,
    CostBasis,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)


def search_groups(
    city: Optional[str] = None,
    location: Optional[str] = None,
    q: Optional[str] = None,
    has_slots: Optional[bool] = None,
    admission_mode: Optional[str] = None,
    max_cost: Optional[Decimal] = None,
    limit: int = 20,
    offset: int = 0,
):
    """Query and rank active recruiting groups based on multi-parameter filters and search."""
    qs = AccommodationGroup.objects.filter(
        group_status=GroupStatus.ACTIVE,
        recruitment_status=RecruitmentStatus.OPEN,
    ).select_related(
        'initiated_by',
        'current_owner',
        'stay_location'
    ).prefetch_related(
        'memberships__user'
    ).annotate(
        active_count=Count('memberships', filter=Q(memberships__status=MembershipStatus.ACTIVE))
    )

    if city and city.strip():
        qs = qs.filter(destination_city_normalized=city.strip().lower())

    if location and location.strip():
        loc_norm = location.strip().lower()
        qs = qs.filter(
            Q(location_within_city_normalized__icontains=loc_norm) |
            Q(lodging_name_normalized__icontains=loc_norm)
        )

    if q and q.strip():
        query_text = q.strip().lower()
        qs = qs.filter(
            Q(title__icontains=query_text) |
            Q(destination_city_normalized__icontains=query_text) |
            Q(location_within_city_normalized__icontains=query_text) |
            Q(lodging_name_normalized__icontains=query_text) |
            Q(description__icontains=query_text)
        )

    if has_slots:
        qs = qs.filter(Q(member_limit__isnull=True) | Q(active_count__lt=F('member_limit')))

    if admission_mode:
        qs = qs.filter(admission_mode=admission_mode)

    if max_cost is not None:
        qs = qs.filter(
            estimated_cost_basis=CostBasis.PER_PERSON_MONTH,
            estimated_cost_amount__lte=max_cost
        )

    total_count = qs.count()
    groups = list(qs.order_by('-created_at')[offset:offset + limit])
    return groups, total_count


def get_group_detail(group_id, viewer: Optional[User] = None) -> Optional[Dict[str, Any]]:
    """Retrieve detailed representation of a group with annotated viewer relationship."""
    try:
        group = AccommodationGroup.objects.select_related(
            'initiated_by',
            'current_owner',
            'stay_location'
        ).prefetch_related(
            'memberships__user'
        ).get(id=group_id)
    except AccommodationGroup.DoesNotExist:
        return None

    active_memberships = list(
        group.memberships.filter(status=MembershipStatus.ACTIVE).select_related('user')
    )

    viewer_is_owner = False
    viewer_active_membership = None
    viewer_pending_application = None

    if viewer and viewer.is_authenticated:
        viewer_is_owner = (group.current_owner_id == viewer.id)
        for m in active_memberships:
            if m.user_id == viewer.id:
                viewer_active_membership = m
                break
        if not viewer_active_membership:
            viewer_pending_application = AccommodationJoinApplication.objects.filter(
                group=group,
                applicant=viewer,
                status__in=[ApplicationStatus.PENDING, ApplicationStatus.WAITLISTED]
            ).first()

    return {
        'group': group,
        'active_memberships': active_memberships,
        'active_members_count': len(active_memberships),
        'has_available_capacity': (
            group.member_limit is None or len(active_memberships) < group.member_limit
        ),
        'viewer_is_owner': viewer_is_owner,
        'viewer_active_membership': viewer_active_membership,
        'viewer_pending_application': viewer_pending_application,
    }


def get_active_cities() -> List[Dict[str, Any]]:
    """Retrieve distinct destination cities with counts of active recruiting groups."""
    rows = AccommodationGroup.objects.filter(
        group_status=GroupStatus.ACTIVE,
        recruitment_status=RecruitmentStatus.OPEN
    ).values('destination_city').annotate(
        group_count=Count('id')
    ).order_by('-group_count', 'destination_city')

    return [{'city': r['destination_city'], 'count': r['group_count']} for r in rows]


def get_popular_stays(city: Optional[str] = None, limit: int = 10) -> List[AccommodationStayLocation]:
    """Retrieve top seeded and previously seen stay locations ordered by popularity."""
    qs = AccommodationStayLocation.objects.all()
    if city and city.strip():
        qs = qs.filter(city_normalized=city.strip().lower())
    return list(qs.order_by('-usage_count', 'name')[:limit])
