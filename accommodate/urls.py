from django.urls import path

from accommodate.views import (
    accommodate_landing_view,
    apply_view,
    approve_application_view,
    group_claim_ownership_view,
    group_create_view,
    group_detail_view,
    group_edit_view,
    group_list_view,
    group_manage_view,
    group_search_partial,
    group_toggle_recruitment_view,
    group_transfer_ownership_view,
    leave_group_view,
    my_applications_view,
    my_groups_view,
    my_reviews_view,
    object_application_view,
    reject_application_view,
    remove_member_view,
    skip_review_view,
    submit_complaint_view,
    submit_review_view,
    update_dates_view,
    withdraw_application_view,
    withdraw_objection_view,
)

app_name = 'accommodate'

urlpatterns = [
    # Dedicated Module Landing Page
    path('', accommodate_landing_view, name='landing'),

    # Discovery & Search
    path('groups/', group_list_view, name='group_list'),
    path('search-partial/', group_search_partial, name='group_search_partial'),
    path('create/', group_create_view, name='group_create'),

    # Member Portal & Outgoing Applications
    path('my-groups/', my_groups_view, name='my_groups'),
    path('my-applications/', my_applications_view, name='my_applications'),

    # Reputation & Reviews
    path('reviews/', my_reviews_view, name='my_reviews'),
    path('reviews/<uuid:invitation_id>/submit/', submit_review_view, name='submit_review'),
    path('reviews/<uuid:invitation_id>/skip/', skip_review_view, name='skip_review'),
    path('complaints/submit/', submit_complaint_view, name='submit_complaint'),

    # Group Profiles & Management
    path('groups/<uuid:group_id>/', group_detail_view, name='group_detail'),
    path('groups/<uuid:group_id>/manage/', group_manage_view, name='group_manage'),
    path('groups/<uuid:group_id>/edit/', group_edit_view, name='group_edit'),
    path('groups/<uuid:group_id>/toggle-recruitment/', group_toggle_recruitment_view, name='group_toggle_recruitment'),
    path('groups/<uuid:group_id>/transfer-ownership/', group_transfer_ownership_view, name='group_transfer_ownership'),
    path('groups/<uuid:group_id>/claim-ownership/', group_claim_ownership_view, name='group_claim_ownership'),

    # Admission Workflows & Objections
    path('groups/<uuid:group_id>/apply/', apply_view, name='apply'),
    path('applications/<uuid:application_id>/withdraw/', withdraw_application_view, name='withdraw_application'),
    path('applications/<uuid:application_id>/approve/', approve_application_view, name='approve_application'),
    path('applications/<uuid:application_id>/reject/', reject_application_view, name='reject_application'),
    path('applications/<uuid:application_id>/object/', object_application_view, name='object_application'),
    path('objections/<uuid:objection_id>/withdraw/', withdraw_objection_view, name='withdraw_objection'),

    # Membership Lifecycles
    path('memberships/<uuid:membership_id>/update-dates/', update_dates_view, name='update_dates'),
    path('memberships/<uuid:membership_id>/leave/', leave_group_view, name='leave_group'),
    path('memberships/<uuid:membership_id>/remove/', remove_member_view, name='remove_member'),
]
