from datetime import timedelta
from decimal import Decimal
from typing import Optional
from django.db import models, transaction
from django.db.models import Avg, Q
from django.utils import timezone

from accounts.models import User
from accommodate.exceptions import (
    ApplicationStateError,
    PermissionDeniedError,
)
from accommodate.models import (
    AccommodationComplaint,
    AccommodationMembership,
    AccommodationReputationSnapshot,
    AccommodationReview,
    AccommodationReviewInvitation,
    ComplaintStatus,
    InvitationStatus,
    ModerationStatus,
)


@transaction.atomic
def issue_review_invitations(subject_membership: AccommodationMembership) -> int:
    """Issue review invitations upon a member's departure.
    
    Eligibility uses recorded platform membership intervals:
    R.joined_at < S.left_at AND (R.left_at IS NULL OR R.left_at > S.joined_at)
    Exactly one invitation per distinct reviewer user.
    """
    if not subject_membership.left_at:
        return 0

    deadline = timezone.now() + timedelta(days=30)

    # Find qualifying peer memberships in the same group with overlapping platform intervals
    overlapping_peers = AccommodationMembership.objects.filter(
        group=subject_membership.group,
        joined_at__lt=subject_membership.left_at,
    ).filter(
        Q(left_at__isnull=True) | Q(left_at__gt=subject_membership.joined_at)
    ).exclude(
        user=subject_membership.user
    ).select_related('user')

    # Deduplicate by reviewer user
    seen_users = set()
    invitations_created = 0

    for peer in overlapping_peers:
        if peer.user_id in seen_users:
            continue
        seen_users.add(peer.user_id)

        _, created = AccommodationReviewInvitation.objects.get_or_create(
            subject_membership=subject_membership,
            reviewer_user=peer.user,
            defaults={
                'qualifying_membership': peer,
                'deadline_at': deadline,
                'status': InvitationStatus.OPEN,
                'created_at': timezone.now(),
            }
        )
        if created:
            invitations_created += 1

    return invitations_created


@transaction.atomic
def submit_review(
    invitation_id,
    reviewer: User,
    reliability_score: Optional[int] = None,
    communication_score: Optional[int] = None,
    cooperation_score: Optional[int] = None,
    respect_score: Optional[int] = None,
    private_context: str = '',
) -> AccommodationReview:
    """Submit a structured peer review evaluating coordination conduct."""
    invitation = AccommodationReviewInvitation.objects.select_for_update().get(id=invitation_id)

    if invitation.reviewer_user != reviewer and not reviewer.is_staff:
        raise PermissionDeniedError("You can only submit reviews for your own invitations.")

    if invitation.status != InvitationStatus.OPEN:
        raise ApplicationStateError(f"Invitation is already {invitation.get_status_display().lower()}.")

    # Validate at least one dimension provided
    scores = [reliability_score, communication_score, cooperation_score, respect_score]
    valid_scores = [s for s in scores if s is not None]
    if not valid_scores:
        raise ValueError("At least one score dimension must be evaluated.")

    for s in valid_scores:
        if not (1 <= s <= 5):
            raise ValueError("Scores must be between 1 and 5.")

    review = AccommodationReview.objects.create(
        invitation=invitation,
        reliability_score=reliability_score,
        communication_score=communication_score,
        cooperation_score=cooperation_score,
        respect_score=respect_score,
        private_context=private_context.strip(),
        submitted_at=timezone.now(),
        moderation_status=ModerationStatus.VISIBLE_TO_AGGREGATION,
    )

    invitation.status = InvitationStatus.SUBMITTED
    invitation.save(update_fields=['status'])

    # Rebuild reputation snapshot for the subject student
    rebuild_reputation_snapshot(invitation.subject_membership.user)

    return review


