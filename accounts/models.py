"""
User model and social models for COORDINATE.
"""

from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


ENROLLMENT_VALIDATOR = RegexValidator(
    regex=r'^(?:(?:\d{4}|20XX)[BMP][A-Z]{3}\d{3}|GUEST_DEMO)$',
    message='Invalid enrollment number. Expected format: 2024BCSE001, 20XXBCSE001, or GUEST_DEMO',
)

_DEGREE_MAP = {'B': 'Bachelors', 'M': 'Masters', 'P': 'PhD'}


class User(AbstractUser):
    """
    Custom user model where enrollment_number is the login credential.

    Enrollment format: 2024BCSE001 or 20XXBCSE001
      [0:4]  → joining year   (2024 or 20XX)
      [4]    → degree code    (B/M/P)
      [5:8]  → branch code    (CSE, ECE, …)
      [8:11] → roll number    (001)
    """

    enrollment_number = models.CharField(
        max_length=11,
        unique=True,
        validators=[ENROLLMENT_VALIDATOR],
        db_index=True,
    )
    phone_number = models.CharField(max_length=15)

    # Auto-derived from enrollment_number on save
    branch = models.CharField(max_length=10, blank=True)
    degree = models.CharField(max_length=20, blank=True)
    joining_year = models.IntegerField(null=True, blank=True)
    roll_number = models.CharField(max_length=10, blank=True)

    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female')]
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)

    verified = models.BooleanField(default=False)
    profile_photo = models.ImageField(upload_to='profiles/', blank=True, null=True)
    bio = models.TextField(max_length=500, blank=True)
    show_friends_publicly = models.BooleanField(default=True)
    hide_online_status = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True, default=timezone.now)
    hide_from_search = models.BooleanField(default=False)
    hide_profile_photo = models.BooleanField(default=False)
    show_contact_on_profile = models.BooleanField(default=True)
    dm_read_receipts = models.BooleanField(default=True)
    two_factor_enabled = models.BooleanField(default=False)

    # Single active session security key (HMAC digest or raw key)
    current_session_key = models.CharField(max_length=64, blank=True, default='')

    # Soft deletion / Account closed state
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Ride reliability stats
    @property
    def is_guest_demo(self) -> bool:
        return self.username == 'GUEST_DEMO' or self.enrollment_number == 'GUEST_DEMO'

    @property
    def is_online_now(self) -> bool:
        if self.hide_online_status:
            return False
        dt = self.last_seen or getattr(self, 'date_joined', None)
        if not dt:
            return False
        from django.utils import timezone
        return (timezone.now() - dt).total_seconds() < 180

    @property
    def last_seen_display(self) -> str:
        if self.hide_online_status:
            return "Hidden"
        dt = self.last_seen or getattr(self, 'date_joined', None)
        if not dt:
            return "Recently"
        import zoneinfo, datetime
        from django.utils import timezone
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        local_dt = timezone.localtime(dt, ist_tz)
        now_local = timezone.localtime(timezone.now(), ist_tz)
        diff = (now_local - local_dt).total_seconds()
        if diff < 180:
            return "Active now"
        elif local_dt.date() == now_local.date():
            return f"Today at {local_dt.strftime('%I:%M %p')} IST"
        elif local_dt.date() == (now_local.date() - datetime.timedelta(days=1)):
            return f"Yesterday at {local_dt.strftime('%I:%M %p')} IST"
        else:
            return local_dt.strftime("%b %d, %Y at %I:%M %p IST")

    rides_completed = models.IntegerField(default=0)
    on_time_count = models.IntegerField(default=0)
    late_count = models.IntegerField(default=0)
    no_show_count = models.IntegerField(default=0)
    behavior_reports = models.IntegerField(default=0)

    USERNAME_FIELD = 'enrollment_number'
    REQUIRED_FIELDS = ['username', 'gender', 'phone_number', 'email']

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initial_profile_photo_name = self.profile_photo.name if (self.pk and self.profile_photo) else ''

    def clean(self):
        super().clean()
        if self.first_name:
            from accounts.two_factor import clean_person_name
            self.first_name = clean_person_name(self.first_name)
        if self.last_name:
            from accounts.two_factor import clean_person_name
            self.last_name = clean_person_name(self.last_name)
        if self.phone_number:
            from accounts.two_factor import normalize_phone_number
            self.phone_number = normalize_phone_number(self.phone_number)

    def save(self, *args, **kwargs):
        if self.first_name:
            from accounts.two_factor import clean_person_name
            self.first_name = clean_person_name(self.first_name)
        if self.last_name:
            from accounts.two_factor import clean_person_name
            self.last_name = clean_person_name(self.last_name)
        if self.phone_number:
            from accounts.two_factor import normalize_phone_number
            self.phone_number = normalize_phone_number(self.phone_number)
        if self.email:
            self.email = self.email.strip().lower()
        if self.enrollment_number:
            e = self.enrollment_number.upper()
            self.enrollment_number = e
            if e == 'GUEST_DEMO':
                self.joining_year = 2099
                self.degree = 'Guest'
                self.branch = 'DEMO'
                self.roll_number = '000'
            else:
                year_part = e[:4]
                self.joining_year = int(year_part) if year_part.isdigit() else 2099
                self.degree = _DEGREE_MAP.get(e[4], 'Unknown') if len(e) > 4 else 'Unknown'
                self.branch = e[5:8] if len(e) >= 8 else ''
                self.roll_number = e[8:] if len(e) > 8 else ''

        update_fields = kwargs.get('update_fields')
        old_photo_to_delete = None
        if self.pk and (update_fields is None or 'profile_photo' in update_fields):
            current_photo_name = self.profile_photo.name if self.profile_photo else ''
            if self._initial_profile_photo_name and self._initial_profile_photo_name != current_photo_name:
                old_photo_to_delete = self._initial_profile_photo_name

        super().save(*args, **kwargs)
        self._initial_profile_photo_name = self.profile_photo.name if self.profile_photo else ''

        if old_photo_to_delete:
            def _delete_old_file(name=old_photo_to_delete, storage=self.profile_photo.storage):
                try:
                    if not User.objects.filter(profile_photo=name).exists():
                        storage.delete(name)
                except Exception:
                    pass
            from django.db import transaction
            transaction.on_commit(_delete_old_file)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_debug_account(self) -> bool:
        """Synthetic debug accounts begin with '20XX' and are active only in debug/test/alpha environments."""
        from accounts.utils import is_debug_allowed, is_debug_enrollment
        return bool(is_debug_allowed() and is_debug_enrollment(self.enrollment_number))

    @property
    def on_time_percentage(self) -> float:
        total = self.on_time_count + self.late_count + self.no_show_count
        if total == 0:
            return 100.0
        return round((self.on_time_count / total) * 100, 1)

    def can_view_phone_number(self, viewer) -> bool:
        """
        Explicit phone number privacy policy:
        Only visible to:
          - Self
          - Staff / superusers
          - Connected friends when target user enables contact visibility
        """
        if not viewer or not viewer.is_authenticated or not getattr(viewer, 'is_active', False):
            return False
        if viewer == self or viewer.is_staff or viewer.is_superuser:
            return True
        if not getattr(viewer, 'verified', False) or getattr(viewer, 'is_deleted', False):
            return False
        if not getattr(self, 'show_contact_on_profile', True):
            return False
        return Friendship.objects.filter(
            models.Q(user1=viewer, user2=self) | models.Q(user1=self, user2=viewer)
        ).exists()

    @property
    def display_name(self) -> str:
        name = self.get_full_name() or self.enrollment_number
        if getattr(self, 'is_deleted', False):
            return f"{name} (Closed Account)"
        return name

    def __str__(self) -> str:
        return self.enrollment_number


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class AccountApplication(models.Model):
    """
    A prospective student's application for a verified account.
    Admin reviews these and calls approve_application() to create the User.
    """

    enrollment_number = models.CharField(max_length=11, unique=True)
    full_name = models.CharField(max_length=255)
    email = models.EmailField(max_length=254, default='')
    phone_number = models.CharField(max_length=15)
    gender = models.CharField(max_length=1, choices=User.GENDER_CHOICES)
    password = models.CharField(max_length=255)          # stored hashed
    application_number = models.CharField(max_length=20, unique=True)
    phone_verified = models.BooleanField(default=False)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Account Application'
        verbose_name_plural = 'Account Applications'

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        if self.enrollment_number and self.enrollment_number.strip().upper() == 'GUEST_DEMO':
            raise ValidationError('Applications cannot be submitted for GUEST_DEMO.')
        if self.full_name:
            from accounts.two_factor import clean_person_name
            self.full_name = clean_person_name(self.full_name)
        if self.phone_number:
            from accounts.two_factor import normalize_phone_number
            self.phone_number = normalize_phone_number(self.phone_number)

    def save(self, *args, **kwargs):
        if self.enrollment_number:
            self.enrollment_number = self.enrollment_number.strip().upper()
            if self.enrollment_number == 'GUEST_DEMO':
                raise ValueError('Applications cannot be submitted for GUEST_DEMO.')
        if self.full_name:
            from accounts.two_factor import clean_person_name
            self.full_name = clean_person_name(self.full_name)
        if self.phone_number:
            from accounts.two_factor import normalize_phone_number
            self.phone_number = normalize_phone_number(self.phone_number)
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.application_number} — {self.enrollment_number}'


