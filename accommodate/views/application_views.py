from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from psycopg.types.range import Range

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
)
from accommodate.selectors.application_selectors import get_user_applications
from accommodate.services.admission_service import approve_manual
from accommodate.services.application_service import (
    reject_application,
    submit_application,
    withdraw_application,
)
from accommodate.services.objection_service import record_objection, withdraw_objection


@login_required
@require_POST
def apply_view(request: HttpRequest, group_id) -> HttpResponse:
    """Submit a join application for an accommodation group."""
    group = get_object_or_404(AccommodationGroup, id=group_id)

    start_date_str = request.POST.get('start_date', '').strip()
    end_date_str = request.POST.get('end_date', '').strip()
    message = request.POST.get('message', '').strip()

    if not start_date_str or not end_date_str:
        messages.error(request, "Please declared your intended stay start and end dates.")
        return redirect('accommodate:group_detail', group_id=group.id)

    try:
        start_d = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        if end_d <= start_d:
            messages.error(request, "Stay end date must be after the start date.")
            return redirect('accommodate:group_detail', group_id=group.id)
        requested_stay = Range(start_d, end_d, bounds='[]')
    except ValueError:
        messages.error(request, "Invalid date format. Please use YYYY-MM-DD.")
        return redirect('accommodate:group_detail', group_id=group.id)

    try:
        app = submit_application(
            group_id=group.id,
            applicant=request.user,
            requested_stay=requested_stay,
            message=message,
        )
        if app.status == 'APPROVED':
            messages.success(request, f"Welcome to '{group.title}'! Your admission was confirmed automatically.")
        elif app.status == 'WAITLISTED':
            messages.info(request, f"You are currently waitlisted for '{group.title}'. You will be admitted automatically when capacity opens.")
        else:
            messages.success(request, f"Your join application for '{group.title}' has been submitted for peer review.")
    except OverlapConflictError as e:
        messages.error(request, str(e))
    except (GroupStateError, ApplicationStateError, PermissionDeniedError, Exception) as e:
        messages.error(request, f"Application could not be submitted: {e}")

    return redirect('accommodate:group_detail', group_id=group.id)


@login_required
@require_POST
def withdraw_application_view(request: HttpRequest, application_id) -> HttpResponse:
    """Applicant withdraws their pending application."""
    try:
        app = withdraw_application(application_id, request.user)
        messages.success(request, "Your application has been withdrawn.")
        return redirect('accommodate:group_detail', group_id=app.group_id)
    except Exception as e:
        messages.error(request, f"Withdrawal failed: {e}")
        return redirect('accommodate:my_applications')


@login_required
@require_POST
def approve_application_view(request: HttpRequest, application_id) -> HttpResponse:
    """Group owner approves a candidate in a MANUAL admission group."""
    app = get_object_or_404(AccommodationJoinApplication, id=application_id)
    group_id = app.group_id

    try:
        membership = approve_manual(app.id, request.user)
        applicant_name = app.applicant.display_name
        messages.success(request, f"{applicant_name} has been admitted to the group.")
    except CapacityRequiredError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Admission approval failed: {e}")

    return redirect('accommodate:group_manage', group_id=group_id)


@login_required
@require_POST
def reject_application_view(request: HttpRequest, application_id) -> HttpResponse:
    """Group owner rejects an application."""
    app = get_object_or_404(AccommodationJoinApplication, id=application_id)
    group_id = app.group_id
    reason = request.POST.get('reason', '').strip()

    try:
        reject_application(app.id, request.user, reason)
        messages.success(request, f"Application from {app.applicant.display_name} has been rejected.")
    except Exception as e:
        messages.error(request, f"Rejection failed: {e}")

    return redirect('accommodate:group_manage', group_id=group_id)


@login_required
@require_POST
def object_application_view(request: HttpRequest, application_id) -> HttpResponse:
    """Active group peer raises advisory objection on a pending application."""
    app = get_object_or_404(AccommodationJoinApplication, id=application_id)
    reason = request.POST.get('reason', '').strip()

    try:
        record_objection(
            application_id=app.id,
            objector_user=request.user,
            reason=reason,
        )
        messages.warning(request, "Your peer objection has been recorded for the group owner's review.")
    except Exception as e:
        messages.error(request, f"Objection could not be recorded: {e}")

    return redirect('accommodate:group_detail', group_id=app.group_id)


@login_required
@require_POST
def withdraw_objection_view(request: HttpRequest, objection_id) -> HttpResponse:
    """Peer withdraws previously filed objection."""
    try:
        obj = withdraw_objection(objection_id, request.user)
        messages.success(request, "Your objection has been withdrawn.")
        return redirect('accommodate:group_detail', group_id=obj.application.group_id)
    except Exception as e:
        messages.error(request, f"Withdrawal failed: {e}")
        return redirect('accommodate:group_list')


@login_required
def my_applications_view(request: HttpRequest) -> HttpResponse:
    """View all outgoing join applications submitted by the logged-in student."""
    applications = get_user_applications(request.user)
    return render(request, 'accommodate/my_applications.html', {
        'applications': applications,
    })
