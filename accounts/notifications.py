"""Real-time user notification dispatcher over Django Channels WebSockets & Web Push."""

import json
import logging
import threading
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def dispatch_web_push(user_id: int, payload: dict):
    """Dispatch background Web Push to all registered device subscriptions for the user."""
    try:
        from django.conf import settings
        from accounts.models import WebPushSubscription

        subscriptions = list(WebPushSubscription.objects.filter(user_id=user_id))
        if not subscriptions:
            return

        vapid_private_key = getattr(settings, 'VAPID_PRIVATE_KEY', '')
        vapid_claims = getattr(settings, 'VAPID_CLAIMS', {'sub': 'mailto:admin@campus-nexus.online'})
        if not vapid_private_key:
            return

        def _worker():
            try:
                from pywebpush import webpush, WebPushException
            except ImportError:
                return

            data_str = json.dumps(payload)
            for sub in subscriptions:
                subscription_info = {
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth": sub.auth,
                    }
                }
                try:
                    webpush(
                        subscription_info=subscription_info,
                        data=data_str,
                        vapid_private_key=vapid_private_key,
                        vapid_claims=vapid_claims,
                        ttl=86400,
                    )
                except WebPushException as ex:
                    # 404 or 410 Gone means the subscription has expired or was revoked by browser
                    if ex.response is not None and ex.response.status_code in (404, 410):
                        try:
                            WebPushSubscription.objects.filter(id=sub.id).delete()
                        except Exception:
                            pass
                    else:
                        logger.debug("WebPush delivery failure for sub %s: %s", sub.id, ex)
                except Exception as e:
                    logger.debug("Unexpected WebPush error for sub %s: %s", sub.id, e)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
    except Exception as e:
        logger.debug("Error in dispatch_web_push: %s", e)


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
    """Send a real-time notification push to a specific user via WebSocket and Web Push.

    Delivered to the user's active /ws/notifications/ connection and displayed
    as an interactive toast notification across all open tabs, plus delivered
    as a background native mobile notification via Service Worker Web Push.
    """
    try:
        sender_enrollment = sender_user.enrollment_number if sender_user else ''
        sender_name = (sender_user.get_full_name() or sender_user.enrollment_number) if sender_user else ''
        sender_avatar = ''
        if sender_user and getattr(sender_user, 'profile_photo', None):
            try:
                sender_avatar = sender_user.profile_photo.url
            except Exception:
                sender_avatar = ''

        now_str = timezone.localtime().strftime('%H:%M')

        # 1. In-tab WebSocket delivery
        channel_layer = get_channel_layer()
        if channel_layer:
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

        # 2. Background Native Web Push delivery (reaches user even when app/browser is closed)
        push_payload = {
            'title': title or 'Campus Nexus',
            'body': content or '',
            'url': url or '/dashboard/',
            'icon': sender_avatar or '/static/img/pwa/icon-192x192.png',
            'badge': '/static/img/pwa/icon-192x192.png',
            'tag': f'nexus-{category}',
        }
        dispatch_web_push(user_id, push_payload)

        return True
    except Exception as exc:
        logger.warning("Failed to send user notification to user %s: %s", user_id, exc)
        return False
