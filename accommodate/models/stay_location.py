import uuid
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class AccommodationStayLocation(models.Model):
    """Canonical, deduplicated accommodation stay location or neighborhood."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=240, help_text="Display name for the hostel, PG, apartment or area")
    formatted_address = models.TextField(help_text="Canonical standardized Google Maps address")
    city = models.CharField(max_length=120, db_index=True)
    city_normalized = models.CharField(max_length=120, db_index=True)
    location_within_city = models.CharField(max_length=240, db_index=True)
    location_within_city_normalized = models.CharField(max_length=260, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    google_place_id = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    maps_url = models.TextField(blank=True, default='')
    is_seeded = models.BooleanField(default=False)
    usage_count = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-usage_count', 'name']
        indexes = [
            models.Index(fields=['city_normalized']),
            models.Index(fields=['city_normalized', '-usage_count']),
            GinIndex(fields=['name'], opclasses=['gin_trgm_ops'], name='accom_stay_name_trgm_idx'),
            GinIndex(fields=['city_normalized'], opclasses=['gin_trgm_ops'], name='accom_stay_city_trgm_idx'),
            GinIndex(fields=['location_within_city_normalized'], opclasses=['gin_trgm_ops'], name='accom_stay_loc_trgm_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.city:
            self.city_normalized = self.city.strip().lower()
        if self.location_within_city:
            self.location_within_city_normalized = self.location_within_city.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.city})"
