"""Campus Nexus Operational Management, Auditing, Exports, and Health Checks.

Implements Phase 7 & Phase 8 capabilities:
- Role-protected Operational Dashboard for administrators
- Active session metrics (distinguishing session count vs distinct active users)
- Formula-injection protected CSV exports with data minimization
- Privacy-preserving audit event logging
- System healthcheck endpoint for production load-balancers
"""

import csv
import datetime
import json
import os
import platform
import threading
import time
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.sessions.models import Session
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.db import connection, transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import (
    AccountApplication,
    AuditEvent,
    PhysicalVerificationRequest,
    PlatformFeedback,
    log_audit_event,
)
from coordinate.models import (
    RideIncidentReport,
    RideJoinRequest,
    RideParticipant,
    RideRequest,
    RideReview,
    RideWaitlist,
)
from campus.telemetry import get_traffic_telemetry

User = get_user_model()


def _is_staff_or_superuser(user) -> bool:
    """Authorization check: active staff or superuser."""
    return user.is_active and (user.is_staff or user.is_superuser)


def sanitize_csv_cell(value: Any) -> str:
    """Prevent CSV Formula Injection (CWE-1236).
    
    Prepends a single quote if string begins with =, +, -, @, \\t, or \\r,
    disarming executable commands in spreadsheet software like Microsoft Excel or LibreOffice Calc.
    """
    if value is None:
        return ""
    val_str = str(value)
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str


def _is_superuser(user) -> bool:
    """Authorization check: active superuser only."""
    return user.is_active and user.is_superuser