# ---------------------------------------------------------------------------
# Social graph
# ---------------------------------------------------------------------------

class FriendRequest(models.Model):
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_friend_requests',
    )
    receiver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_friend_requests',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['sender', 'receiver'],
                name='unique_friend_request',
            ),
            models.CheckConstraint(
                condition=~models.Q(sender=models.F('receiver')),
                name='no_self_friend_request',
            ),
        ]
        verbose_name = 'Friend Request'

    def __str__(self) -> str:
        return f'{self.sender} → {self.receiver}'


class Friendship(models.Model):
    user1 = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='friendships_as_user1',
    )
    user2 = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='friendships_as_user2',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user1', 'user2'],
                name='unique_friendship',
            ),
            models.CheckConstraint(
                condition=~models.Q(user1=models.F('user2')),
                name='no_self_friendship',
            ),
        ]
        verbose_name = 'Friendship'

    def save(self, *args, **kwargs):
        if self.user1_id and self.user2_id and self.user1_id > self.user2_id:
            self.user1, self.user2 = self.user2, self.user1
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.user1} ↔ {self.user2}'


class DirectMessage(models.Model):
    """1-on-1 direct message between friends."""

    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_direct_messages',
    )
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_direct_messages',
    )
    content = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    @property
    def read_at_display(self) -> str:
        if not self.read_at:
            return ""
        import zoneinfo, datetime
        from django.utils import timezone
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        local_dt = timezone.localtime(self.read_at, ist_tz)
        now_local = timezone.localtime(timezone.now(), ist_tz)
        if local_dt.date() == now_local.date():
            return f"Today at {local_dt.strftime('%I:%M %p')} IST"
        elif local_dt.date() == (now_local.date() - datetime.timedelta(days=1)):
            return f"Yesterday at {local_dt.strftime('%I:%M %p')} IST"
        else:
            return local_dt.strftime("%b %d, %Y at %I:%M %p IST")

    @property
    def read_at_time(self) -> str:
        if not self.read_at:
            return ""
        import zoneinfo
        from django.utils import timezone
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        local_dt = timezone.localtime(self.read_at, ist_tz)
        now_local = timezone.localtime(timezone.now(), ist_tz)
        if local_dt.date() == now_local.date():
            return local_dt.strftime("%H:%M")
        return local_dt.strftime("%b %d, %H:%M")

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Direct Message'
        verbose_name_plural = 'Direct Messages'
        indexes = [
            models.Index(fields=['sender', 'recipient', 'created_at'], name='idx_dm_sender_rcpt_created'),
            models.Index(fields=['recipient', 'is_read', 'sender'], name='idx_dm_unread_by_sender'),
        ]

    def clean(self):
        super().clean()
        if not self.content or not self.content.strip():
            from django.core.exceptions import ValidationError
            raise ValidationError({'content': 'Message content cannot be blank.'})
        self.content = self.content.strip()

    def __str__(self) -> str:
        return f'{self.sender} → {self.recipient}: {self.content[:30]}'


