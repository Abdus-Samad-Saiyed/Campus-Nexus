import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class OwnershipEventType(models.TextChoices):
    TRANSFERRED = 'TRANSFERRED', 'Transferred to Active Member'
    OWNER_CLOSED_AND_LEFT = 'OWNER_CLOSED_AND_LEFT', 'Owner Closed Recruitment and Left'
    CLAIMED = 'CLAIMED', 'Claimed by Active Member'


class AccommodationGroupOwnershipEvent(models.Model):
    """Audit record of group management ownership lifecycle events."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        'AccommodationGroup',
        on_delete=models.PROTECT,
        related_name='ownership_events'
    )
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='transferred_from_accommodation_groups'
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='transferred_to_accommodation_groups'
    )
    event_type = models.CharField(max_length=30, choices=OwnershipEventType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='initiated_accommodation_ownership_events'
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.event_type} on {self.group.title} at {self.created_at}"
