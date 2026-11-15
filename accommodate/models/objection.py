import uuid
from django.db import models
from django.utils import timezone


class AccommodationApplicationObjection(models.Model):
    """Advisory objection lodged by an active group member against a pending MANUAL application."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        'AccommodationJoinApplication',
        on_delete=models.PROTECT,
        related_name='objections'
    )
    objector_membership = models.ForeignKey(
        'AccommodationMembership',
        on_delete=models.PROTECT,
        related_name='filed_objections'
    )
    reason = models.TextField(blank=True, default='')
    objection_round = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['application', 'objector_membership', 'objection_round'],
                name='accom_one_objection_per_membership_app_round',
            ),
        ]

    def __str__(self):
        return f"Objection on {self.application_id} (Round {self.objection_round})"