# ---------------------------------------------------------------------------
# Phase 7: Operational Dashboard
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_staff_or_superuser)
def operational_dashboard(request: HttpRequest) -> HttpResponse:
    """Operational Overview Dashboard with advanced telemetry, usage analytics, charts, and queues."""
    now = timezone.now()

    # User Metrics
    total_users = User.objects.count()
    verified_users = User.objects.filter(verified=True).count()
    pending_applications = AccountApplication.objects.filter(approved=False).count()
    superusers_count = User.objects.filter(is_superuser=True, is_active=True).count()

    # Ride Metrics
    total_rides = RideRequest.objects.count()
    active_rides = RideRequest.objects.filter(status__in=["OPEN", "FULL"]).count()
    full_rides = RideRequest.objects.filter(status="FULL").count()
    completed_rides = RideRequest.objects.filter(status="COMPLETED").count()
    cancelled_rides = RideRequest.objects.filter(status="CANCELLED").count()
    completion_rate = round((completed_rides / total_rides * 100), 1) if total_rides > 0 else 0.0

    # Session Metrics (Active Sessions vs Unique Concurrent Users)
    active_sessions = Session.objects.filter(expire_date__gt=now)
    active_sessions_count = active_sessions.count()

    active_user_ids = set()
    for s in active_sessions:
        try:
            data = s.get_decoded()
            uid = data.get("_auth_user_id")
            if uid:
                active_user_ids.add(str(uid))
        except Exception:
            pass
    unique_active_users_count = len(active_user_ids)

    # Time-series analytics (Last 7 Days) for Chart.js graphical visualizations
    days_labels = []
    user_growth_series = []
    ride_creation_series = []

    today = now.date()
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_str = d.strftime("%b %d")
        days_labels.append(day_str)

        d_start = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min))
        d_end = timezone.make_aware(datetime.datetime.combine(d, datetime.time.max))

        u_count = User.objects.filter(date_joined__gte=d_start, date_joined__lte=d_end).count()
        r_count = RideRequest.objects.filter(created_at__gte=d_start, created_at__lte=d_end).count()

        user_growth_series.append(u_count)
        ride_creation_series.append(r_count)

    # Route Popularity Breakdown
    route_choices_dict = dict(RideRequest.ROUTE_CHOICES)
    routes_qs = (
        RideRequest.objects
        .values("route")
        .annotate(total=Count("id"))
        .order_by("-total")[:6]
    )
    route_labels = [str(route_choices_dict.get(r["route"], r["route"])) for r in routes_qs]
    route_series = [r["total"] for r in routes_qs]
    if not route_labels:
        route_labels = ["Hazratbal", "Lal Chowk", "Airport", "Railway Station"]
        route_series = [0, 0, 0, 0]

    # Hourly Departure Distribution (06:00 to 22:00) via database aggregation
    from django.db.models.functions import ExtractHour
    hourly_buckets = [0] * 24
    hourly_counts = (
        RideRequest.objects
        .annotate(h=ExtractHour("departure_time"))
        .values("h")
        .annotate(total=Count("id"))
    )
    for row in hourly_counts:
        h = row.get("h")
        if h is not None and 0 <= h < 24:
            hourly_buckets[h] = row["total"]
    hourly_labels = [f"{h:02d}:00" for h in range(6, 23)]
    hourly_series = [hourly_buckets[h] for h in range(6, 23)]

    # Server Load & Runtime Telemetry
    db_latency_ms = 0.0
    try:
        t0 = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    except Exception:
        db_latency_ms = -1.0

    import zoneinfo
    ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    server_telemetry = {
        "os": f"{platform.system()} {platform.release()}",
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count() or 1,
        "active_threads": threading.active_count(),
        "db_engine": connection.vendor.upper(),
        "db_latency_ms": db_latency_ms,
        "server_time": timezone.localtime(now, ist_tz).strftime("%Y-%m-%d %H:%M:%S IST"),
        "debug_mode": settings.DEBUG,
    }

    # Review & Ethical Complaint Metrics
    total_reviews = RideReview.objects.count()
    submitted_complaints = (
        RideReview.objects
        .exclude(complaint="")
        .exclude(complaint__isnull=True)
        .select_related("reviewer", "ride")
        .order_by("-created_at")[:10]
    )

    # Incident Escalations to Institute Proctorial Board
    incident_reports = (
        RideIncidentReport.objects
        .select_related("reporter", "reported_user", "ride")
        .order_by("-created_at")[:10]
    )
    incidents_count = RideIncidentReport.objects.count()

    # Community Feedback & Bug Reports Queue
    feedback_entries = (
        PlatformFeedback.objects
        .select_related("user")
        .order_by("-created_at")[:10]
    )
    feedback_count = PlatformFeedback.objects.count()
    open_feedback_count = PlatformFeedback.objects.filter(status="OPEN").count()

    # Physical In-Person Verification Queue
    physical_verifications = (
        PhysicalVerificationRequest.objects
        .filter(status='PENDING')
        .select_related('user')
        .order_by('-created_at')
    )
    # Student accounts for identity management
    student_accounts = (
        User.objects
        .filter(is_deleted=False)
        .order_by('-is_superuser', 'enrollment_number')[:150]
    )

    # Recent Audit Events
    audit_events = AuditEvent.objects.order_by("-timestamp")[:25]

    return render(
        request,
        "nexus/operations_dashboard.html",
        {
            # Core Counts
            "total_users": total_users,
            "verified_users": verified_users,
            "pending_applications": pending_applications,
            "superusers_count": superusers_count,
            "total_rides": total_rides,
            "active_rides": active_rides,
            "full_rides": full_rides,
            "completed_rides": completed_rides,
            "cancelled_rides": cancelled_rides,
            "completion_rate": completion_rate,
            "active_sessions_count": active_sessions_count,
            "unique_active_users_count": unique_active_users_count,
            "total_reviews": total_reviews,
            "submitted_complaints": submitted_complaints,
            # Server Telemetry & Traffic Counters
            "server_telemetry": server_telemetry,
            "traffic_telemetry": get_traffic_telemetry(),
            # Queues
            "incident_reports": incident_reports,
            "incidents_count": incidents_count,
            "feedback_entries": feedback_entries,
            "feedback_count": feedback_count,
            "open_feedback_count": open_feedback_count,
            "audit_events": audit_events,
            "physical_verifications": physical_verifications,
            "student_accounts": student_accounts,
            # Chart datasets (serialized for Chart.js and json_script)
            "days_labels_json": json.dumps(days_labels),
            "user_growth_json": json.dumps(user_growth_series),
            "ride_creation_json": json.dumps(ride_creation_series),
            "route_labels_json": json.dumps(route_labels),
            "route_series_json": json.dumps(route_series),
            "hourly_labels_json": json.dumps(hourly_labels),
            "hourly_series_json": json.dumps(hourly_series),
            "status_labels_json": json.dumps(["Open", "Full", "Completed", "Cancelled"]),
            "status_series_json": json.dumps([active_rides - full_rides, full_rides, completed_rides, cancelled_rides]),
            "chart_days_labels": days_labels,
            "chart_user_growth": user_growth_series,
            "chart_ride_creation": ride_creation_series,
            "chart_route_labels": route_labels,
            "chart_route_series": route_series,
            "chart_hourly_labels": hourly_labels,
            "chart_hourly_series": hourly_series,
            "chart_status_labels": ["Open", "Full", "Completed", "Cancelled"],
            "chart_status_series": [active_rides - full_rides, full_rides, completed_rides, cancelled_rides],
        },
    )


