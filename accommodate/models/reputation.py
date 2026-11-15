import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class InvitationStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    SUBMITTED = 'SUBMITTED', 'Submitted'
    SKIPPED = 'SKIPPED', 'Skipped'
    EXPIRED = 'EXPIRED', 'Expired'


class ModerationStatus(models.TextChoices):
    VISIBLE_TO_AGGREGATION = 'VISIBLE_TO_AGGREGATION', 'Visible to Aggregation'
    UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
    EXCLUDED = 'EXCLUDED', 'Excluded by Moderation'


class ComplaintStatus(models.TextChoices):
    SUBMITTED = 'SUBMITTED', 'Submitted'
    UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
    RESOLVED = 'RESOLVED', 'Resolved'
    DISMISSED = 'DISMISSED', 'Dismissed'


class AccommodationReviewInvitation(models.Model):
    """Invitation issued to an eligible peer upon a member's departure."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject_membership = models.ForeignKey(
        'AccommodationMembership',
        on_delete=models.PROTECT,
        related_name='received_review_invitations'
    )
    reviewer_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='accommodation_review_invitations'
    )
    qualifying_membership = models.ForeignKey(
        'AccommodationMembership',
        on_delete=models.PROTECT,
        related_name='qualifying_review_invitations'
    )
    deadline_at = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        choices=InvitationStatus.choices,
        default=InvitationStatus.OPEN
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['subject_membership', 'reviewer_user'],
                name='accom_one_invitation_per_subject_reviewer',
            ),
        ]

    def __str__(self):
        return f"Invite for {self.reviewer_user_id} regarding subject {self.subject_membership_id}"


class AccommodationReview(models.Model):
    """Structured 4-dimension peer evaluation of accommodation coordination conduct."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invitation = models.OneToOneField(
        'AccommodationReviewInvitation',
        on_delete=models.PROTECT,
        related_name='review'
    )
    reliability_score = models.PositiveSmallIntegerField(null=True, blank=True)
    communication_score = models.PositiveSmallIntegerField(null=True, blank=True)
    cooperation_score = models.PositiveSmallIntegerField(null=True, blank=True)
    respect_score = models.PositiveSmallIntegerField(null=True, blank=True)
    private_context = models.TextField(blank=True, default='')
    submitted_at = models.DateTimeField(default=timezone.now)
    moderation_status = models.CharField(
        max_length=30,
        choices=ModerationStatus.choices,
        default=ModerationStatus.VISIBLE_TO_AGGREGATION
    )

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Review {self.id} for invitation {self.invitation_id}"


class AccommodationComplaint(models.Model):
    """Confidential grievance escalated to platform moderators."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invitation = models.OneToOneField(
        'AccommodationReviewInvitation',
        on_delete=models.PROTECT,
        related_name='complaint'
    )
    category = models.CharField(max_length=50, blank=True, default='')
    custom_description = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=ComplaintStatus.choices,
        default=ComplaintStatus.SUBMITTED
    )
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='moderated_accommodation_complaints'
    )
    moderator_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Complaint {self.id} (Status: {self.status})"


class AccommodationReputationSnapshot(models.Model):
    """Rebuildable aggregated accommodation conduct snapshot for a student."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='accommodation_reputation_snapshot'
    )
    score_version = models.IntegerField(default=1)
    reliability_average = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    communication_average = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    cooperation_average = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    respect_average = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    composite_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    review_count = models.PositiveIntegerField(default=0)
    group_count = models.PositiveIntegerField(default=0)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-computed_at']

    @property
    def has_limited_history(self) -> bool:
        return self.review_count < 3

    def __str__(self):
        enrollment = getattr(self.user, 'enrollment_number', str(self.user_id))
        return f"Reputation for {enrollment} (Score: {self.composite_score}, Reviews: {self.review_count})"
