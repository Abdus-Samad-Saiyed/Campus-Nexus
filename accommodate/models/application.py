import uuid
from django.conf import settings
from django.contrib.postgres.fields import DateRangeField
from django.db import models
from django.db.models import Q


class ApplicationStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending Owner Review'
    WAITLISTED = 'WAITLISTED', 'Waitlisted in Queue'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'
    WITHDRAWN = 'WITHDRAWN', 'Withdrawn by Applicant'
    AUTO_RESCINDED = 'AUTO_RESCINDED', 'Auto-Rescinded (Conflicting Membership)'
    EXPIRED = 'EXPIRED', 'Expired (Group Closed/Ended)'


class AccommodationJoinApplication(models.Model):
    """A student's explicit application to join an accommodation group."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        'AccommodationGroup',
        on_delete=models.PROTECT,
        related_name='applications'
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='accommodation_applications'
    )
    requested_stay = DateRangeField(help_text="Requested date range [start, end), half-open")
    message = models.TextField(blank=True, default='')
    objection_round = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.PENDING
    )
    membership = models.OneToOneField(
        'AccommodationMembership',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='source_application'
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='resolved_accommodation_applications'
    )
    resolution_code = models.CharField(max_length=40, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'applicant'],
                condition=Q(status__in=['PENDING', 'WAITLISTED']),
                name='accom_one_unresolved_app_per_group_applicant',
            ),
        ]
        indexes = [
            models.Index(fields=['group', 'status', 'created_at', 'id']),
            models.Index(fields=['applicant', 'status', '-created_at']),
        ]

    def __str__(self):
        enrollment = getattr(self.applicant, 'enrollment_number', str(self.applicant_id))
        return f"App by {enrollment} for {self.group.title} ({self.status})"