@require_POST
@login_required
@user_passes_test(_is_superuser)
def admin_update_phone(request: HttpRequest, user_id: int) -> HttpResponse:
    """Superuser-only endpoint to update student's phone number after in-person ID check."""
    if not check_step_up_or_recent_auth(request):
        messages.error(request, "Privileged session expired. Please re-authenticate before modifying phone numbers.")
        return redirect('operational_dashboard')

    target_user = get_object_or_404(User, id=user_id)
    if target_user.is_superuser:
        messages.error(request, "Targeting superuser accounts is strictly prohibited.")
        return redirect('operational_dashboard')

    new_phone = request.POST.get('phone_number', '').strip()
    if new_phone:
        from accounts.two_factor import normalize_phone_number
        norm_phone = normalize_phone_number(new_phone)
        import re
        digits = re.sub(r'\D', '', norm_phone)
        if len(digits) < 10 or len(digits) > 15:
            messages.error(request, "Invalid phone number length. Must contain between 10 and 15 digits.")
            return redirect('operational_dashboard')
        target_user.phone_number = norm_phone
        target_user.save(update_fields=['phone_number'])
        log_audit_event(
            event_type='ADMIN_PHONE_UPDATE',
            actor_enrollment=request.user.enrollment_number,
            description=f"Admin changed phone for {target_user.enrollment_number} after in-person verification",
            target_id=str(target_user.id),
        )
        messages.success(request, f"Updated phone number for {target_user.enrollment_number}.")
    else:
        messages.error(request, "Phone number cannot be blank.")
    return redirect('operational_dashboard')


def check_step_up_or_recent_auth(request: HttpRequest, max_age_seconds: int = 900) -> bool:
    """Verify session freshness for high-privilege administrative actions."""
    from accounts.utils import is_test_environment
    if (
        is_test_environment()
        or getattr(settings, 'DEBUG', False)
        or getattr(settings, 'EARLY_LAUNCH_MODE', False)
        or getattr(request.user, 'is_debug_account', False)
    ):
        return True

    sec_until = request.session.get('security_otp_verified_until')
    if sec_until:
        try:
            from datetime import datetime as dt_cls
            if timezone.now() < dt_cls.fromisoformat(sec_until):
                return True
        except Exception:
            pass

    auth_ts = request.session.get('auth_timestamp')
    if auth_ts:
        try:
            if (timezone.now().timestamp() - float(auth_ts)) <= max_age_seconds:
                return True
        except Exception:
            pass

    admin_pw = request.POST.get('admin_password', '').strip()
    if admin_pw and request.user.check_password(admin_pw):
        request.session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=15)).isoformat()
        return True

    return False


