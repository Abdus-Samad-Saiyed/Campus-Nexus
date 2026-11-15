from typing import Optional
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accommodate.models import (
    AccommodationComplaint,
    AccommodationReview,
    AccommodationReviewInvitation,
    ComplaintStatus,
    InvitationStatus,
)
from accommodate.services.reputation_service import submit_complaint, submit_review


@login_required
def my_reviews_view(request: HttpRequest) -> HttpResponse:
    """View pending review invitations and past feedback given/received."""
    pending_invitations = list(
        AccommodationReviewInvitation.objects.filter(
            reviewer_user=request.user,
            status=InvitationStatus.OPEN,
        ).select_related(
            'subject_membership__group',
            'subject_membership__user',
        ).order_by('-created_at')
    )

    submitted_reviews = list(
        AccommodationReview.objects.filter(
            invitation__reviewer_user=request.user
        ).select_related(
            'invitation__subject_membership__user',
            'invitation__subject_membership__group'
        ).order_by('-submitted_at')
    )

    reviews_received = list(
        AccommodationReview.objects.filter(
            invitation__subject_membership__user=request.user
        ).select_related(
            'invitation__reviewer_user',
            'invitation__subject_membership__group'
        ).order_by('-submitted_at')
    )

    context = {
        'pending_invitations': pending_invitations,
        'submitted_reviews': submitted_reviews,
        'reviews_received': reviews_received,
    }
    return render(request, 'accommodate/my_reviews.html', context)


@login_required
def submit_review_view(request: HttpRequest, invitation_id) -> HttpResponse:
    """Submit a peer exit review for an accommodation group peer."""
    invitation = get_object_or_404(
        AccommodationReviewInvitation,
        id=invitation_id,
        reviewer_user=request.user,
    )

    if invitation.status != InvitationStatus.OPEN:
        messages.info(request, "This review invitation is no longer open.")
        return redirect('accommodate:my_reviews')

    departed_user = invitation.subject_membership.user
    group = invitation.subject_membership.group

    if request.method == 'POST':
        def parse_score(val: str) -> Optional[int]:
            if not val or not val.strip():
                return None
            try:
                v = int(val)
                return v if 1 <= v <= 5 else None
            except ValueError:
                return None

        rel_score = parse_score(request.POST.get('reliability_score', '5'))
        comm_score = parse_score(request.POST.get('communication_score', '5'))
        coop_score = parse_score(request.POST.get('cooperation_score', '5'))
        resp_score = parse_score(request.POST.get('respect_score', '5'))
        private_context = request.POST.get('private_context', '').strip()

        try:
            submit_review(
                invitation_id=invitation.id,
                reviewer=request.user,
                reliability_score=rel_score,
                communication_score=comm_score,
                cooperation_score=coop_score,
                respect_score=resp_score,
                private_context=private_context,
            )
            messages.success(request, f"Review for {departed_user.display_name} submitted successfully.")
            return redirect('accommodate:my_reviews')
        except Exception as e:
            messages.error(request, f"Review submission failed: {e}")

    return render(request, 'accommodate/review_form.html', {
        'invitation': invitation,
        'departed_user': departed_user,
        'group': group,
    })


@login_required
@require_POST
def skip_review_view(request: HttpRequest, invitation_id) -> HttpResponse:
    """Skip an optional review invitation."""
    invitation = get_object_or_404(
        AccommodationReviewInvitation,
        id=invitation_id,
        reviewer_user=request.user,
    )

    if invitation.status == InvitationStatus.OPEN:
        invitation.status = InvitationStatus.SKIPPED
        invitation.save(update_fields=['status'])
        messages.info(request, "Review invitation skipped.")

    return redirect('accommodate:my_reviews')


@login_required
def submit_complaint_view(request: HttpRequest) -> HttpResponse:
    """Submit a confidential grievance escalated directly to platform moderators."""
    if request.method == 'POST':
        invitation_id = request.POST.get('invitation_id', '').strip()
        category = request.POST.get('category', '').strip()
        description = request.POST.get('custom_description', '').strip()

        if not invitation_id or not description:
            messages.error(request, "Please provide the complaint description.")
            return redirect('accommodate:my_reviews')

        try:
            complaint = submit_complaint(
                invitation_id=invitation_id,
                reviewer=request.user,
                custom_description=description,
                category=category,
            )
            messages.success(request, f"Grievance (Ref: #{complaint.id}) submitted to moderation desk.")
            return redirect('accommodate:my_reviews')
        except Exception as e:
            messages.error(request, f"Submission error: {e}")

    return redirect('accommodate:my_reviews')
