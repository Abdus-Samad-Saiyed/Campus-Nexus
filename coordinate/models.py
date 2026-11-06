"""
Ride coordination models for COORDINATE.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import User


class ParticipantStatus(models.TextChoices):
    CONFIRMED = 'CONFIRMED', 'Confirmed Active Passenger'
    PENDING = 'PENDING', 'Pending Host Approval'
    WAITLISTED = 'WAITLISTED', 'Standby Queue'
    LEFT = 'LEFT', 'Voluntarily Left Ride'
    KICKED = 'KICKED', 'Removed by Host'
    DECLINED = 'DECLINED', 'Declined by Host'
    CANCELLED = 'CANCELLED', 'Cancelled / Withdrawn'


class ConfirmedManager(models.Manager):
    """Fail-safe default manager: existing unfiltered queries automatically filter status='CONFIRMED'."""
    def get_queryset(self):
        return super().get_queryset().filter(status=ParticipantStatus.CONFIRMED)


class RideRequest(models.Model):
    """A shared cab ride that students can join."""

    ROUTE_CHOICES = [
        ('NIT_TO_AIRPORT',  'NIT Srinagar → Airport'),
        ('AIRPORT_TO_NIT',  'Airport → NIT Srinagar'),
        ('NIT_TO_STATION',  'NIT Srinagar → Railway Station'),
        ('STATION_TO_NIT',  'Railway Station → NIT Srinagar'),
        ('CUSTOM',          'Custom Route'),
    ]

    VISIBILITY_CHOICES = [
        ('MIXED',   'Mixed'),
        ('BOYS',    'Boys Only'),
        ('GIRLS',   'Girls Only'),
        ('FRIENDS', 'Friends Only'),
    ]

    STATUS_CHOICES = [
        ('OPEN',      'Open'),
        ('FULL',      'Full'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    creator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_rides',
    )
    route = models.CharField(max_length=30, choices=ROUTE_CHOICES)
    custom_from_location = models.CharField(max_length=255, blank=True)
    custom_to_location = models.CharField(max_length=255, blank=True)
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES)
    departure_time = models.DateTimeField()
    total_spots = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    kicked_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='kicked_from_rides',
    )
    left_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='left_rides',
    )
    demo_session_key = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        help_text='Session key for ephemeral guest demo rides.',
    )
    share_chat_history = models.BooleanField(
        default=False,
        help_text='Allow participants who join later to see chat messages from before they joined.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Ride Request'
        verbose_name_plural = 'Ride Requests'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_spots__gte=1, total_spots__lte=6),
                name='check_total_spots_range',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'departure_time'], name='idx_ride_status_dept'),
            models.Index(fields=['creator', 'status'], name='idx_ride_creator_status'),
        ]

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def route_display(self) -> str:
        """Human-readable route string, handling custom routes."""
        if self.route == 'CUSTOM' and self.custom_from_location and self.custom_to_location:
            return f'{self.custom_from_location} → {self.custom_to_location}'
        return self.get_route_display()

    def participant_count(self) -> int:
        if not self.pk:
            return 0
        annotated = getattr(self, 'participants_total', None)
        if annotated is not None:
            return int(annotated)
        return self.participants.filter(status=ParticipantStatus.CONFIRMED).count()

    def spots_remaining(self) -> int:
        return max(0, self.total_spots - self.participant_count())

    def is_full(self) -> bool:
        return self.participant_count() >= self.total_spots

    @property
    def co_riders(self):
        """Confirmed participants excluding the creator/host."""
        if not self.pk:
            return []
        return [p for p in self.participants.all() if p.user_id != self.creator_id]

    @property
    def confirmed_participants(self):
        """All confirmed participants in the ride."""
        if not self.pk:
            return []
        return list(self.participants.all())

    @property
    def kicked_users_qs(self):
        return User.objects.filter(ride_participations__ride=self, ride_participations__status=ParticipantStatus.KICKED)

    @property
    def left_users_qs(self):
        return User.objects.filter(ride_participations__ride=self, ride_participations__status=ParticipantStatus.LEFT)

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------

    def sync_status(self, commit: bool = True) -> None:
        """Recalculate OPEN/FULL status based on current headcount."""
        if self.status in ['COMPLETED', 'CANCELLED']:
            return
        self.status = 'FULL' if self.is_full() else 'OPEN'
        if commit and self.pk:
            self.save(update_fields=['status'])

    def clean(self):
        super().clean()
        if hasattr(self, 'creator') and self.creator_id:
            if self.visibility == 'GIRLS' and self.creator.gender != 'F':
                raise ValidationError({'visibility': 'Only female students can create Girls Only rides.'})
            if self.visibility == 'BOYS' and self.creator.gender != 'M':
                raise ValidationError({'visibility': 'Only male students can create Boys Only rides.'})
        if self.route == 'CUSTOM':
            if not self.custom_from_location or not self.custom_from_location.strip():
                raise ValidationError({'custom_from_location': 'Custom route requires a departure location.'})
            if not self.custom_to_location or not self.custom_to_location.strip():
                raise ValidationError({'custom_to_location': 'Custom route requires a destination location.'})

        # Catalogue overload protection: limit active rides to 10 per account
        if hasattr(self, 'creator') and self.creator_id and not self.pk:
            active_count = RideRequest.objects.filter(
                creator_id=self.creator_id,
                status__in=['OPEN', 'FULL'],
            ).count()
            if active_count >= 10:
                raise ValidationError('Ride creation limit reached: you cannot have more than 10 active rides in the catalogue.')

    def save(self, *args, **kwargs):
        if not self.pk:
            if self.status not in ['COMPLETED', 'CANCELLED']:
                self.status = 'OPEN'
        elif self.status not in ['COMPLETED', 'CANCELLED']:
            self.status = 'FULL' if self.is_full() else 'OPEN'
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.creator} — {self.get_route_display()}'


class RideParticipant(models.Model):
    """Membership table: which users are in which ride."""

    ride = models.ForeignKey(
        RideRequest,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='ride_participations',
    )
    status = models.CharField(
        max_length=20,
        choices=ParticipantStatus.choices,
        default=ParticipantStatus.CONFIRMED,
        db_index=True,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    # Folded RideConsent fields (fail-closed defaults)
    consent_version = models.CharField(max_length=20, default='v2.0')
    disclose_ride_identity = models.BooleanField(default=False)
    share_contact_details = models.BooleanField(default=False)
    is_revoked = models.BooleanField(default=False)
    consent_granted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = ConfirmedManager()
    all_rows = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['ride', 'user'],
                name='unique_ride_participant',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in ParticipantStatus]),
                name='ride_participant_status_valid',
            ),
        ]
        indexes = [
            models.Index(fields=['ride', 'status', 'queued_at'], name='idx_rp_ride_status_queued'),
            models.Index(fields=['user', 'status'], name='idx_rp_user_status'),
        ]
        verbose_name = 'Ride Participant'
        verbose_name_plural = 'Ride Participants'

    def clean(self):
        super().clean()
        if self.status in (ParticipantStatus.LEFT, ParticipantStatus.KICKED,
                           ParticipantStatus.DECLINED, ParticipantStatus.CANCELLED):
            return
        if hasattr(self, 'ride') and hasattr(self, 'user'):
            if self.ride.visibility == 'GIRLS' and self.user.gender != 'F':
                raise ValidationError({'user': 'Male students cannot join Girls Only rides.'})
            if self.ride.visibility == 'BOYS' and self.user.gender != 'M':
                raise ValidationError({'user': 'Female students cannot join Boys Only rides.'})

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and hasattr(self, 'ride') and hasattr(self, 'user'):
            try:
                RideConsent.objects.get_or_create(
                    ride=self.ride,
                    user=self.user,
                    defaults={
                        'disclose_ride_identity': True,
                        'share_contact_details': False,
                        'is_revoked': False,
                    }
                )
            except Exception:
                pass

    def __str__(self) -> str:
        return f'{self.user} in {self.ride}'


class RideWaitlist(models.Model):
    """Waitlist / Standby queue for full rides."""

    ride = models.ForeignKey(
        RideRequest,
        on_delete=models.CASCADE,
        related_name='waitlist_entries',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='ride_waitlist_entries',
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['joined_at']
        constraints = [
            models.UniqueConstraint(
                fields=['ride', 'user'],
                name='unique_ride_waitlist',
            )
        ]
        verbose_name = 'Ride Waitlist Entry'
        verbose_name_plural = 'Ride Waitlist Entries'

    def clean(self):
        super().clean()
        if hasattr(self, 'ride') and hasattr(self, 'user'):
            if self.ride.visibility == 'GIRLS' and self.user.gender != 'F':
                raise ValidationError({'user': 'Male students cannot join Girls Only waitlists.'})
            if self.ride.visibility == 'BOYS' and self.user.gender != 'M':
                raise ValidationError({'user': 'Female students cannot join Boys Only waitlists.'})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.user} waitlisted for {self.ride}'


class RideJoinRequest(models.Model):
    """A student's explicit request to join a ride, requiring host approval."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending Host Approval'),
        ('APPROVED', 'Approved by Host'),
        ('DECLINED', 'Declined by Host'),
        ('CANCELLED', 'Cancelled by Student'),
    ]

    ride = models.ForeignKey(RideRequest, on_delete=models.CASCADE, related_name='join_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_join_requests')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['ride', 'user'],
                condition=models.Q(status='PENDING'),
                name='unique_pending_ride_join_request',
            )
        ]
        verbose_name = 'Ride Join Request'
        verbose_name_plural = 'Ride Join Requests'

    def __str__(self) -> str:
        return f'{self.user} request for {self.ride} [{self.status}]'