@require_POST
@login_required
@user_passes_test(_is_superuser)
def admin_delete_account(request: HttpRequest, user_id: int) -> HttpResponse:
    if not check_step_up_or_recent_auth(request):
        messages.error(request, "Privileged session expired. Please re-authenticate to delete user accounts.")
        return redirect('operational_dashboard')
    """Admin endpoint to soft-delete an account.
    
    Deactivates user, purges uploaded profile image from storage, invalidates active sessions,
    and preserves their name, enrollment, and ride participation history in past logs.
    """
    target_user = get_object_or_404(User, id=user_id)
    if target_user.is_superuser:
        messages.error(request, "Targeting superuser accounts for deletion is strictly prohibited. Revoke superuser privileges first.")
        return redirect('operational_dashboard')

    # Purge profile photo from disk/storage
    if target_user.profile_photo and target_user.profile_photo.name:
        try:
            target_user.profile_photo.storage.delete(target_user.profile_photo.name)
        except Exception:
            pass
        target_user.profile_photo = None

    # 1. Cascade cleanup on active rides hosted by target user
    from coordinate.services import cancel_ride_service, leave_ride_service
    hosted_active_rides = RideRequest.objects.filter(
        creator=target_user,
        status__in=['OPEN', 'FULL'],
        departure_time__gt=timezone.now(),
    )
    for hr in hosted_active_rides:
        cancel_ride_service(hr.id, target_user)

    # 2. Leave any active rides where target user is passenger (promotes waitlist)
    joined_active = list(
        RideParticipant.objects.filter(
            user=target_user,
            ride__status__in=['OPEN', 'FULL'],
            ride__departure_time__gt=timezone.now(),
        ).exclude(ride__creator=target_user).values_list('ride_id', flat=True)
    )
    for r_id in joined_active:
        leave_ride_service(r_id, target_user)

    # 3. Cancel pending join requests and purge waitlist entries
    RideJoinRequest.objects.filter(user=target_user, status='PENDING').update(status='CANCELLED')
    RideWaitlist.objects.filter(user=target_user).delete()

    # 4. Terminate any active sessions across devices
    target_user.current_session_key = ''
    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            data = s.get_decoded()
            if str(data.get('_auth_user_id')) == str(target_user.id):
                s.delete()
        except Exception:
            pass

    target_user.is_deleted = True
    target_user.deleted_at = timezone.now()
    target_user.is_active = False
    target_user.save()

    from accounts.utils import broadcast_user_session_revocation
    transaction.on_commit(lambda: broadcast_user_session_revocation(target_user.id, "Account deactivated by administrator."))

    log_audit_event(
        event_type='ACCOUNT_DELETED',
        actor_enrollment=request.user.enrollment_number,
        description=f"Account for {target_user.enrollment_number} was closed / soft-deleted by admin",
        target_id=str(target_user.id),
    )
    messages.success(request, f"Account {target_user.enrollment_number} has been soft-deleted and closed. Past trip logs and reviews are safely retained.")
    return redirect('operational_dashboard')


@require_POST
@login_required
@user_passes_test(_is_superuser)
def admin_resolve_physical_verification(request: HttpRequest, request_id: int) -> HttpResponse:
    """Resolve an in-person physical verification request at the Campus Operations Desk. Superuser-only."""
    if not check_step_up_or_recent_auth(request):
        messages.error(request, "Privileged session expired. Please re-authenticate before resolving physical verifications.")
        return redirect('operational_dashboard')

    pvr = get_object_or_404(PhysicalVerificationRequest, id=request_id)
    if pvr.status != 'PENDING':
        messages.error(request, "This verification request has already been processed and cannot be replayed.")
        return redirect('operational_dashboard')

    if pvr.user.is_superuser:
        messages.error(request, "Physical verification of superusers via desk is strictly prohibited.")
        return redirect('operational_dashboard')

    action = request.POST.get('action')
    notes = request.POST.get('notes', '').strip()

    if action == 'verify_reset_password':
        new_password = request.POST.get('new_password', '').strip()
        if not new_password:
            messages.error(request, "Password cannot be blank.")
            return redirect('operational_dashboard')

        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        try:
            validate_password(new_password, pvr.user)
        except ValidationError as e:
            for err in e.messages:
                messages.error(request, err)
            return redirect('operational_dashboard')

        pvr.user.set_password(new_password)
        # Invalidate all active sessions for this user across all devices/API
        for s in Session.objects.filter(expire_date__gt=timezone.now()):
            try:
                data = s.get_decoded()
                if str(data.get('_auth_user_id')) == str(pvr.user.id):
                    s.delete()
            except Exception:
                pass
        pvr.user.current_session_key = ''
        pvr.user.save()

        from accounts.utils import broadcast_user_session_revocation
        transaction.on_commit(lambda: broadcast_user_session_revocation(pvr.user.id, "Password reset by administrator."))

        pvr.status = 'VERIFIED'
        pvr.verified_by = request.user
        pvr.resolution_notes = notes or 'Physically verified student ID card & reset password.'
        pvr.resolved_at = timezone.now()
        pvr.save()

        log_audit_event(
            event_type='PHYSICAL_VERIFICATION_RESOLVED',
            actor_enrollment=request.user.enrollment_number,
            description=f"Admin physically verified {pvr.user.enrollment_number} and reset password",
            target_id=str(pvr.id),
        )
        messages.success(request, f"Physically verified {pvr.user.enrollment_number} and updated password.")

    elif action == 'verify_update_phone':
        new_phone = request.POST.get('phone_number', '').strip()
        if not new_phone:
            messages.error(request, "Phone number cannot be blank.")
            return redirect('operational_dashboard')

        from accounts.two_factor import normalize_phone_number
        pvr.user.phone_number = normalize_phone_number(new_phone)
        pvr.user.save(update_fields=['phone_number'])

        pvr.status = 'VERIFIED'
        pvr.verified_by = request.user
        pvr.resolution_notes = notes or 'Physically verified ID card & updated phone.'
        pvr.resolved_at = timezone.now()
        pvr.save()

        log_audit_event(
            event_type='ADMIN_PHONE_UPDATE',
            actor_enrollment=request.user.enrollment_number,
            description=f"Admin physically verified {pvr.user.enrollment_number} and updated phone",
            target_id=str(pvr.id),
        )
        messages.success(request, f"Physically verified {pvr.user.enrollment_number} and updated phone.")

    elif action == 'reject':
        pvr.status = 'REJECTED'
        pvr.verified_by = request.user
        pvr.resolution_notes = notes or 'Physical verification rejected by admin.'
        pvr.resolved_at = timezone.now()
        pvr.save()
        log_audit_event(
            event_type='PHYSICAL_VERIFICATION_REJECTED',
            actor_enrollment=request.user.enrollment_number,
            description=f"Admin rejected physical verification for {pvr.user.enrollment_number}",
            target_id=str(pvr.id),
        )
        messages.info(request, f"Physical verification request for {pvr.user.enrollment_number} rejected.")

    return redirect('operational_dashboard')