class DirectMessageReport(models.Model):
    """Formal user report filed against another user in direct messages.

    Includes optional evidentiary snapshot of the direct chat transcript
    for the selected timeframe (past 15 minutes, 1 hour, 1 day, or lifetime).
    """

    TIMEFRAME_CHOICES = [
        ('15_MINUTES', 'Past 15 minutes'),
        ('1_HOUR', 'Past 1 hour'),
        ('1_DAY', 'Past 1 day (24 hours)'),
        ('LIFETIME', 'Lifetime (Full Conversation)'),
    ]

    CATEGORY_CHOICES = [
        ('HARASSMENT', 'Harassment, Bullying, or Intimidation'),
        ('INAPPROPRIATE_LANGUAGE', 'Inappropriate, Abusive, or Explicit Language'),
        ('SPAM_SCAM', 'Spam, Solicitation, or Fraud'),
        ('THREAT_SAFETY', 'Threat, Hate Speech, or Safety Concern'),
        ('OTHER', 'Other Violation of Student Conduct'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending Review'),
        ('INVESTIGATING', 'Under Investigation'),
        ('RESOLVED', 'Resolved'),
        ('DISMISSED', 'Dismissed'),
    ]

    reference_id = models.CharField(max_length=30, unique=True, db_index=True)
    reporter = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='filed_dm_reports',
    )
    reported_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_dm_reports',
    )
    reason_category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    statement = models.TextField(blank=True)
    share_transcript = models.BooleanField(default=False)
    transcript_timeframe = models.CharField(
        max_length=20,
        choices=TIMEFRAME_CHOICES,
        blank=True,
        default='',
    )
    transcript_snapshot = models.TextField(blank=True)
    transcript_message_count = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    admin_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Direct Message Report'
        verbose_name_plural = 'Direct Message Reports'

    def __str__(self) -> str:
        return f"{self.reference_id} — {self.reporter} reported {self.reported_user} ({self.get_reason_category_display()})"


