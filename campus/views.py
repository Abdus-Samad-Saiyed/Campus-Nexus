import re
from accounts.throttling import rate_limit
"""Campus Nexus platform views: About, Contributors, and Feedback/Bug Reporting."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import Contributor, PlatformFeedback, User, log_audit_event
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST


def _redirect_contributors_target(request: HttpRequest) -> HttpResponse:
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
        return redirect(referer)
    return redirect("contributors")


def about_view(request: HttpRequest) -> HttpResponse:
    """About Page: The vision, philosophy, technology stack, and module invitation for Campus Nexus."""
    return render(request, "nexus/about.html")


def contributors_view(request: HttpRequest) -> HttpResponse:
    """Contributors Page: Listing creators, architects, repos, and recognized contributors."""
    contributors = Contributor.objects.filter(is_active=True).select_related('nominated_by', 'user').order_by('created_at')

    can_manage_contributors = False
    if request.user.is_authenticated:
        if request.user.is_superuser or request.user.is_staff:
            can_manage_contributors = True

    return render(request, "nexus/contributors.html", {
        "contributors": contributors,
        "can_manage_contributors": can_manage_contributors,
    })


@login_required
@require_POST
def nominate_contributor(request: HttpRequest) -> HttpResponse:
    """Allows administrators to add recognized contributors."""
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, "Permission denied: Only administrators can add contributors.")
        return _redirect_contributors_target(request)

    name = request.POST.get("name", "").strip()
    enrollment = request.POST.get("enrollment_number", "").strip().upper()
    role = request.POST.get("role", "Open Source Contributor").strip()
    github_url = request.POST.get("github_url", "").strip()
    linkedin_url = request.POST.get("linkedin_url", "").strip()
    bio = request.POST.get("bio", "").strip()

    from django.core.validators import URLValidator
    from django.core.exceptions import ValidationError

    validator = URLValidator(schemes=['http', 'https'])
    for u in (github_url, linkedin_url):
        if u:
            try:
                validator(u)
            except ValidationError:
                messages.error(request, "Invalid URL format for GitHub or LinkedIn.")
                return _redirect_contributors_target(request)

    name = name[:100]
    enrollment = enrollment[:20]
    role = (role or "Open Source Contributor")[:100]
    bio = bio[:255]

    if not name:
        messages.error(request, "Name is required to add a contributor.")
        return _redirect_contributors_target(request)

    linked_user = None
    if enrollment:
        linked_user = User.objects.filter(enrollment_number=enrollment).first()

    contributor = Contributor(
        user=linked_user,
        name=name,
        enrollment_number=enrollment,
        role=role,
        github_url=github_url,
        linkedin_url=linkedin_url,
        bio=bio,
        nominated_by=request.user,
        is_active=True,
    )
    try:
        contributor.full_clean()
    except ValidationError as e:
        messages.error(request, f"Validation error: {e}")
        return _redirect_contributors_target(request)
    contributor.save()

    log_audit_event(
        event_type="CONTRIBUTOR_NOMINATED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Administrator added contributor: {name} ({role})",
        target_id=str(contributor.id),
    )

    messages.success(request, f"Successfully added {name} as a recognized contributor.")
    return _redirect_contributors_target(request)


@login_required
@require_POST
def remove_contributor(request: HttpRequest, contributor_id: int) -> HttpResponse:
    """Allows administrators to remove a recognized contributor."""
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, "Permission denied: Only administrators can remove contributors.")
        return _redirect_contributors_target(request)

    from django.shortcuts import get_object_or_404
    contributor = get_object_or_404(Contributor, id=contributor_id)
    name = contributor.name
    contributor.delete()

    log_audit_event(
        event_type="CONTRIBUTOR_REMOVED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Administrator removed contributor: {name}",
        target_id=str(contributor_id),
    )

    messages.success(request, f"Removed {name} from recognized contributors.")
    return _redirect_contributors_target(request)


def _is_enrollment_like(value: str) -> bool:
    """
    Check if a string matches the structural pattern of a student enrollment number
    (e.g., 2024BCSE065, 2024bcse070, 2024-BCSE-070, 20XXBCSE001, GUEST_DEMO, etc.)
    and does not look like an email or telephone number.
    """
    if not value:
        return False
    raw = value.strip()
    if '@' in raw:
        return False
    clean = re.sub(r'[\s\-_/]', '', raw).upper()
    if clean.isdigit() and len(clean) >= 10:
        return False
    if raw.startswith('+'):
        return False

    # Standard Campus Nexus enrollment regex: (2024|20XX)[BMP][A-Z]{3}\d{3} or GUEST_DEMO
    if re.match(r'^(?:(?:\d{4}|20XX)[BMP][A-Z]{3}\d{3}|GUEST_DEMO)$', clean, re.IGNORECASE):
        return True

    # Broader academic enrollment pattern (4 digits, 2-6 letters, 1-4 digits)
    if re.match(r'^(?:\d{4}|20XX)[A-Z]{2,6}\d{1,4}$', clean, re.IGNORECASE):
        return True

    return False


def _handle_feedback_post(request: HttpRequest, error_redirect: str = "feedback") -> HttpResponse:
    """Shared POST handler for submitting platform feedback, bug reports, and proposals."""
    feedback_type = request.POST.get("feedback_type", "GENERAL").strip().upper()
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()
    submitter_name = request.POST.get("submitter_name", "").strip()
    submitter_contact = request.POST.get("submitter_contact", "").strip()
    system_info = request.META.get("HTTP_USER_AGENT", "")[:250]

    valid_feedback_types = dict(PlatformFeedback.FEEDBACK_TYPES)
    if feedback_type not in valid_feedback_types:
        messages.error(request, "Invalid feedback category selected.")
        return redirect(error_redirect)

    if len(title) > 200 or len(submitter_name) > 100 or len(submitter_contact) > 100 or len(description) > 5000:
        messages.error(request, "One or more feedback fields exceeded maximum allowed length.")
        return redirect(error_redirect)

    if not title or not description:
        messages.error(request, "Title and description are required to submit feedback.")
        return redirect(error_redirect)

    # -------------------------------------------------------------------------
    # Student Enrollment Verification, Proxy Attribution, & Name Veto Logic
    # -------------------------------------------------------------------------
    clean_contact = re.sub(r'[\s\-_/]', '', submitter_contact).strip().upper() if submitter_contact else ""
    clean_name = re.sub(r'[\s\-_/]', '', submitter_name).strip().upper() if submitter_name else ""

    target_user = None
    candidate_enrollment = ""

    # Check if submitter_contact is or looks like an enrollment number
    if clean_contact:
        user_by_contact = User.objects.filter(enrollment_number__iexact=clean_contact).first()
        if user_by_contact:
            target_user = user_by_contact
            candidate_enrollment = user_by_contact.enrollment_number
        elif _is_enrollment_like(submitter_contact):
            candidate_enrollment = clean_contact

    # If contact was not an enrollment, check if submitter provided enrollment in name field
    if not target_user and clean_name:
        user_by_name = User.objects.filter(enrollment_number__iexact=clean_name).first()
        if user_by_name:
            target_user = user_by_name
            candidate_enrollment = user_by_name.enrollment_number
        elif _is_enrollment_like(submitter_name):
            candidate_enrollment = clean_name

    # Check 1: During bug reports, you cannot make a report against a person whose enrollment doesn't exist
    if feedback_type == 'BUG' and candidate_enrollment and not target_user:
        messages.error(
            request,
            f"Enrollment number '{candidate_enrollment}' does not exist in the student registry. "
            f"You cannot submit a bug report against a person whose enrollment does not exist."
        )
        return redirect(error_redirect)

    # Fallback to authenticated user if no specific target user was resolved and no other enrollment candidate was provided
    if not target_user and not candidate_enrollment and request.user.is_authenticated:
        target_user = request.user

    attributed_user = target_user
    reported_by_user = request.user if request.user.is_authenticated else None

    # Check 2: If target user is resolved, verify name and veto if they don't match
    if target_user:
        expected_name = target_user.get_full_name().strip() or target_user.first_name.strip() or target_user.enrollment_number
        norm_submitted = " ".join(submitter_name.split()).strip().lower()
        norm_expected = " ".join(expected_name.split()).strip().lower()

        # If name doesn't match or was left blank, veto to the name attached to the enrollment
        if not norm_submitted or norm_submitted != norm_expected:
            submitter_name = expected_name
        else:
            submitter_name = expected_name

        if candidate_enrollment:
            submitter_contact = target_user.enrollment_number
        else:
            submitter_contact = submitter_contact or target_user.enrollment_number or target_user.phone_number
    else:
        submitter_name = submitter_name or "Anonymous Student"
        submitter_contact = submitter_contact or ""

    feedback = PlatformFeedback.objects.create(
        user=attributed_user,
        reported_by=reported_by_user,
        submitter_name=submitter_name,
        submitter_contact=submitter_contact,
        feedback_type=feedback_type,
        title=title,
        description=description,
        system_info=system_info,
        status="OPEN",
    )

    actor_enrollment = request.user.enrollment_number if request.user.is_authenticated else "Guest"
    audit_desc = f"[{feedback.get_feedback_type_display()}] {title[:100]}"
    if attributed_user and request.user.is_authenticated and attributed_user != request.user:
        audit_desc += f" (attributed to {attributed_user.enrollment_number})"

    log_audit_event(
        event_type="FEEDBACK_SUBMITTED",
        actor_enrollment=actor_enrollment,
        description=audit_desc,
        target_id=str(feedback.id),
    )

    if attributed_user and request.user.is_authenticated and attributed_user != request.user:
        messages.success(
            request,
            f"Thank you! Your {feedback.get_feedback_type_display().lower()} has been recorded and attributed to "
            f"{submitter_name} ({attributed_user.enrollment_number}). Our student maintenance team will review it."
        )
    else:
        messages.success(
            request,
            f"Thank you! Your {feedback.get_feedback_type_display().lower()} has been recorded. "
            f"Our student maintenance team will review it."
        )
    return redirect("feedback")


@rate_limit(key_prefix='feedback', limit=5, period_seconds=60)
def feedback_submit_view(request: HttpRequest, default_type: str = "GENERAL") -> HttpResponse:
    """Dedicated Feedback & Bug Report Submission Page."""
    valid_types = dict(PlatformFeedback.FEEDBACK_TYPES)
    fallback_type = default_type if default_type in valid_types else "GENERAL"

    if request.method == "POST":
        return _handle_feedback_post(request, error_redirect=request.get_full_path())

    requested_type = request.GET.get("type", "").strip().upper()
    selected_type = requested_type if requested_type in valid_types else fallback_type

    return render(request, "nexus/feedback_submit.html", {
        "selected_type": selected_type,
        "feedback_types": PlatformFeedback.FEEDBACK_TYPES,
    })


@rate_limit(key_prefix='feedback', limit=5, period_seconds=60)
def feedback_view(request: HttpRequest) -> HttpResponse:
    """Feedback & Bug Reporting: Allows students and staff to submit bugs, feature ideas, and proposals, and browse public tracker."""
    if request.method == "POST":
        return _handle_feedback_post(request, error_redirect="feedback")

    # GET: Dedicated Searchable Bug & Feedback Tracker
    qs = PlatformFeedback.objects.all().order_by('-created_at')

    # Metrics
    total_count = qs.count()
    bugs_count = qs.filter(feedback_type='BUG').count()
    features_count = qs.filter(feedback_type='FEATURE').count()
    proposals_count = qs.filter(feedback_type='PROPOSAL').count()
    resolved_count = qs.filter(status='RESOLVED').count()

    search_query = request.GET.get('q', '').strip()
    selected_type = request.GET.get('type', '').strip()
    selected_status = request.GET.get('status', '').strip()
    view_filter = request.GET.get('view', '').strip()

    # User-centric metrics for authenticated user
    my_stats = None
    if request.user.is_authenticated:
        user_filter = Q(user=request.user)
        if request.user.enrollment_number:
            user_filter |= Q(submitter_contact__icontains=request.user.enrollment_number)
        my_qs = PlatformFeedback.objects.filter(user_filter)
        my_stats = {
            'total': my_qs.count(),
            'bugs': my_qs.filter(feedback_type='BUG').count(),
            'features': my_qs.filter(feedback_type='FEATURE').count(),
            'proposals': my_qs.filter(feedback_type='PROPOSAL').count(),
            'resolved': my_qs.filter(status='RESOLVED').count(),
        }
        if view_filter == 'mine':
            qs = my_qs

    # Community Bug Hunters Leaderboard
    leaderboard = list(
        PlatformFeedback.objects
        .exclude(submitter_name='')
        .values('submitter_name')
        .annotate(
            total=Count('id'),
            bugs=Count('id', filter=Q(feedback_type='BUG')),
            resolved=Count('id', filter=Q(status='RESOLVED'))
        )
        .filter(total__gt=0)
        .order_by('-bugs', '-total')[:5]
    )

    if search_query:
        qs = qs.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))

    if selected_type and selected_type in dict(PlatformFeedback.FEEDBACK_TYPES):
        qs = qs.filter(feedback_type=selected_type)

    if selected_status and selected_status in dict(PlatformFeedback.STATUS_CHOICES):
        qs = qs.filter(status=selected_status)

    paginator = Paginator(qs, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, "nexus/feedback.html", {
        "feedbacks": page_obj,
        "total_count": total_count,
        "bugs_count": bugs_count,
        "features_count": features_count,
        "proposals_count": proposals_count,
        "resolved_count": resolved_count,
        "search_query": search_query,
        "selected_type": selected_type,
        "selected_status": selected_status,
        "view_filter": view_filter,
        "my_stats": my_stats,
        "leaderboard": leaderboard,
        "feedback_types": PlatformFeedback.FEEDBACK_TYPES,
        "status_choices": PlatformFeedback.STATUS_CHOICES,
        "is_admin": bool(request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)),
    })


def contact_view(request: HttpRequest) -> HttpResponse:
    """Contact Us Page: Student engineering contact directory and online communications desk."""
    return render(request, "nexus/contact.html")


def offline_view(request: HttpRequest) -> HttpResponse:
    """Offline fallback page for Progressive Web App when network disconnects."""
    return render(request, "offline.html")

