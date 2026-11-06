"""Real-time user notification dispatcher over Django Channels WebSockets."""

import logging
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def send_user_notification(
    user_id: int,
    title: str,
    content: str,
    url: str = '',
    category: str = 'general',
    sender_user=None,
    badge: str = '',
    badge_class: str = 'bg-success-subtle text-success',
    icon: str = 'bi-bell-fill',
) -> bool:
    """Send a real-time notification push to a specific user via WebSocket.

    Delivered to the user's active /ws/notifications/ connection and displayed
    as an interactive toast notification across all open tabs.
    """
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return False

        sender_enrollment = sender_user.enrollment_number if sender_user else ''
        sender_name = (sender_user.get_full_name() or sender_user.enrollment_number) if sender_user else ''
        sender_avatar = ''
        if sender_user and getattr(sender_user, 'profile_photo', None):
            try:
                sender_avatar = sender_user.profile_photo.url
            except Exception:
                sender_avatar = ''

        now_str = timezone.now().strftime('%H:%M')

        async_to_sync(channel_layer.group_send)(
            f'user_notifications_{user_id}',
            {
                'type': 'app_notification',
                'category': category,
                'title': title,
                'content': content,
                'sender': sender_enrollment,
                'sender_name': sender_name,
                'sender_avatar': sender_avatar,
                'url': url,
                'badge': badge or title,
                'badge_class': badge_class,
                'icon': icon,
                'timestamp': now_str,
            }
        )
        return True
    except Exception as exc:
        logger.warning("Failed to send user notification to user %s: %s", user_id, exc)
        return False