class PhoneOTP(models.Model):
    """Cryptographically protected single-use OTP for 2FA, Signup, and Security changes."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='phone_otps')
    purpose = models.CharField(max_length=30, default='2FA')  # 2FA, SIGNUP, SECURITY_CHANGE, PASSWORD_RESET
    otp_hash = models.CharField(max_length=128)
    phone_number = models.CharField(max_length=20)
    email = models.EmailField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts_left = models.IntegerField(default=3)
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Phone OTP'
        verbose_name_plural = 'Phone OTPs'
        indexes = [
            models.Index(fields=['user', 'purpose', 'is_used', '-created_at'], name='idx_otp_user_lookup'),
            models.Index(fields=['phone_number', 'purpose', 'is_used', '-created_at'], name='idx_otp_phone_lookup'),
        ]

    def save(self, *args, **kwargs):
        if self.phone_number:
            from accounts.two_factor import normalize_phone_number
            self.phone_number = normalize_phone_number(self.phone_number)
        super().save(*args, **kwargs)

    def is_valid(self) -> bool:
        return (not self.is_used) and (self.attempts_left > 0) and (timezone.now() < self.expires_at)

    def __str__(self) -> str:
        ident = self.user.enrollment_number if self.user else 'Anonymous'
        return f'OTP ({self.purpose}) for {ident} ({self.phone_number})'


class PhysicalVerificationRequest(models.Model):
    """In-person physical verification request for forgotten password or lost phone/SIM."""

    STATUS_CHOICES = [
        ('PENDING', 'Pending In-Person Verification'),
        ('VERIFIED', 'Physically Verified & Resolved'),
        ('REJECTED', 'Rejected'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='physical_verification_requests')
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    student_notes = models.TextField(blank=True, max_length=500)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_verifications')
    resolution_notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Physical Verification Request'
        verbose_name_plural = 'Physical Verification Requests'

    def __str__(self) -> str:
        return f"{self.user.enrollment_number} — {self.status}"


class PasskeyCredential(models.Model):
    """FIDO2 / WebAuthn Biometric & Windows Hello Passkey Credential."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passkey_credentials')
    credential_id = models.CharField(max_length=512, unique=True, db_index=True)
    public_key = models.TextField()
    sign_count = models.IntegerField(default=0)
    name = models.CharField(max_length=100, default='Windows Hello / Biometric Key')
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Passkey Credential'
        verbose_name_plural = 'Passkey Credentials'

    @property
    def device_icon(self) -> str:
        """Bootstrap icon class based on device of origin."""
        name_lower = (self.name or '').lower()
        if 'windows' in name_lower:
            return 'bi-windows text-info'
        elif any(k in name_lower for k in ['apple', 'mac', 'iphone', 'ipad', 'ios']):
            return 'bi-apple text-light'
        elif 'android' in name_lower:
            return 'bi-android2 text-success'
        elif 'linux' in name_lower or 'ubuntu' in name_lower:
            return 'bi-ubuntu text-danger'
        elif 'chromebook' in name_lower or 'cros' in name_lower:
            return 'bi-laptop text-primary'
        return 'bi-fingerprint text-warning'

    def __str__(self) -> str:
        return f'{self.name} — {self.user.enrollment_number}'


