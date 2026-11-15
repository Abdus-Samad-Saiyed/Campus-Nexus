from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
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
    AccommodationMembership,
    AccommodationStayLocation,
    AdmissionMode,
    CostBasis,
    GroupStatus,
    MembershipStatus,
    RecruitmentStatus,
)
from accommodate.selectors.application_selectors import get_group_inbox
from accommodate.selectors.group_selectors import get_popular_stays
from accommodate.services.group_service import (
    create_group,
    set_admission_mode,
    toggle_recruitment,
    update_group_details,
    update_member_limit,
)
from accommodate.services.ownership_service import claim_ownership, transfer_ownership


@login_required
def group_create_view(request: HttpRequest) -> HttpResponse:
    """Form to initiate a new student accommodation group."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        destination_city = request.POST.get('destination_city', '').strip()
        location_within_city = request.POST.get('location_within_city', '').strip()
        lodging_name = request.POST.get('lodging_name', '').strip()
        maps_url = request.POST.get('maps_url', '').strip()
        description = request.POST.get('description', '').strip()

        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()

        member_limit_str = request.POST.get('member_limit', '').strip()
        admission_mode = request.POST.get('admission_mode', AdmissionMode.MANUAL).strip()

        cost_str = request.POST.get('estimated_cost_amount', '').strip()
        cost_basis = request.POST.get('estimated_cost_basis', '').strip()
        cost_note = request.POST.get('cost_note', '').strip()

        stay_location_id = request.POST.get('stay_location_id', '').strip()
        google_place_id = request.POST.get('google_place_id', '').strip()
        formatted_address = request.POST.get('formatted_address', '').strip()
        lat_str = request.POST.get('latitude', '').strip()
        lng_str = request.POST.get('longitude', '').strip()

        errors = []
        if not title:
            errors.append("Please provide a group title.")
        if not destination_city:
            errors.append("Please specify the destination city.")
        if not location_within_city:
            errors.append("Please specify the location or neighborhood within the city.")
        if not start_date_str or not end_date_str:
            errors.append("Please declared your planned stay start and end dates.")

        stay_range = None
        if start_date_str and end_date_str:
            try:
                start_d = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                if end_d <= start_d:
                    errors.append("Stay end date must be after the start date.")
                else:
                    stay_range = Range(start_d, end_d, bounds='[]')
            except ValueError:
                errors.append("Invalid date format. Please use YYYY-MM-DD.")

        member_limit = None
        if member_limit_str:
            try:
                member_limit = int(member_limit_str)
                if member_limit < 1:
                    errors.append("Member limit must be at least 1.")
            except ValueError:
                errors.append("Invalid member limit number.")

        estimated_cost = None
        if cost_str:
            try:
                estimated_cost = Decimal(cost_str)
                if estimated_cost < 0:
                    errors.append("Estimated cost cannot be negative.")
            except (InvalidOperation, ValueError):
                errors.append("Invalid estimated cost amount.")

        if errors:
            for err in errors:
                messages.error(request, err)
            popular_stays = get_popular_stays(limit=14)
            return render(request, 'accommodate/group_create.html', {
                'popular_stays': popular_stays,
                'google_maps_api_key': getattr(settings, 'GOOGLE_MAPS_API_KEY', ''),
                'form_data': request.POST,
            })

        # Resolve or create stay location if Google Maps address / place ID was supplied
        stay_location = None
        if stay_location_id:
            try:
                stay_location = AccommodationStayLocation.objects.get(id=stay_location_id)
            except AccommodationStayLocation.DoesNotExist:
                stay_location = None

        if not stay_location and google_place_id:
            lat = Decimal(lat_str) if lat_str else None
            lng = Decimal(lng_str) if lng_str else None
            stay_location, _ = AccommodationStayLocation.objects.get_or_create(
                google_place_id=google_place_id,
                defaults={
                    'name': lodging_name or location_within_city,
                    'city': destination_city,
                    'location_area': location_within_city,
                    'formatted_address': formatted_address or lodging_name,
                    'latitude': lat,
                    'longitude': lng,
                }
            )

        try:
            group = create_group(
                creator=request.user,
                title=title,
                destination_city=destination_city,
                location_within_city=location_within_city,
                creator_planned_stay=stay_range,
                stay_location=stay_location,
                lodging_name=lodging_name,
                maps_url=maps_url,
                description=description,
                member_limit=member_limit,
                admission_mode=admission_mode,
                estimated_cost_amount=estimated_cost,
                estimated_cost_basis=cost_basis or None,
                cost_note=cost_note,
            )
            messages.success(request, f"Group '{group.title}' created successfully!")
            return redirect('accommodate:group_detail', group_id=group.id)
        except OverlapConflictError as e:
            messages.error(request, str(e))
        except (GroupStateError, PermissionDeniedError, Exception) as e:
            messages.error(request, f"Failed to create group: {e}")

    popular_stays = get_popular_stays(limit=14)
    return render(request, 'accommodate/group_create.html', {
        'popular_stays': popular_stays,
        'google_maps_api_key': getattr(settings, 'GOOGLE_MAPS_API_KEY', ''),
    })


@login_required
def group_manage_view(request: HttpRequest, group_id) -> HttpResponse:
    """Owner's management console for admissions, member roster, and settings."""
    group = get_object_or_404(AccommodationGroup, id=group_id)

    if group.current_owner != request.user and not request.user.is_staff:
        messages.error(request, "Only the group owner can access the management console.")
        return redirect('accommodate:group_detail', group_id=group.id)

    inbox = get_group_inbox(group.id, request.user)
    active_memberships = list(
        group.memberships.filter(status=MembershipStatus.ACTIVE).select_related('user')
    )

    context = {
        'group': group,
        'inbox': inbox,
        'active_memberships': active_memberships,
        'active_members_count': len(active_memberships),
        'capacity_left': (group.member_limit - len(active_memberships)) if group.member_limit else None,
        'google_maps_api_key': getattr(settings, 'GOOGLE_MAPS_API_KEY', ''),
    }
    return render(request, 'accommodate/group_manage.html', context)


