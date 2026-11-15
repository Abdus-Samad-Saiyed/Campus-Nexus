from django.contrib import admin
from .models import (
    AccommodationStayLocation,
    AccommodationGroup,
    AccommodationMembership,
    AccommodationMembershipDateChange,
    AccommodationJoinApplication,
    AccommodationApplicationObjection,
    AccommodationGroupOwnershipEvent,
    AccommodationReviewInvitation,
    AccommodationReview,
    AccommodationComplaint,
    AccommodationReputationSnapshot,
)


@admin.register(AccommodationStayLocation)
class AccommodationStayLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'location_within_city', 'is_seeded', 'usage_count', 'google_place_id')
    list_filter = ('city', 'is_seeded')
    search_fields = ('name', 'city', 'location_within_city', 'formatted_address', 'google_place_id')
    ordering = ('-usage_count', 'name')


@admin.register(AccommodationGroup)
class AccommodationGroupAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'destination_city',
        'location_within_city',
        'initiated_by',
        'current_owner',
        'member_limit',
        'admission_mode',
        'recruitment_status',
        'group_status',
        'created_at',
    )
    list_filter = ('destination_city', 'admission_mode', 'recruitment_status', 'group_status')
    search_fields = ('title', 'destination_city', 'location_within_city', 'lodging_name', 'description')
    ordering = ('-created_at',)


@admin.register(AccommodationMembership)
class AccommodationMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'group', 'status', 'planned_stay', 'joined_at', 'left_at', 'departure_type')
    list_filter = ('status', 'departure_type', 'departure_reason_code')
    search_fields = ('user__enrollment_number', 'group__title')
    ordering = ('-joined_at',)


@admin.register(AccommodationMembershipDateChange)
class AccommodationMembershipDateChangeAdmin(admin.ModelAdmin):
    list_display = ('membership', 'changed_by', 'old_period', 'new_period', 'changed_at')
    ordering = ('-changed_at',)


@admin.register(AccommodationJoinApplication)
class AccommodationJoinApplicationAdmin(admin.ModelAdmin):
    list_display = ('applicant', 'group', 'status', 'requested_stay', 'objection_round', 'created_at')
    list_filter = ('status', 'objection_round')
    search_fields = ('applicant__enrollment_number', 'group__title')
    ordering = ('-created_at',)


@admin.register(AccommodationApplicationObjection)
class AccommodationApplicationObjectionAdmin(admin.ModelAdmin):
    list_display = ('application', 'objector_membership', 'objection_round', 'created_at', 'withdrawn_at')
    ordering = ('-created_at',)


@admin.register(AccommodationGroupOwnershipEvent)
class AccommodationGroupOwnershipEventAdmin(admin.ModelAdmin):
    list_display = ('group', 'event_type', 'from_user', 'to_user', 'actor', 'created_at')
    list_filter = ('event_type',)
    ordering = ('-created_at',)


@admin.register(AccommodationReviewInvitation)
class AccommodationReviewInvitationAdmin(admin.ModelAdmin):
    list_display = ('subject_membership', 'reviewer_user', 'status', 'deadline_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('reviewer_user__enrollment_number',)
    ordering = ('-created_at',)


@admin.register(AccommodationReview)
class AccommodationReviewAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'invitation',
        'reliability_score',
        'communication_score',
        'cooperation_score',
        'respect_score',
        'moderation_status',
        'submitted_at',
    )
    list_filter = ('moderation_status',)
    ordering = ('-submitted_at',)


@admin.register(AccommodationComplaint)
class AccommodationComplaintAdmin(admin.ModelAdmin):
    list_display = ('id', 'invitation', 'category', 'status', 'moderator', 'created_at', 'resolved_at')
    list_filter = ('status', 'category')
    search_fields = ('custom_description', 'moderator_notes')
    ordering = ('-created_at',)


@admin.register(AccommodationReputationSnapshot)
class AccommodationReputationSnapshotAdmin(admin.ModelAdmin):
    list_display = ('user', 'composite_score', 'review_count', 'group_count', 'computed_at')
    ordering = ('-composite_score',)
