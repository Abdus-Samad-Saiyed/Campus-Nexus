import uuid
from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.db import models
from django.db.models import Q
from django.utils import timezone


class MembershipStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    LEFT = 'LEFT', 'Left'
    REMOVED = 'REMOVED', 'Removed'


class DepartureType(models.TextChoices):
    VOLUNTARY = 'VOLUNTARY', 'Voluntary'
    REMOVED = 'REMOVED', 'Removed'


class DepartureReason(models.TextChoices):
    REPEATED_NON_COMMUNICATION = 'REPEATED_NON_COMMUNICATION', 'Repeated Non-Communication'
    DISRUPTIVE_CONDUCT = 'DISRUPTIVE_CONDUCT', 'Disruptive Conduct'
    BREACH_OF_GROUP_AGREEMENT = 'BREACH_OF_GROUP_AGREEMENT', 'Breach of Group Agreement'
    SAFETY_CONCERN = 'SAFETY_CONCERN', 'Safety Concern'
    OTHER = 'OTHER', 'Other'


class AccommodationMembership(models.Model):
    """Continuous active or historical platform membership episode in a group."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        'AccommodationGroup',
        on_delete=models.PROTECT,
        related_name='memberships'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='accommodation_memberships'
    )
    planned_stay = DateRangeField(help_text="Declared date range [start, end), half-open")
    status = models.CharField(
        max_length=16,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE
    )
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    departure_type = models.CharField(
        max_length=16,
        choices=DepartureType.choices,
        null=True,
        blank=True
    )
    departure_reason_code = models.CharField(
        max_length=40,
        choices=DepartureReason.choices,
        null=True,
        blank=True
    )
    departure_reason_text = models.TextField(blank=True, default='')
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='removed_accommodation_memberships'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['joined_at']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'user'],
                condition=Q(status='ACTIVE'),
                name='accom_one_active_membership_per_group_user',
            ),
            ExclusionConstraint(
                name='accom_no_overlapping_active_memberships_per_user',
                expressions=[
                    ('user', RangeOperators.EQUAL),
                    ('planned_stay', RangeOperators.OVERLAPS),
                ],
                condition=Q(status='ACTIVE'),
            ),
        ]
        indexes = [
            models.Index(fields=['group', 'status']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        enrollment = getattr(self.user, 'enrollment_number', str(self.user_id))
        return f"{enrollment} in {self.group.title} ({self.status})"


class AccommodationMembershipDateChange(models.Model):
    """Historical record of an occupant altering their planned stay date range."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(
        'AccommodationMembership',
        on_delete=models.PROTECT,
        related_name='date_changes'
    )
    old_period = DateRangeField()
    new_period = DateRangeField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='accommodation_date_changes'
    )
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"DateChange for {self.membership_id} at {self.changed_at}"