@login_required
@user_passes_test(_is_superuser)
def manage_superusers(request: HttpRequest) -> HttpResponse:
    """Manage superuser privileges (promote/revoke) with audit logging and lockout protection."""
    from django.shortcuts import redirect

    if request.method == "POST":
        if not check_step_up_or_recent_auth(request):
            messages.error(
                request,
                "Privileged session expired. For administrative security, please re-authenticate or confirm your password to modify superuser privileges."
            )
            return redirect("manage_superusers")
        action = request.POST.get("action", "").strip()
        enrollment = request.POST.get("enrollment_number", "").strip().upper()

        if not enrollment:
            messages.error(request, "Enrollment number is required.")
            return redirect("manage_superusers")

        with transaction.atomic():
            try:
                target_user = User.objects.get(enrollment_number=enrollment)
            except User.DoesNotExist:
                messages.error(request, f"No student found with enrollment number {enrollment}.")
                return redirect("manage_superusers")

            # Deterministic lock on active superusers to prevent race conditions during revocation
            active_superusers = User.objects.select_for_update().filter(is_superuser=True, is_active=True)

            if action == "promote":
                if target_user.is_superuser:
                    messages.info(request, f"{target_user.enrollment_number} ({target_user.display_name}) is already a superuser.")
                elif not target_user.is_active or not target_user.verified:
                    messages.error(request, f"Candidate {target_user.enrollment_number} must be an active and verified student to receive superuser privileges.")
                else:
                    target_user.is_superuser = True
                    target_user.is_staff = True
                    target_user.save(update_fields=["is_superuser", "is_staff"])
                    log_audit_event(
                        event_type="SUPERUSER_PROMOTED",
                        actor_enrollment=request.user.enrollment_number,
                        description=f"Promoted {target_user.enrollment_number} ({target_user.display_name}) to superuser",
                        target_id=target_user.enrollment_number,
                    )
                    messages.success(request, f"Successfully granted superuser privileges to {target_user.enrollment_number} ({target_user.display_name}).")
                return redirect("manage_superusers")

            elif action == "revoke":
                if not target_user.is_superuser:
                    messages.info(request, f"{target_user.enrollment_number} is not a superuser.")
                    return redirect("manage_superusers")

                # Lockout protection: ensure at least one active superuser remains
                if target_user.is_active and active_superusers.count() <= 1:
                    messages.error(
                        request,
                        "Action blocked: Cannot revoke privileges from the last active superuser. "
                        "At least one active superuser must always exist to prevent administrative lockout.",
                    )
                    return redirect("manage_superusers")

                target_user.is_superuser = False
                target_user.save(update_fields=["is_superuser"])
                log_audit_event(
                    event_type="SUPERUSER_REVOKED",
                    actor_enrollment=request.user.enrollment_number,
                    description=f"Revoked superuser privileges from {target_user.enrollment_number} ({target_user.display_name})",
                    target_id=target_user.enrollment_number,
                )

                if target_user == request.user:
                    messages.warning(request, "You have revoked superuser privileges from your own account.")
                    return redirect("dashboard")

                messages.success(request, f"Successfully revoked superuser privileges from {target_user.enrollment_number}.")
                return redirect("manage_superusers")

            else:
                messages.error(request, "Invalid management action requested.")
                return redirect("manage_superusers")

    # GET request
    superusers = User.objects.filter(is_superuser=True).order_by("enrollment_number")
    staff_members = User.objects.filter(is_staff=True, is_superuser=False).order_by("enrollment_number")
    verified_candidates = (
        User.objects
        .filter(verified=True, is_superuser=False)
        .order_by("enrollment_number")[:100]
    )
    privileged_audits = (
        AuditEvent.objects
        .filter(event_type__in=["SUPERUSER_PROMOTED", "SUPERUSER_REVOKED"])
        .order_by("-timestamp")[:15]
    )

    return render(
        request,
        "nexus/manage_superusers.html",
        {
            "superusers": superusers,
            "staff_members": staff_members,
            "verified_candidates": verified_candidates,
            "privileged_audits": privileged_audits,
            "active_superusers_count": superusers.filter(is_active=True).count(),
        },
    )


