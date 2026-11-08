from accounts.views import vapid_public_key_view, webpush_subscribe_view, webpush_unsubscribe_view
"""URL configuration for Campus Nexus REST API (v1)."""

from django.urls import path

from .views import (
    api_catalogue,
    api_login,
    api_logout,
    api_me,
    api_me_photo_upload,
    api_my_rides,
    api_ride_approve_request,
    api_ride_create,
    api_ride_decline_request,
    api_ride_detail,
    api_ride_join,
    api_ride_leave,
    api_ride_list,
    api_recent_notifications,
    api_root,
    api_service_detail,
)

urlpatterns = [
    # API entry point & service directory
    path("", api_root, name="api_root"),
    path("catalogue/", api_catalogue, name="api_catalogue"),
    path("catalogue/<slug:slug>/", api_service_detail, name="api_service_detail"),

    # Auth
    path("auth/login/", api_login, name="api_login"),
    path("auth/logout/", api_logout, name="api_logout"),

    # User profile & stats
    path("me/", api_me, name="api_me"),
    path("me/photo/", api_me_photo_upload, name="api_me_photo_upload"),

    # COORDINATE Rides
    path("rides/", api_ride_list, name="api_ride_list"),
    path("rides/create/", api_ride_create, name="api_ride_create"),
    path("rides/<int:ride_id>/", api_ride_detail, name="api_ride_detail"),
    path("rides/<int:ride_id>/join/", api_ride_join, name="api_ride_join"),
    path("rides/<int:ride_id>/requests/<int:request_id>/approve/", api_ride_approve_request, name="api_ride_approve_request"),
    path("rides/<int:ride_id>/requests/<int:request_id>/decline/", api_ride_decline_request, name="api_ride_decline_request"),
    path("rides/<int:ride_id>/leave/", api_ride_leave, name="api_ride_leave"),
    path("my-rides/", api_my_rides, name="api_my_rides"),
    path("notifications/recent/", api_recent_notifications, name="api_recent_notifications"),
    # Background Web Push
    path("notifications/push/vapid-key/", vapid_public_key_view, name="api_webpush_vapid_key"),
    path("notifications/push/subscribe/", webpush_subscribe_view, name="api_webpush_subscribe"),
    path("notifications/push/unsubscribe/", webpush_unsubscribe_view, name="api_webpush_unsubscribe"),
]