@transaction.atomic
def submit_complaint(
    invitation_id,
    reviewer: User,
    custom_description: str,
    category: str = '',
) -> AccommodationComplaint:
    """Submit a confidential grievance escalated directly to platform moderators."""
    invitation = AccommodationReviewInvitation.objects.select_for_update().get(id=invitation_id)

    if invitation.reviewer_user != reviewer and not reviewer.is_staff:
        raise PermissionDeniedError("You can only submit complaints for your own invitations.")

    if not custom_description.strip():
        raise ValueError("Complaint narrative description is required.")

    complaint, created = AccommodationComplaint.objects.get_or_create(
        invitation=invitation,
        defaults={
            'category': category.strip(),
            'custom_description': custom_description.strip(),
            'status': ComplaintStatus.SUBMITTED,
            'created_at': timezone.now(),
        }
    )
    if not created:
        complaint.category = category.strip()
        complaint.custom_description = custom_description.strip()
        complaint.save(update_fields=['category', 'custom_description'])

    return complaint


@transaction.atomic
def rebuild_reputation_snapshot(user: User) -> AccommodationReputationSnapshot:
    """Calculate and persist the normalized accommodation reputation snapshot for a student.
    
    Averages each dimension per-group first to avoid large-group distortion,
    then averages across distinct groups to produce a 0-100 composite score.
    """
    reviews = AccommodationReview.objects.filter(
        invitation__subject_membership__user=user,
        moderation_status=ModerationStatus.VISIBLE_TO_AGGREGATION,
    ).select_related('invitation__subject_membership')

    review_count = reviews.count()
    if review_count == 0:
        snapshot, _ = AccommodationReputationSnapshot.objects.update_or_create(
            user=user,
            defaults={
                'score_version': 1,
                'reliability_average': None,
                'communication_average': None,
                'cooperation_average': None,
                'respect_average': None,
                'composite_score': Decimal('0.00'),
                'review_count': 0,
                'group_count': 0,
                'computed_at': timezone.now(),
            }
        )
        return snapshot

    # Group reviews by distinct group_id
    groups_data = {}
    for r in reviews:
        gid = r.invitation.subject_membership.group_id
        if gid not in groups_data:
            groups_data[gid] = {'rel': [], 'com': [], 'coop': [], 'resp': []}
        if r.reliability_score is not None:
            groups_data[gid]['rel'].append(r.reliability_score)
        if r.communication_score is not None:
            groups_data[gid]['com'].append(r.communication_score)
        if r.cooperation_score is not None:
            groups_data[gid]['coop'].append(r.cooperation_score)
        if r.respect_score is not None:
            groups_data[gid]['resp'].append(r.respect_score)

    distinct_groups_count = len(groups_data)

    def avg_of_group_means(dimension_key):
        means = []
        for g_dict in groups_data.values():
            scores_list = g_dict[dimension_key]
            if scores_list:
                means.append(sum(scores_list) / len(scores_list))
        if not means:
            return None
        return round(Decimal(str(sum(means) / len(means))), 2)

    rel_avg = avg_of_group_means('rel')
    com_avg = avg_of_group_means('com')
    coop_avg = avg_of_group_means('coop')
    resp_avg = avg_of_group_means('resp')

    # Normalize 1-5 to 0-100: composite = ((mean - 1) / 4) * 100
    avail_means = [m for m in (rel_avg, com_avg, coop_avg, resp_avg) if m is not None]
    if avail_means:
        overall_mean = sum(avail_means) / Decimal(str(len(avail_means)))
        composite = round(((overall_mean - Decimal('1.00')) / Decimal('4.00')) * Decimal('100.00'), 2)
    else:
        composite = Decimal('0.00')

    snapshot, _ = AccommodationReputationSnapshot.objects.update_or_create(
        user=user,
        defaults={
            'score_version': 1,
            'reliability_average': rel_avg,
            'communication_average': com_avg,
            'cooperation_average': coop_avg,
            'respect_average': resp_avg,
            'composite_score': composite,
            'review_count': review_count,
            'group_count': distinct_groups_count,
            'computed_at': timezone.now(),
        }
    )
    return snapshot