class RideReview(models.Model):
    """Peer review with multi-parameter ratings and grievance categorization."""

    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    GRIEVANCE_CHOICES = [
        ('NONE', 'No grievances (Smooth coordination / Pleasant trip)'),
        ('LATE', 'Significant Tardiness / Delayed departure'),
        ('NO_SHOW', 'Unannounced No-Show / Ghosted pickup'),
        ('RUDE', 'Disrespectful / Hostile behavior'),
        ('SAFETY', 'Safety concern / Reckless conduct'),
        ('CUSTOM', 'Other / Custom reason'),
    ]

    ride = models.ForeignKey(RideRequest, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_reviews')
    reviewed_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_reviews')
    rating = models.IntegerField(choices=RATING_CHOICES, default=5)
    punctuality_rating = models.IntegerField(choices=RATING_CHOICES, default=5)
    communication_rating = models.IntegerField(choices=RATING_CHOICES, default=5)
    reliability_rating = models.IntegerField(choices=RATING_CHOICES, default=5)
    grievance = models.CharField(max_length=30, choices=GRIEVANCE_CHOICES, default='NONE', db_index=True)
    custom_grievance = models.CharField(max_length=255, blank=True)
    complaint = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['ride', 'reviewer', 'reviewed_user'],
                name='unique_ride_review',
            )
        ]
        verbose_name = 'Ride Review'

    def __str__(self) -> str:
        return f'{self.reviewer} → {self.reviewed_user} (ride {self.ride_id}) [{self.grievance}]'