class ImmutableAuditQuerySet(models.QuerySet):
    def update(self, *args, **kwargs):
        raise PermissionError("AuditEvent records are append-only and cannot be updated in bulk.")

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditEvent records are append-only and cannot be deleted in bulk.")


class AuditEvent(models.Model):
    """Minimal, privacy-preserving operational audit log."""

    EVENT_CHOICES = [
        ('LOGIN_SUCCESS', 'Login Success'),
        ('LOGIN_FAILURE', 'Login Failure'),
        ('APP_SUBMIT', 'Application Submitted'),
        ('APP_APPROVED', 'Application Approved'),
        ('APP_REJECTED', 'Application Rejected'),
        ('SUPERUSER_PROMOTED', 'Superuser Promoted'),
        ('SUPERUSER_REVOKED', 'Superuser Revoked'),
        ('ADMIN_PHONE_UPDATE', 'Admin Updated Phone Number'),
        ('ACCOUNT_DELETED', 'Account Closed / Deleted by Admin'),
        ('PHYSICAL_VERIFICATION_REQUESTED', 'Physical In-Person Verification Filed'),
        ('PHYSICAL_VERIFICATION_RESOLVED', 'Physical In-Person Verification Approved'),
        ('PHYSICAL_VERIFICATION_REJECTED', 'Physical In-Person Verification Rejected'),
        ('SECURITY_SETTINGS_UPDATED', 'Security Credentials Updated'),
        ('PASSKEY_REGISTERED', 'Passkey Registered'),
        ('CONTRIBUTOR_NOMINATED', 'Contributor Nominated'),
        ('CONTRIBUTOR_REMOVED', 'Contributor Removed'),
        ('CONSENT_GRANTED', 'Consent Granted'),
        ('CONSENT_REVOKED', 'Consent Revoked'),
        ('SESSION_TERMINATED_CONCURRENT', 'Session Terminated on Concurrent Login'),
        ('RIDE_CREATED', 'Ride Created'),
        ('RIDE_JOINED', 'Ride Joined'),
        ('RIDE_LEFT', 'Ride Left'),
        ('RIDE_COMPLETED', 'Ride Completed'),
        ('RIDE_CANCELLED', 'Ride Cancelled'),
        ('INCIDENT_ESCALATED', 'Incident Escalated to Institute'),
        ('FEEDBACK_SUBMITTED', 'Platform Feedback Submitted'),
        ('EXPORT_GENERATED', 'Export Generated'),
        ('ANNOUNCEMENT_CREATED', 'Admin Issued Announcement'),
        ('ANNOUNCEMENT_DELETED', 'Admin Deleted Announcement'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES, db_index=True)
    actor_enrollment = models.CharField(max_length=20, blank=True)
    description = models.CharField(max_length=255)
    target_id = models.CharField(max_length=50, blank=True)

    objects = ImmutableAuditQuerySet.as_manager()

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Audit Event'
        verbose_name_plural = 'Audit Events'

    def __str__(self) -> str:
        return f"[{timezone.localtime(self.timestamp).strftime('%Y-%m-%d %H:%M')}] {self.event_type}: {self.description}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit events are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.get('force_insert', False):
            raise PermissionError("Audit events are immutable and cannot be modified.")
        super().save(*args, **kwargs)


class PlatformFeedback(models.Model):
    """User feedback, feature requests, proposals, and bug reports."""

    FEEDBACK_TYPES = [
        ('BUG', 'Bug Report'),
        ('FEATURE', 'Feature Request'),
        ('PROPOSAL', 'Project / Service Proposal'),
        ('GENERAL', 'General Feedback'),
    ]

    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('IN_REVIEW', 'In Review'),
        ('RESOLVED', 'Resolved'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='feedbacks')
    submitter_name = models.CharField(max_length=100, blank=True)
    submitter_contact = models.CharField(max_length=100, blank=True)
    feedback_type = models.CharField(max_length=20, choices=FEEDBACK_TYPES, default='GENERAL')
    title = models.CharField(max_length=200)
    description = models.TextField()
    system_info = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Platform Feedback'
        verbose_name_plural = 'Platform Feedback'

    def __str__(self) -> str:
        return f"[{self.get_feedback_type_display()}] {self.title}"


class Contributor(models.Model):
    """Platform contributor and maintainer profile with peer nomination tracking."""

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='contributor_profiles')
    name = models.CharField(max_length=100)
    enrollment_number = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=100, default='Core Contributor')
    github_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    bio = models.CharField(max_length=255, blank=True)
    nominated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='nominated_contributors')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Contributor'
        verbose_name_plural = 'Contributors'

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


