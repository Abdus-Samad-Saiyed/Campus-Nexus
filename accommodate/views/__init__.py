"""View controllers for ACCOMMODATE module."""
from .discovery_views import accommodate_landing_view, group_list_view, group_detail_view, group_search_partial
from .management_views import (
    group_create_view,
    group_edit_view,
    group_manage_view,
    group_toggle_recruitment_view,
    group_transfer_ownership_view,
    group_claim_ownership_view,
)
from .application_views import (
    apply_view,
    withdraw_application_view,
    approve_application_view,
    reject_application_view,
    object_application_view,
    withdraw_objection_view,
    my_applications_view,
)
from .membership_views import (
    my_groups_view,
    update_dates_view,
    leave_group_view,
    remove_member_view,
)
from .reputation_views import (
    my_reviews_view,
    submit_review_view,
    skip_review_view,
    submit_complaint_view,
)
