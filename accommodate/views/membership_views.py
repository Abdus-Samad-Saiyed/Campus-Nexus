from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from psycopg.types.range import Range

from accommodate.exceptions import (
    ApplicationStateError,
    GroupStateError,
    OverlapConflictError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationMembership,
    DepartureReason,
)
from accommodate.selectors.application_selectors import (
    get_user_applications,
    get_user_memberships,
)
from accommodate.services.membership_service import (
    leave_group,
    remove_member,
    update_planned_stay,
)


@login_required
def my_groups_view(request: HttpRequest) -> HttpResponse:
    """Consolidated hub of user's active groups, past stays, and sent applications."""
    data = get_user_memberships(request.user)
    applications = get_user_applications(request.user)

    context = {
        'active_memberships': data['active_memberships'],
        'past_memberships': data['past_memberships'],
        'applications': applications,
    }
    return render(request, 'accommodate/my_groups.html', context)


@login_required
@require_POST
def update_dates_view(request: HttpRequest, membership_id) -> HttpResponse:
    """Active occupant adjusts their declared stay period."""
    membership = get_object_or_404(AccommodationMembership, id=membership_id)
    group_id = membership.group_id

    start_date_str = request.POST.get('start_date', '').strip()
    end_date_str = request.POST.get('end_date', '').strip()

    if not start_date_str or not end_date_str:
        messages.error(request, "Please declared new stay start and end dates.")
        return redirect('accommodate:group_detail', group_id=group_id)

    try:
        start_d = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        if end_d <= start_d:
            messages.error(request, "Stay end date must be after the start date.")
            return redirect('accommodate:group_detail', group_id=group_id)
        new_stay = Range(start_d, end_d, bounds='[]')
    except ValueError:
        messages.error(request, "Invalid date format. Please use YYYY-MM-DD.")
        return redirect('accommodate:group_detail', group_id=group_id)

    try:
        update_planned_stay(membership.id, request.user, new_stay)
        messages.success(request, "Your declared stay dates have been updated successfully.")
    except OverlapConflictError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Could not update dates: {e}")

    return redirect('accommodate:group_detail', group_id=group_id)


@login_required
@require_POST
def leave_group_view(request: HttpRequest, membership_id) -> HttpResponse:
    """Voluntary exit from an accommodation group."""
    membership = get_object_or_404(AccommodationMembership, id=membership_id)
    group_id = membership.group_id

    try:
        leave_group(membership.id, request.user)
        messages.success(request, "You have departed from the group.")
        return redirect('accommodate:my_groups')
    except GroupStateError as e:
        messages.error(request, str(e))
        return redirect('accommodate:group_manage', group_id=group_id)
    except Exception as e:
        messages.error(request, f"Departure failed: {e}")
        return redirect('accommodate:group_detail', group_id=group_id)


@login_required
@require_POST
def remove_member_view(request: HttpRequest, membership_id) -> HttpResponse:
    """Group owner removes an occupant with a structured reason category."""
    membership = get_object_or_404(AccommodationMembership, id=membership_id)
    group_id = membership.group_id
    reason_code = request.POST.get('reason_code', '').strip()
    reason_text = request.POST.get('reason_text', '').strip()

    if reason_code not in DepartureReason.values:
        messages.error(request, "Please select a valid removal reason category.")
        return redirect('accommodate:group_manage', group_id=group_id)

    try:
        remove_member(
            membership_id=membership.id,
            actor=request.user,
            reason_code=reason_code,
            reason_text=reason_text,
        )
        messages.success(request, f"{membership.user.display_name} has been removed from the group.")
    except Exception as e:
        messages.error(request, f"Removal failed: {e}")

    return redirect('accommodate:group_manage', group_id=group_id)