class DailyTrafficMetric(models.Model):
    """Durable operational telemetry record for traffic, throughput, and latency.

    Provides lasting historical metrics that survive server restarts, worker cycling,
    and process crashes, updated via high-performance write-behind batching.
    """
    date = models.DateField(unique=True, db_index=True)
    total_requests = models.PositiveBigIntegerField(default=0)
    total_latency_ms = models.FloatField(default=0.0)
    peak_rps = models.PositiveIntegerField(default=0)
    hourly_requests = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Daily Traffic Metric'
        verbose_name_plural = 'Daily Traffic Metrics'

    def __str__(self) -> str:
        return f"{self.date}: {self.total_requests:,} requests (Peak: {self.peak_rps} RPS)"

    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests > 0:
            return round(self.total_latency_ms / self.total_requests, 1)
        return 0.0


class PlatformRuntimeState(models.Model):
    """Durable platform runtime indicators (persistent uptime, server initialization, heartbeats)."""
    key = models.CharField(max_length=64, unique=True, db_index=True)
    value_text = models.CharField(max_length=255, blank=True)
    value_int = models.BigIntegerField(default=0)
    timestamp = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Platform Runtime State'
        verbose_name_plural = 'Platform Runtime States'

    def __str__(self) -> str:
        return f"{self.key}: {self.value_text or self.value_int} ({self.timestamp})"


