from django.urls import path

from .views import (
    api_application_approve,
    api_application_reject,
    api_application_withdraw,
    api_cities,
    api_group_apply,
    api_group_detail_update,
    api_group_inbox,
    api_groups_list_create,
    api_membership_dates,
    api_membership_leave,
    api_popular_stays,
    api_reputation_detail,
)

app_name = "accommodate_api"

urlpatterns = [
    # Reference & Catalog
    path("cities/", api_cities, name="api_cities"),
    path("stays/popular/", api_popular_stays, name="api_popular_stays"),

    # Groups
    path("groups/", api_groups_list_create, name="api_groups_list_create"),
    path("groups/<uuid:group_id>/", api_group_detail_update, name="api_group_detail_update"),
    path("groups/<uuid:group_id>/apply/", api_group_apply, name="api_group_apply"),
    path("groups/<uuid:group_id>/inbox/", api_group_inbox, name="api_group_inbox"),

    # Applications
    path("applications/<uuid:application_id>/approve/", api_application_approve, name="api_application_approve"),
    path("applications/<uuid:application_id>/reject/", api_application_reject, name="api_application_reject"),
    path("applications/<uuid:application_id>/withdraw/", api_application_withdraw, name="api_application_withdraw"),

    # Memberships
    path("memberships/<uuid:membership_id>/leave/", api_membership_leave, name="api_membership_leave"),
    path("memberships/<uuid:membership_id>/dates/", api_membership_dates, name="api_membership_dates"),

    # Reputation Snapshot
    path("reputation/<str:enrollment>/", api_reputation_detail, name="api_reputation_detail"),
]
