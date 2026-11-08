from django.utils.html import format_html
"""Admin registrations for the accounts app."""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AccountApplication,
    Announcement,
    AnnouncementReceipt,
    AuditEvent,
    Contributor,
    DailyTrafficMetric,
    DirectMessage,
    DirectMessageReport,
    Friendship,
    FriendRequest,
    PasskeyCredential,
    PhoneOTP,
    PlatformFeedback,
    PlatformRuntimeState,
    User,
    UserPlatformSession,
    log_audit_event,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom UserAdmin with administrative hardening.
    Privileges, status, and reputation fields are read-only;
    privilege changes must go through audited multi-admin workflows.
    """

    list_display = [
        'enrollment_number',
        'get_full_name',
        'gender',
        'branch',
        'degree',
        'verified',
        'rides_completed',
        'is_staff',
        'is_superuser',
    ]
    list_filter = ['verified', 'gender', 'branch', 'degree', 'is_staff', 'is_superuser']
    search_fields = ['enrollment_number', 'first_name', 'last_name', 'phone_number']
    ordering = ['enrollment_number']
    readonly_fields = [
        'branch', 'degree', 'joining_year', 'roll_number', 'last_login', 'date_joined',
        'rides_completed', 'on_time_count', 'late_count', 'no_show_count', 'behavior_reports',
        'verified', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions',
    ]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if actions and 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def user_change_password(self, request, id, form_url=''):
        user = self.get_object(request, id)
        response = super().user_change_password(request, id, form_url)
        if request.method == 'POST' and user:
            from django.contrib.sessions.models import Session
            UserPlatformSession.objects.filter(user=user).delete()
            if getattr(user, 'current_session_key', None):
                for k in user.current_session_key.split(','):
                    k = k.strip()
                    if k:
                        Session.objects.filter(session_key=k).delete()
                user.current_session_key = ''
                user.save(update_fields=['current_session_key'])
            log_audit_event(
                event_type='ADMIN_PASSWORD_CHANGE',
                actor_enrollment=getattr(request.user, 'enrollment_number', 'admin'),
                description=f"Admin updated password for {user.enrollment_number}",
                target_id=str(user.enrollment_number),
            )
        return response

    fieldsets = (
        (None, {'fields': ('enrollment_number', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email', 'phone_number', 'gender')}),
        ('Academic info', {'fields': ('branch', 'degree', 'joining_year', 'roll_number')}),
        ('Permissions & Status', {
            'fields': ('verified', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Ride Statistics', {
            'fields': (
                'rides_completed',
                'on_time_count',
                'late_count',
                'no_show_count',
                'behavior_reports',
            ),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'enrollment_number',
                'phone_number',
                'gender',
                'password1',
                'password2',
            ),
        }),
    )


@admin.register(AccountApplication)
class AccountApplicationAdmin(admin.ModelAdmin):
    list_display = ['application_number', 'enrollment_number', 'full_name', 'gender', 'approved', 'created_at']
    list_filter = ['approved', 'gender']
    search_fields = ['enrollment_number', 'full_name', 'application_number']
    readonly_fields = ['application_number', 'created_at']


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ['sender', 'receiver', 'created_at']
    search_fields = ['sender__enrollment_number', 'receiver__enrollment_number']


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ['user1', 'user2', 'created_at']
    search_fields = ['user1__enrollment_number', 'user2__enrollment_number']


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'event_type', 'actor_enrollment', 'target_id', 'description']
    list_filter = ['event_type', 'timestamp']
    search_fields = ['actor_enrollment', 'target_id', 'description']
    readonly_fields = ['timestamp', 'event_type', 'actor_enrollment', 'target_id', 'description']
    ordering = ['-timestamp']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformFeedback)
class PlatformFeedbackAdmin(admin.ModelAdmin):
    list_display = ['title', 'feedback_type', 'user', 'submitter_name', 'status', 'created_at']
    list_filter = ['feedback_type', 'status', 'created_at']
    search_fields = ['title', 'description', 'submitter_name', 'user__enrollment_number']
    readonly_fields = ['created_at']


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    """
    DirectMessage admin registration with privacy safeguards:
    Message content is strictly omitted from changelist and changeform.
    """
    list_display = ['sender', 'recipient', 'is_read', 'read_at', 'created_at']
    list_filter = ['is_read', 'read_at', 'created_at']
    search_fields = ['sender__enrollment_number', 'recipient__enrollment_number']
    readonly_fields = ['sender', 'recipient', 'is_read', 'read_at', 'created_at']
    fields = ['sender', 'recipient', 'is_read', 'read_at', 'created_at']

    def get_actions(self, request):
        actions = super().get_actions(request)
        if actions and 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def has_add_permission(self, request):
        return False


@admin.register(DirectMessageReport)
class DirectMessageReportAdmin(admin.ModelAdmin):
    list_display = [
        'reference_id',
        'reporter',
        'reported_user',
        'reason_category',
        'share_transcript',
        'transcript_timeframe',
        'transcript_message_count',
        'status',
        'created_at',
    ]
    list_filter = ['status', 'reason_category', 'share_transcript', 'transcript_timeframe', 'created_at']
    search_fields = [
        'reference_id',
        'reporter__enrollment_number',
        'reported_user__enrollment_number',
        'statement',
        'transcript_snapshot',
    ]
    readonly_fields = [
        'reference_id',
        'reporter',
        'reported_user',
        'reason_category',
        'statement',
        'share_transcript',
        'transcript_timeframe',
        'transcript_message_count',
        'transcript_snapshot_display',
        'created_at',
        'updated_at',
    ]
    fields = [
        'reference_id',
        'status',
        'reporter',
        'reported_user',
        'reason_category',
        'statement',
        'share_transcript',
        'transcript_timeframe',
        'transcript_message_count',
        'transcript_snapshot_display',
        'admin_notes',
        'created_at',
        'updated_at',
    ]

    def transcript_snapshot_display(self, obj):
        if not obj.share_transcript or not obj.transcript_snapshot:
            return format_html('<span style="color: #999;">No chat transcript was attached with this report.</span>')
        return format_html(
            '<pre style="background: #1a1a1a; color: #e6e6e6; padding: 12px; border-radius: 6px; max-height: 400px; overflow-y: auto; font-family: monospace; white-space: pre-wrap;">{}</pre>',
            obj.transcript_snapshot,
        )
    transcript_snapshot_display.short_description = 'Evidentiary Chat Transcript'


@admin.register(PhoneOTP)
class PhoneOTPAdmin(admin.ModelAdmin):
    list_display = ['user', 'phone_number', 'attempts_left', 'is_used', 'created_at', 'expires_at']
    list_filter = ['is_used', 'created_at']
    search_fields = ['user__enrollment_number', 'phone_number']
    readonly_fields = ['otp_hash', 'created_at']


@admin.register(PasskeyCredential)
class PasskeyCredentialAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'sign_count', 'created_at', 'last_used_at']
    search_fields = ['user__enrollment_number', 'name', 'credential_id']
    readonly_fields = ['credential_id', 'public_key', 'created_at']


@admin.register(Contributor)
class ContributorAdmin(admin.ModelAdmin):
    list_display = ['name', 'role', 'enrollment_number', 'nominated_by', 'is_active', 'created_at']
    list_filter = ['is_active', 'role']
    search_fields = ['name', 'enrollment_number', 'role', 'bio']
    readonly_fields = ['created_at']


@admin.register(DailyTrafficMetric)
class DailyTrafficMetricAdmin(admin.ModelAdmin):
    list_display = ['date', 'total_requests', 'avg_latency_ms', 'peak_rps', 'updated_at']
    ordering = ['-date']
    readonly_fields = ['date', 'total_requests', 'total_latency_ms', 'peak_rps', 'hourly_requests', 'created_at', 'updated_at']


@admin.register(PlatformRuntimeState)
class PlatformRuntimeStateAdmin(admin.ModelAdmin):
    list_display = ['key', 'value_text', 'value_int', 'timestamp', 'updated_at']
    ordering = ['key']
    readonly_fields = ['key', 'value_text', 'value_int', 'timestamp', 'updated_at']


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'priority', 'target_type', 'is_active', 'created_by', 'created_at', 'expires_at']
    list_filter = ['priority', 'target_type', 'is_active', 'created_at']
    search_fields = ['title', 'content', 'target_branches', 'target_years']
    ordering = ['-created_at']
    filter_horizontal = ['target_users']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(AnnouncementReceipt)
class AnnouncementReceiptAdmin(admin.ModelAdmin):
    list_display = ['announcement', 'user', 'seen_at', 'dismissed']
    list_filter = ['dismissed', 'seen_at']
    search_fields = ['user__enrollment_number', 'announcement__title']
    ordering = ['-seen_at']
    readonly_fields = ['seen_at']


@admin.register(UserPlatformSession)
class UserPlatformSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'platform', 'session_key_short', 'ip_address', 'last_activity', 'created_at']
    list_filter = ['platform', 'created_at', 'last_activity']
    search_fields = ['user__enrollment_number', 'user__first_name', 'user__last_name', 'session_key', 'ip_address']
    ordering = ['-last_activity']
    readonly_fields = ['created_at', 'last_activity']

    def session_key_short(self, obj):
        return f"{obj.session_key[:8]}..."
    session_key_short.short_description = "Session Key"


