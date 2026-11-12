"""Real-time user notification dispatcher over Django Channels WebSockets & Web Push."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Bounded thread pool preventing thread exhaustion during chat or activity bursts
_WEBPUSH_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="webpush-worker")


def dispatch_web_push(user_id: int, payload: dict):
    """Dispatch background Web Push to all registered device subscriptions for the user.

    Safe for invocation from both synchronous contexts (Django views) and
    asynchronous contexts (Channels consumers, asyncio event loops) without
    triggering SynchronousOnlyOperation.
    """
    def _worker():
        from django.db import close_old_connections
        close_old_connections()
        try:
            from django.conf import settings
            from accounts.models import WebPushSubscription

            subscriptions = list(WebPushSubscription.objects.filter(user_id=user_id))
            if not subscriptions:
                logger.debug("dispatch_web_push: no subscriptions registered for user_id=%s", user_id)
                return

            vapid_private_key = getattr(settings, 'VAPID_PRIVATE_KEY', '')
            vapid_claims = getattr(settings, 'VAPID_CLAIMS', {'sub': 'mailto:admin@campus-nexus.online'})
            if not vapid_private_key:
                logger.warning("dispatch_web_push: VAPID_PRIVATE_KEY is missing in settings")
                return

            logger.info("Dispatching WebPush to %d device(s) for user_id=%s", len(subscriptions), user_id)

            try:
                from pywebpush import webpush, WebPushException
            except ImportError:
                logger.warning("dispatch_web_push: pywebpush library not installed")
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
                        headers={'Urgency': 'high'},
                    )
                    logger.info("WebPush delivered successfully to sub_id=%s (user_id=%s)", sub.id, user_id)
                except WebPushException as ex:
                    # 404 or 410 Gone means the subscription has expired or was revoked by browser
                    if ex.response is not None and ex.response.status_code in (404, 410):
                        try:
                            WebPushSubscription.objects.filter(id=sub.id).delete()
                            logger.info("Deleted expired/revoked WebPush subscription %s for user %s", sub.id, user_id)
                        except Exception:
                            pass
                    else:
                        logger.warning("WebPush delivery failure for sub %s: %s", sub.id, ex)
                except Exception as e:
                    logger.warning("Unexpected WebPush error for sub %s: %s", sub.id, e)
        except Exception as exc:
            logger.warning("Error in dispatch_web_push worker: %s", exc)
        finally:
            close_old_connections()

    return _WEBPUSH_POOL.submit(_worker)


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
    tag: str = '',
    notif_id: str = '',
) -> bool:
    """Send a real-time notification push to a specific user via WebSocket and Web Push.

    Delivered to the user's active /ws/notifications/ connection and displayed
    as an interactive heads-up banner across open tabs, plus delivered
    as a background native notification via high-urgency Service Worker Web Push.
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
        effective_tag = tag or notif_id or f'nexus-{category}-{int(time.time() * 1000)}'
        effective_id = notif_id or effective_tag
        ws_sent = False
        push_submitted = False

        # 1. In-tab WebSocket delivery (independent error isolation)
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'user_notifications_{user_id}',
                    {
                        'type': 'app_notification',
                        'id': effective_id,
                        'tag': effective_tag,
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
                ws_sent = True
        except Exception as ws_exc:
            logger.debug("WebSocket notification delivery failed for user %s: %s", user_id, ws_exc)

        # 2. Background Native Web Push delivery (reaches user even when app/browser is closed)
        try:
            push_payload = {
                'title': title or 'Campus Nexus',
                'body': content or '',
                'url': url or '/dashboard/',
                'icon': sender_avatar or '/static/img/pwa/icon-192x192.png',
                'badge': '/static/img/pwa/icon-192x192.png',
                'tag': effective_tag,
                'category': category,
            }
            dispatch_web_push(user_id, push_payload)
            push_submitted = True
        except Exception as push_exc:
            logger.warning("WebPush submission failed for user %s: %s", user_id, push_exc)

        return ws_sent or push_submitted
    except Exception as exc:
        logger.warning("Failed to send user notification to user %s: %s", user_id, exc)
        return False