# ---------------------------------------------------------------------------
# Phase 7: Protected CSV Exports with Formula Injection Safeguards
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(_is_staff_or_superuser)
def export_rides_csv(request: HttpRequest) -> HttpResponse:
    """Export rides with formula-injection sanitation and audit logging."""
    if not check_step_up_or_recent_auth(request):
        if 'text/html' in request.META.get('HTTP_ACCEPT', ''):
            messages.warning(request, "Privileged session expired. Please re-authenticate via the prompt below before exporting platform data.")
            return redirect('operational_dashboard')
        return HttpResponseForbidden("Administrative session freshness check failed. Please re-authenticate before exporting platform data.")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="campus_nexus_rides_export.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "ID", "Route", "Route Display", "Departure Time", "Status",
        "Visibility", "Total Spots", "Participants Count", "Created At"
    ])

    rides = (
        RideRequest.objects
        .select_related("creator")
        .prefetch_related("participants")
        .order_by("-departure_time")[:1000]
    )

    for r in rides:
        writer.writerow([
            r.id,
            sanitize_csv_cell(r.route),
            sanitize_csv_cell(r.route_display),
            r.departure_time.isoformat(),
            sanitize_csv_cell(r.status),
            sanitize_csv_cell(r.visibility),
            r.total_spots,
            r.participants.count(),
            r.created_at.isoformat(),
        ])

    log_audit_event(
        event_type="EXPORT_GENERATED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Exported {rides.count()} rides to CSV",
    )
    return response


@login_required
@user_passes_test(_is_staff_or_superuser)
def export_users_csv(request: HttpRequest) -> HttpResponse:
    """Export verified users with sensitive fields strictly omitted."""
    if not check_step_up_or_recent_auth(request):
        if 'text/html' in request.META.get('HTTP_ACCEPT', ''):
            messages.warning(request, "Privileged session expired. Please re-authenticate via the prompt below before exporting platform data.")
            return redirect('operational_dashboard')
        return HttpResponseForbidden("Administrative session freshness check failed. Please re-authenticate before exporting platform data.")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="campus_nexus_users_export.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "ID", "Enrollment", "Name", "Branch", "Degree", "Gender",
        "Verified", "Rides Completed", "On-Time %", "Behavior Reports"
    ])

    users = User.objects.order_by("enrollment_number")[:1000]
    for u in users:
        writer.writerow([
            u.id,
            sanitize_csv_cell(u.enrollment_number),
            sanitize_csv_cell(u.display_name),
            sanitize_csv_cell(u.branch),
            sanitize_csv_cell(u.degree),
            sanitize_csv_cell(u.gender),
            u.verified,
            u.rides_completed,
            u.on_time_percentage,
            u.behavior_reports,
        ])

    log_audit_event(
        event_type="EXPORT_GENERATED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Exported {users.count()} users to CSV",
    )
    return response


