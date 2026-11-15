from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationStayLocation,
    AdmissionMode,
    ApplicationStatus,
    CostBasis,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)
from accommodate.selectors.application_selectors import get_group_inbox
from accommodate.selectors.group_selectors import (
    get_active_cities,
    get_group_detail,
    get_popular_stays,
    search_groups,
)


def accommodate_landing_view(request: HttpRequest) -> HttpResponse:
    """Dedicated landing and overview page for the ACCOMMODATE module."""
    active_groups_count = 0
    cities = []
    popular_stays = []
    user_stays_count = 0
    try:
        active_groups_count = AccommodationGroup.objects.filter(status=GroupStatus.ACTIVE).count()
        cities = get_active_cities()
        popular_stays = get_popular_stays(limit=4)
        if request.user.is_authenticated:
            user_stays_count = request.user.accommodation_memberships.count()
    except Exception:
        pass

    return render(request, 'accommodate/landing.html', {
        'active_groups_count': active_groups_count,
        'cities': cities,
        'popular_stays': popular_stays,
        'user_stays_count': user_stays_count,
    })


@login_required
def group_list_view(request: HttpRequest) -> HttpResponse:
    """Explore accommodation groups across destination cities with instant filtering."""
    city = request.GET.get('city', '').strip()
    location = request.GET.get('location', '').strip()
    q = request.GET.get('q', '').strip()
    has_slots_val = request.GET.get('has_slots', '')
    has_slots = True if has_slots_val in ['1', 'true', 'on'] else None
    mode = request.GET.get('mode', '').strip()
    admission_mode = mode if mode in [AdmissionMode.MANUAL, AdmissionMode.AUTO] else None

    max_cost = None
    cost_str = request.GET.get('max_cost', '').strip()
    if cost_str:
        try:
            max_cost = Decimal(cost_str)
        except (InvalidOperation, ValueError):
            max_cost = None

    page_number = int(request.GET.get('page', 1) or 1)
    limit = 12
    offset = (page_number - 1) * limit

    groups, total_count = search_groups(
        city=city or None,
        location=location or None,
        q=q or None,
        has_slots=has_slots,
        admission_mode=admission_mode,
        max_cost=max_cost,
        limit=limit,
        offset=offset,
    )

    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    has_next = page_number < total_pages
    has_prev = page_number > 1

    active_cities = get_active_cities()
    popular_stays = get_popular_stays(city=city if city else None, limit=8)

    context = {
        'groups': groups,
        'total_count': total_count,
        'page': page_number,
        'total_pages': total_pages,
        'has_next': has_next,
        'has_prev': has_prev,
        'next_page': page_number + 1,
        'prev_page': page_number - 1,
        'selected_city': city,
        'selected_location': location,
        'search_query': q,
        'has_slots': has_slots,
        'selected_mode': admission_mode,
        'selected_max_cost': cost_str,
        'active_cities': active_cities,
        'popular_stays': popular_stays,
        'google_maps_api_key': getattr(settings, 'GOOGLE_MAPS_API_KEY', ''),
    }

    if request.headers.get('HX-Request'):
        return render(request, 'accommodate/partials/group_results.html', context)

    return render(request, 'accommodate/group_list.html', context)


@login_required
def group_search_partial(request: HttpRequest) -> HttpResponse:
    """HTMX endpoint returning only the updated group results card deck."""
    return group_list_view(request)


@login_required
def group_detail_view(request: HttpRequest, group_id) -> HttpResponse:
    """Detailed profile of an accommodation group."""
    detail = get_group_detail(group_id, viewer=request.user)
    if not detail:
        raise Http404("Accommodation group not found.")

    group = detail['group']

    # If viewer is group owner, also supply incoming inbox overview
    inbox = None
    if detail['viewer_is_owner']:
        inbox = get_group_inbox(group.id, request.user)

    # Active member count and capacity
    active_members = detail['active_memberships']
    active_count = detail['active_members_count']
    capacity_left = (group.member_limit - active_count) if group.member_limit else None

    # Check if viewer has pending/waitlisted application
    viewer_app = detail['viewer_pending_application']

    context = {
        'group': group,
        'active_members': active_members,
        'active_count': active_count,
        'capacity_left': capacity_left,
        'viewer_is_owner': detail['viewer_is_owner'],
        'viewer_membership': detail['viewer_active_membership'],
        'viewer_application': viewer_app,
        'inbox': inbox,
        'google_maps_api_key': getattr(settings, 'GOOGLE_MAPS_API_KEY', ''),
    }

    return render(request, 'accommodate/group_detail.html', context)
