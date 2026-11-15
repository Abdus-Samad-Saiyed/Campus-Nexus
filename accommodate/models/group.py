import uuid
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class AdmissionMode(models.TextChoices):
    MANUAL = 'MANUAL', 'Manual Review'
    AUTO = 'AUTO', 'Automatic Queue'


class RecruitmentStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    CLOSED = 'CLOSED', 'Closed'


class GroupStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    ENDED = 'ENDED', 'Ended'


class CostBasis(models.TextChoices):
    PER_PERSON_MONTH = 'PER_PERSON_MONTH', 'Per Person / Month'
    PER_PERSON_TOTAL_STAY = 'PER_PERSON_TOTAL_STAY', 'Per Person / Total Stay'
    GROUP_TOTAL_MONTH = 'GROUP_TOTAL_MONTH', 'Group Total / Month'
    OTHER = 'OTHER', 'Other'


class AccommodationGroup(models.Model):
    """Core aggregate for an accommodation coordination group."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=180)
    destination_city = models.CharField(max_length=120, db_index=True)
    destination_city_normalized = models.CharField(max_length=120, db_index=True)
    location_within_city = models.CharField(max_length=240, db_index=True)
    location_within_city_normalized = models.CharField(max_length=260, db_index=True)
    stay_location = models.ForeignKey(
        'AccommodationStayLocation',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='groups'
    )
    lodging_name = models.CharField(max_length=240, blank=True, default='')
    lodging_name_normalized = models.CharField(max_length=260, blank=True, default='')
    maps_url = models.TextField(blank=True, default='')
    description = models.TextField(blank=True, default='')

    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='initiated_accommodation_groups'
    )
    current_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='owned_accommodation_groups'
    )
    member_limit = models.PositiveIntegerField(null=True, blank=True)
    admission_mode = models.CharField(
        max_length=10,
        choices=AdmissionMode.choices,
        default=AdmissionMode.MANUAL
    )
    recruitment_status = models.CharField(
        max_length=10,
        choices=RecruitmentStatus.choices,
        default=RecruitmentStatus.OPEN
    )
    group_status = models.CharField(
        max_length=10,
        choices=GroupStatus.choices,
        default=GroupStatus.ACTIVE
    )

    estimated_cost_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_cost_basis = models.CharField(max_length=30, choices=CostBasis.choices, null=True, blank=True)
    currency_code = models.CharField(max_length=3, default='INR')
    cost_note = models.CharField(max_length=300, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.IntegerField(default=1)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['group_status', 'recruitment_status', '-created_at']),
            models.Index(fields=['destination_city_normalized']),
            models.Index(fields=['destination_city_normalized', 'recruitment_status']),
            GinIndex(fields=['destination_city_normalized'], opclasses=['gin_trgm_ops'], name='accom_grp_city_trgm_idx'),
            GinIndex(fields=['location_within_city_normalized'], opclasses=['gin_trgm_ops'], name='accom_grp_loc_trgm_idx'),
            GinIndex(fields=['lodging_name_normalized'], opclasses=['gin_trgm_ops'], name='accom_grp_lodge_trgm_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.destination_city:
            self.destination_city_normalized = self.destination_city.strip().lower()
        if self.location_within_city:
            self.location_within_city_normalized = self.location_within_city.strip().lower()
        if self.lodging_name:
            self.lodging_name_normalized = self.lodging_name.strip().lower()
        super().save(*args, **kwargs)

    @property
    def active_members_count(self) -> int:
        return self.memberships.filter(status='ACTIVE').count()

    @property
    def has_available_capacity(self) -> bool:
        if self.member_limit is None:
            return True
        return self.active_members_count < self.member_limit

    def __str__(self):
        return f"{self.title} ({self.destination_city})"