class Announcement(models.Model):
    """Platform administrative announcement with targeting and first-login proclamation."""

    PRIORITY_CHOICES = [
        ('INFO', 'Informational Notice'),
        ('WARNING', 'Important Warning'),
        ('CRITICAL', 'Urgent / Critical Alert'),
        ('SUCCESS', 'Platform Update / Good News'),
    ]

    TARGET_TYPE_CHOICES = [
        ('ALL', 'All Platform Users'),
        ('SET', 'Preset User Group'),
        ('FILTERED', 'Filtered Audience Criteria'),
        ('INDIVIDUAL', 'Specific Individual Users'),
    ]

    TARGET_SET_CHOICES = [
        ('STUDENTS', 'All Students (Non-Staff)'),
        ('STAFF', 'Institute Staff & Superusers'),
        ('VERIFIED', 'Verified Students Only'),
        ('UNVERIFIED', 'Unverified Students Only'),
    ]

    title = models.CharField(max_length=200)
    content = models.TextField(help_text="Announcement message body")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='INFO', db_index=True)

    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES, default='ALL', db_index=True)
    target_set = models.CharField(max_length=30, choices=TARGET_SET_CHOICES, blank=True, null=True)

    # Filtered audience attributes
    target_branches = models.CharField(max_length=255, blank=True, default='', help_text="Comma-separated branch codes e.g. CSE,ECE (empty for any)")
    target_years = models.CharField(max_length=255, blank=True, default='', help_text="Comma-separated joining years e.g. 2024,2023 (empty for any)")
    target_degrees = models.CharField(max_length=255, blank=True, default='', help_text="Comma-separated degree types e.g. B.Tech,M.Tech (empty for any)")
    target_gender = models.CharField(max_length=10, blank=True, default='', help_text="Target gender e.g. M, F, or empty for any")

    # Specific individual user targets
    target_users = models.ManyToManyField(
        'User',
        blank=True,
        related_name='targeted_announcements',
        help_text="Explicitly chosen target users"
    )

    # Optional Action Link
    action_url = models.CharField(max_length=255, blank=True, default='', help_text="Optional destination link e.g. /rides/ or external URL")
    action_label = models.CharField(max_length=60, blank=True, default='', help_text="Button text e.g. View Details")

    created_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='authored_announcements'
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Optional expiration timestamp")

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Announcement'
        verbose_name_plural = 'Announcements'

    def __str__(self) -> str:
        return f"[{self.priority}] {self.title}"

    def is_user_targeted(self, user) -> bool:
        """Evaluate whether a given user is in the audience for this announcement."""
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False

        # In guest demo mode, allow seeing ALL and SET announcements for preview
        if getattr(user, 'is_guest_demo', False):
            return self.target_type in ('ALL', 'SET')

        if self.target_type == 'ALL':
            return True

        if self.target_type == 'SET':
            if self.target_set == 'STUDENTS':
                return not (user.is_staff or user.is_superuser)
            elif self.target_set == 'STAFF':
                return user.is_staff or user.is_superuser
            elif self.target_set == 'VERIFIED':
                return getattr(user, 'verified', False)
            elif self.target_set == 'UNVERIFIED':
                return not getattr(user, 'verified', False) and not (user.is_staff or user.is_superuser)
            return False

        if self.target_type == 'INDIVIDUAL':
            return self.target_users.filter(id=user.id).exists()

        if self.target_type == 'FILTERED':
            if self.target_branches:
                allowed_branches = [b.strip().upper() for b in self.target_branches.split(',') if b.strip()]
                if (user.branch or '').strip().upper() not in allowed_branches:
                    return False
            if self.target_years:
                allowed_years = [y.strip() for y in self.target_years.split(',') if y.strip()]
                if str(user.joining_year or '') not in allowed_years:
                    return False
            if self.target_degrees:
                DEGREE_ALIASES = {
                    'B': {'B', 'BTECH', 'B.TECH', 'BACHELORS', 'BACHELOR'},
                    'M': {'M', 'MTECH', 'M.TECH', 'MASTERS', 'MASTER', 'MSC', 'M.SC', 'MBA'},
                    'P': {'P', 'PHD', 'PH.D', 'DOCTORATE'},
                }
                user_deg = (user.degree or '').strip().upper()
                allowed_degrees = [d.strip().upper() for d in self.target_degrees.split(',') if d.strip()]
                matched = user_deg in allowed_degrees
                if not matched:
                    for code, aliases in DEGREE_ALIASES.items():
                        if user_deg in aliases or (len(getattr(user, 'enrollment_number', '') or '') > 4 and user.enrollment_number[4].upper() == code):
                            if any(ad in aliases for ad in allowed_degrees):
                                matched = True
                                break
                if not matched:
                    return False
            if self.target_gender:
                if (user.gender or '').strip().upper() != self.target_gender.strip().upper():
                    return False
            return True

        return False

    @classmethod
    def get_active_for_user(cls, user) -> list:
        """Fetch all active, non-expired announcements matching user targeting criteria."""
        now = timezone.now()
        qs = cls.objects.filter(is_active=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).prefetch_related('target_users').order_by('-created_at')

        return [ann for ann in qs if ann.is_user_targeted(user)]


