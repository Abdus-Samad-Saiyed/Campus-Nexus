from accounts.throttling import rate_limit
"""Campus Nexus platform views: About, Contributors, and Feedback/Bug Reporting."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from accounts.models import Contributor, PlatformFeedback, User, log_audit_event
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST


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
        return redirect("contributors")

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
                return redirect("contributors")

    name = name[:100]
    enrollment = enrollment[:20]
    role = (role or "Open Source Contributor")[:100]
    bio = bio[:255]

    if not name:
        messages.error(request, "Name is required to add a contributor.")
        return redirect("contributors")

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
        return redirect("contributors")
    contributor.save()

    log_audit_event(
        event_type="CONTRIBUTOR_NOMINATED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Administrator added contributor: {name} ({role})",
        target_id=str(contributor.id),
    )

    messages.success(request, f"Successfully added {name} as a recognized contributor.")
    return redirect("contributors")


@login_required
@require_POST
def remove_contributor(request: HttpRequest, contributor_id: int) -> HttpResponse:
    """Allows administrators to remove a recognized contributor."""
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, "Permission denied: Only administrators can remove contributors.")
        return redirect("contributors")

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
    return redirect("contributors")


@rate_limit(key_prefix='feedback', limit=5, period_seconds=60)
def feedback_view(request: HttpRequest) -> HttpResponse:
    """Feedback & Bug Reporting: Allows students and staff to submit bugs, feature ideas, and proposals."""
    if request.method == "POST":
        feedback_type = request.POST.get("feedback_type", "GENERAL").strip()
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        submitter_name = request.POST.get("submitter_name", "").strip()
        submitter_contact = request.POST.get("submitter_contact", "").strip()
        system_info = request.META.get("HTTP_USER_AGENT", "")[:250]

        valid_feedback_types = dict(PlatformFeedback.FEEDBACK_TYPES)
        if feedback_type not in valid_feedback_types:
            messages.error(request, "Invalid feedback category selected.")
            return render(request, "nexus/feedback.html", {
                "feedback_type": feedback_type,
                "title": title,
                "description": description,
                "submitter_name": submitter_name,
                "submitter_contact": submitter_contact,
            }, status=400)

        if len(title) > 200 or len(submitter_name) > 100 or len(submitter_contact) > 100 or len(description) > 5000:
            messages.error(request, "One or more feedback fields exceeded maximum allowed length.")
            return render(request, "nexus/feedback.html", {
                "feedback_type": feedback_type,
                "title": title,
                "description": description,
                "submitter_name": submitter_name,
                "submitter_contact": submitter_contact,
            }, status=400)

        if not title or not description:
            messages.error(request, "Title and description are required to submit feedback.")
            return render(request, "nexus/feedback.html", {
                "feedback_type": feedback_type,
                "title": title,
                "description": description,
                "submitter_name": submitter_name,
                "submitter_contact": submitter_contact,
            })

        feedback = PlatformFeedback.objects.create(
            user=request.user if request.user.is_authenticated else None,
            submitter_name=submitter_name or (request.user.get_full_name() if request.user.is_authenticated else "Anonymous Student"),
            submitter_contact=submitter_contact or (request.user.phone_number if request.user.is_authenticated else ""),
            feedback_type=feedback_type,
            title=title,
            description=description,
            system_info=system_info,
            status="OPEN",
        )

        actor_enrollment = request.user.enrollment_number if request.user.is_authenticated else "Guest"
        log_audit_event(
            event_type="FEEDBACK_SUBMITTED",
            actor_enrollment=actor_enrollment,
            description=f"[{feedback.get_feedback_type_display()}] {title[:100]}",
            target_id=str(feedback.id),
        )

        messages.success(
            request,
            f"Thank you! Your {feedback.get_feedback_type_display().lower()} (ID #{feedback.id}) has been recorded. "
            f"Our student maintenance team will review it."
        )
        return redirect("feedback")

    return render(request, "nexus/feedback.html")


def contact_view(request: HttpRequest) -> HttpResponse:
    """Contact Us Page: Student engineering contact directory and online communications desk."""
    return render(request, "nexus/contact.html")


def offline_view(request: HttpRequest) -> HttpResponse:
    """Offline fallback page for Progressive Web App when network disconnects."""
    return render(request, "offline.html")