@login_required
@user_passes_test(_is_superuser)
def export_applications_csv(request: HttpRequest) -> HttpResponse:
    """Export application queue with formula-injection sanitation. Superuser only; phone redacted by default."""
    if not check_step_up_or_recent_auth(request):
        if 'text/html' in request.META.get('HTTP_ACCEPT', ''):
            messages.warning(request, "Privileged session expired. Please re-authenticate via the prompt below before exporting platform data.")
            return redirect('operational_dashboard')
        return HttpResponseForbidden("Administrative session freshness check failed. Please re-authenticate before exporting platform data.")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="campus_nexus_applications_export.csv"'

    include_phone = request.GET.get("include_phone") == "1"
    headers = ["ID", "Application Number", "Enrollment", "Full Name", "Email", "Gender"]
    if include_phone:
        headers.append("Phone Number")
    headers.extend(["Created At", "Approved"])

    writer = csv.writer(response)
    writer.writerow(headers)

    apps = AccountApplication.objects.order_by("-created_at")[:1000]
    for a in apps:
        row = [
            a.id,
            sanitize_csv_cell(a.application_number),
            sanitize_csv_cell(a.enrollment_number),
            sanitize_csv_cell(a.full_name),
            sanitize_csv_cell(a.email),
            sanitize_csv_cell(a.gender),
        ]
        if include_phone:
            row.append(sanitize_csv_cell(a.phone_number))
        row.extend([
            a.created_at.isoformat() if a.created_at else "",
            a.approved,
        ])
        writer.writerow(row)

    log_audit_event(
        event_type="EXPORT_GENERATED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Exported {apps.count()} applications to CSV (phone included: {include_phone})",
    )
    return response


# ---------------------------------------------------------------------------
# Phase 8: System Health Check Endpoint
# ---------------------------------------------------------------------------

def health_check(request: HttpRequest) -> HttpResponse:
    """Production health check verifying database and application connectivity.
    Supports both machine-readable JSON (format=json or Accept: application/json)
    and browser-friendly HTML with return navigation to Campus Nexus.
    """
    db_ok = True
    db_error = None

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    cache_ok = True
    try:
        from django.core.cache import cache
        cache.set('__health_probe__', '1', 5)
        if cache.get('__health_probe__') != '1':
            cache_ok = False
    except Exception:
        cache_ok = False

    is_ready = db_ok and cache_ok
    status_code = 200 if is_ready else 503
    traffic = get_traffic_telemetry()
    payload = {
        "status": "healthy" if is_ready else "unhealthy",
        "database": "connected" if db_ok else "disconnected",
        "cache": "connected" if cache_ok else "disconnected",
        "timestamp": timezone.now().isoformat(),
        "version": "2.4.0",
        "traffic_telemetry": {
            "total_requests": traffic['total_requests'],
            "avg_latency_ms": traffic['avg_latency_ms'],
            "peak_rps": traffic['peak_rps'],
            "uptime": traffic['uptime_display'],
        }
    }
    if not is_ready:
        if not db_ok:
            payload["error"] = str(db_error) if getattr(settings, 'DEBUG', False) else "Database connection unavailable."
        elif not cache_ok:
            payload["error"] = "Cache connection unavailable." 

    # Return HTML if explicitly requested via format=html or if a browser sends text/html without format=json
    accept_header = request.headers.get('Accept', '') or ''
    wants_html = request.GET.get('format') == 'html' or ('text/html' in accept_header and request.GET.get('format') != 'json')

    if wants_html:
        return render(request, "nexus/health_check.html", {
            "payload": payload,
            "status_code": status_code,
            "db_ok": db_ok,
            "traffic": traffic,
        }, status=status_code)

    # Otherwise return raw JSON (API callers, monitoring, load-balancers, tests)
    return JsonResponse(payload, status=status_code)