class AnnouncementReceipt(models.Model):
    """Audit receipt tracking when a user first saw and acknowledged an announcement."""

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='receipts')
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='announcement_receipts')
    seen_at = models.DateTimeField(auto_now_add=True)
    dismissed = models.BooleanField(default=False)

    class Meta:
        unique_together = ('announcement', 'user')
        indexes = [
            models.Index(fields=['user', 'announcement']),
        ]
        verbose_name = 'Announcement Receipt'
        verbose_name_plural = 'Announcement Receipts'

    def __str__(self) -> str:
        return f"{self.user.enrollment_number} acknowledged #{self.announcement_id}"


def log_audit_event(
    event_type: str,
    actor_enrollment: str = '',
    description: str = '',
    target_id: str = '',
    fail_silently: bool = True,
):
    """Log an operational audit event without collecting sensitive payloads."""
    try:
        evt = AuditEvent.objects.create(
            event_type=event_type,
            actor_enrollment=str(actor_enrollment)[:20],
            description=str(description)[:255],
            target_id=str(target_id)[:50],
        )
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            import zoneinfo
            layer = get_channel_layer()
            if layer:
                ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                ts_str = timezone.localtime(evt.timestamp, ist_tz).strftime("%b %d %H:%M:%S")
                async_to_sync(layer.group_send)(
                    "staff_operations",
                    {
                        "type": "audit_event_broadcast",
                        "id": evt.id,
                        "timestamp": ts_str,
                        "event_type": evt.event_type,
                        "actor": evt.actor_enrollment or "SYSTEM",
                        "details": evt.description,
                    }
                )
        except Exception:
            pass
        return evt
    except Exception as e:
        if not fail_silently:
            raise
        return None


from django.db.models.signals import post_delete
from django.dispatch import receiver

@receiver(post_delete, sender=User)
def cleanup_user_profile_photo_on_delete(sender, instance, **kwargs):
    """Ensure profile photo file is removed from storage when User is deleted."""
    if instance.profile_photo and instance.profile_photo.name:
        try:
            instance.profile_photo.storage.delete(instance.profile_photo.name)
        except Exception:
            pass




# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver

@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    if user and hasattr(user, 'current_session_key') and getattr(user, 'pk', None):
        user.current_session_key = ''
        user.save(update_fields=['current_session_key'])


from django.db.models.signals import pre_delete, post_delete
from django.core.exceptions import PermissionDenied

@receiver(pre_delete, sender=User)
def prevent_last_superuser_deletion(sender, instance, **kwargs):
    """Ensure the system cannot delete the sole remaining active superuser."""
    if instance.is_superuser and instance.is_active:
        remaining_superusers = User.objects.filter(is_superuser=True, is_active=True).exclude(pk=instance.pk).count()
        if remaining_superusers == 0:
            raise PermissionDenied("Cannot delete the last remaining active superuser.")