@login_required
def group_edit_view(request: HttpRequest, group_id) -> HttpResponse:
    """Edit group descriptive metadata, admission mode, and member capacity."""
    group = get_object_or_404(AccommodationGroup, id=group_id)

    if group.current_owner != request.user and not request.user.is_staff:
        messages.error(request, "Only the group owner can modify group settings.")
        return redirect('accommodate:group_detail', group_id=group.id)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        destination_city = request.POST.get('destination_city', '').strip()
        location_within_city = request.POST.get('location_within_city', '').strip()
        lodging_name = request.POST.get('lodging_name', '').strip()
        maps_url = request.POST.get('maps_url', '').strip()
        description = request.POST.get('description', '').strip()

        member_limit_str = request.POST.get('member_limit', '').strip()
        admission_mode = request.POST.get('admission_mode', '').strip()

        cost_str = request.POST.get('estimated_cost_amount', '').strip()
        cost_basis = request.POST.get('estimated_cost_basis', '').strip()
        cost_note = request.POST.get('cost_note', '').strip()

        member_limit = None
        if member_limit_str:
            try:
                member_limit = int(member_limit_str)
            except ValueError:
                messages.error(request, "Invalid member limit.")
                return redirect('accommodate:group_edit', group_id=group.id)

        estimated_cost = None
        if cost_str:
            try:
                estimated_cost = Decimal(cost_str)
            except (InvalidOperation, ValueError):
                messages.error(request, "Invalid estimated cost.")
                return redirect('accommodate:group_edit', group_id=group.id)

        try:
            update_group_details(
                group_id=group.id,
                actor=request.user,
                title=title or None,
                destination_city=destination_city or None,
                location_within_city=location_within_city or None,
                lodging_name=lodging_name or None,
                maps_url=maps_url or None,
                description=description or None,
                estimated_cost_amount=estimated_cost,
                estimated_cost_basis=cost_basis or None,
                cost_note=cost_note or None,
            )

            update_member_limit(group.id, request.user, member_limit)

            if admission_mode in [AdmissionMode.MANUAL, AdmissionMode.AUTO]:
                set_admission_mode(group.id, request.user, admission_mode)

            messages.success(request, "Group settings updated successfully.")
            return redirect('accommodate:group_manage', group_id=group.id)
        except Exception as e:
            messages.error(request, f"Update failed: {e}")

    return render(request, 'accommodate/group_edit.html', {'group': group})


@login_required
@require_POST
def group_toggle_recruitment_view(request: HttpRequest, group_id) -> HttpResponse:
    """Toggle recruitment status between OPEN and CLOSED."""
    group = get_object_or_404(AccommodationGroup, id=group_id)
    new_status = request.POST.get('status', '').strip()

    try:
        toggle_recruitment(group.id, request.user, new_status)
        messages.success(request, f"Recruitment status changed to {new_status}.")
    except Exception as e:
        messages.error(request, f"Unable to toggle recruitment: {e}")

    return redirect('accommodate:group_manage', group_id=group.id)


@login_required
@require_POST
def group_transfer_ownership_view(request: HttpRequest, group_id) -> HttpResponse:
    """Transfer owner role to another active member."""
    group = get_object_or_404(AccommodationGroup, id=group_id)
    new_owner_id = request.POST.get('new_owner_id', '').strip()

    try:
        new_owner = User.objects.get(id=new_owner_id)
        transfer_ownership(group.id, request.user, new_owner)
        messages.success(request, f"Group ownership transferred to {new_owner.display_name}.")
    except Exception as e:
        messages.error(request, f"Transfer failed: {e}")

    return redirect('accommodate:group_detail', group_id=group.id)


@login_required
@require_POST
def group_claim_ownership_view(request: HttpRequest, group_id) -> HttpResponse:
    """Claim abandoned ownership if current owner departed."""
    group = get_object_or_404(AccommodationGroup, id=group_id)

    try:
        claim_ownership(group.id, request.user)
        messages.success(request, "You have successfully claimed ownership of this group.")
    except Exception as e:
        messages.error(request, f"Claim failed: {e}")

    return redirect('accommodate:group_detail', group_id=group.id)
