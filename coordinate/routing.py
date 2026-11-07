"""WebSocket URL routing for the coordinate app."""

from django.urls import re_path

from accounts.consumers import FriendChatConsumer, NotificationConsumer
from campus.consumers import OperationsConsumer, ApplicationsConsumer
from .consumers import RideChatConsumer

websocket_urlpatterns = [
    re_path(r'^ws/notifications/$', NotificationConsumer.as_asgi()),
    re_path(r'^ws/operations/$', OperationsConsumer.as_asgi()),
    re_path(r'^ws/applications/$', ApplicationsConsumer.as_asgi()),
    re_path(r'^ws/ride/(?P<ride_id>\d+)/$', RideChatConsumer.as_asgi()),
    re_path(r'^ws/chat/direct/(?P<friend_enrollment>[\w\d]+)/$', FriendChatConsumer.as_asgi()),
]