class RideMessage(models.Model):
    """An in-ride chat message."""

    ride = models.ForeignKey(
        RideRequest,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_messages')
    content = models.TextField(max_length=1000)
    created_at = models.DateTimeField(default=timezone.now)
    read_by = models.ManyToManyField(User, related_name='read_ride_messages', blank=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Ride Message'
        indexes = [
            models.Index(fields=['ride', 'created_at'], name='idx_ridemsg_ride_created'),
        ]

    def clean(self):
        if not self.content or not self.content.strip():
            raise ValidationError({'content': 'Message cannot be empty.'})
        self.content = self.content.strip()

    def __str__(self) -> str:
        return f'{self.author} in ride {self.ride_id}'


class RideIncidentReport(models.Model):
    """Formal incident report filed by a ride participant.
    
    Escalated directly to the Institute Proctorial Board and Dean of Students Welfare.
    Includes an optional snapshot of the in-ride chat transcript as evidentiary log.
    """

    CATEGORY_CHOICES = [
        ('HARASSMENT', 'Harassment or Intimidation'),
        ('INAPPROPRIATE_LANGUAGE', 'Inappropriate or Abusive Language'),
        ('UNSAFE_BEHAVIOR', 'Unsafe Driving or Travel Conduct'),
        ('NO_SHOW_FRAUD', 'Fare Fraud or Intentional No-Show'),
        ('DISCRIMINATION', 'Discrimination or Threat'),
        ('OTHER', 'Other Institute Code of Conduct Violation'),
    ]

    STATUS_CHOICES = [
        ('ESCALATED', 'Escalated to Institute'),
        ('FORWARDED', 'Forwarded to Proctorial Board'),
        ('RESOLVED', 'Inquiry Resolved'),
    ]

    reference_id = models.CharField(max_length=25, unique=True, db_index=True)
    ride = models.ForeignKey(RideRequest, on_delete=models.CASCADE, related_name='incident_reports')
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='filed_incidents')
    reported_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reported_incidents')
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    statement = models.TextField()
    include_chat_log = models.BooleanField(default=True)
    chat_transcript = models.TextField(blank=True)
    escalated_to_institute = models.BooleanField(default=True)
    institute_case_number = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ESCALATED')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Ride Incident Report'
        verbose_name_plural = 'Ride Incident Reports'

    def __str__(self) -> str:
        return f"{self.reference_id} — Ride #{self.ride_id} ({self.get_category_display()})"


class RideConsent(models.Model):
    """Explicit, ride-scoped affirmative consent given by a student to disclose
    identity, departure status, and contact details to fellow confirmed participants."""

    ride = models.ForeignKey(RideRequest, on_delete=models.CASCADE, related_name='consents')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_consents')
    consent_version = models.CharField(max_length=20, default='v2.0')
    disclose_ride_identity = models.BooleanField(default=False)
    share_contact_details = models.BooleanField(default=False)
    is_revoked = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['ride', 'user'],
                name='unique_ride_consent',
            )
        ]
        verbose_name = 'Ride Consent'
        verbose_name_plural = 'Ride Consents'

    def __str__(self) -> str:
        status = "REVOKED" if self.is_revoked else "ACTIVE"
        return f"Consent ({status}) for {self.user.enrollment_number} in Ride #{self.ride_id} ({self.consent_version})"

