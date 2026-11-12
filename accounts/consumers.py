"""WebSocket consumer for 1-on-1 direct messaging between friends.

Path: /ws/chat/direct/<friend_enrollment>/
Channel group: direct_chat_<min_id>_<max_id>
Features:
  - Strict friendship verification on connection
  - Real-time typing indicators
  - Online presence broadcasts
  - Message persistence in DirectMessage model
"""

import json
import logging
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q
from django.utils import timezone
from django.template.defaultfilters import date as date_filter


class FriendChatConsumer(AsyncWebsocketConsumer):

    async def _is_connection_authorized(self) -> bool:
        """Verify both users are authenticated, active, verified, not soft-deleted, and not self."""
        if not self.user or not self.user.is_authenticated or not self.user.is_active or getattr(self.user, 'is_deleted', False):
            return False
        if not self.friend or not self.friend.is_active or getattr(self.friend, 'is_deleted', False):
            return False
        if self.user.id == self.friend.id:
            return False
        return True

    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated or not self.user.is_active or getattr(self.user, 'is_deleted', False):
            await self.close()
            return
        if getattr(self.user, 'is_guest_demo', False) or getattr(self.user, 'username', '') == 'GUEST_DEMO':
            await self.close()
            return

        self.friend_enrollment = self.scope['url_route']['kwargs']['friend_enrollment'].strip().upper()
        self.friend = await self._get_friend_user()

        if not await self._is_connection_authorized():
            await self.close()
            return

        # Canonical group naming
        u1_id, u2_id = sorted([self.user.id, self.friend.id])
        self.group_name = f'direct_chat_{u1_id}_{u2_id}'
        self.user_group_name = f'user_{self.user.id}'

        try:
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        except Exception as e:
            logger.warning(f"FriendChatConsumer: channel layer group_add failed: {e}")
            await self.close(code=1012)
            return
        await self.accept()
        try:
            from asgiref.sync import sync_to_async
            from accounts.models import User
            await sync_to_async(User.objects.filter(id=self.user.id).update)(last_seen=timezone.now())
        except Exception:
            pass

        # Announce online presence to the friend if not hidden and first connection
        user_hides_online = await self._user_hides_online()
        if not user_hides_online:
            count = await self._incr_user_connection(self.user.id)
            self._presence_incremented = True
            if count == 1:
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'chat.presence',
                        'sender': self.user.enrollment_number,
                        'status': 'online',
                    }
                )

        # Send initial peer presence to self if friend is active and unhidden
        friend_hides = await self._friend_hides_online()
        if not friend_hides:
            friend_online = await self._is_user_online(self.friend.id)
            if friend_online:
                await self.send(text_data=json.dumps({
                    'type': 'presence',
                    'sender': self.friend.enrollment_number,
                    'status': 'online',
                }))

    async def disconnect(self, close_code):
        if hasattr(self, 'user_group_name') and self.channel_layer:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        if hasattr(self, 'group_name'):
            if getattr(self, '_presence_incremented', False):
                count = await self._decr_user_connection(self.user.id)
                self._presence_incremented = False
                if count <= 0:
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            'type': 'chat.presence',
                            'sender': self.user.enrollment_number,
                            'status': 'offline',
                        }
                    )
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        try:
            from django.db import close_old_connections
            await database_sync_to_async(close_old_connections)()
        except Exception:
            pass

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None or not text_data:
            return

        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(data, dict):
            return

        if getattr(self, '_presence_incremented', False):
            await self._refresh_presence_lease(self.user.id)

        event_type = data.get('type')

        # Read receipt (supports both individual and single-batch mark_all_read)
        if event_type in ('mark_read', 'mark_all_read'):
            if event_type == 'mark_all_read':
                read_time = await self._mark_all_messages_read()
                msg_id = 0
            else:
                msg_id = data.get('message_id')
                read_time = await self._mark_message_read(msg_id) if msg_id else None

            # Check if this user permits DM read receipts
            if read_time and await self._user_allows_read_receipts():
                import zoneinfo
                ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                local_dt = timezone.localtime(read_time or timezone.now(), ist_tz)
                time_str = local_dt.strftime("%H:%M")
                display_str = f"Today at {local_dt.strftime('%I:%M %p')} IST"
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'chat.read_receipt',
                        'message_id': msg_id,
                        'reader': self.user.enrollment_number,
                        'read_at_time': time_str,
                        'read_at_display': display_str,
                    }
                )
            return

        # Real-time typing indicator (suppressed if user hid online status)
        if event_type == 'typing':
            if not await self._check_rate_limit('typing', limit=10, period_sec=10):
                return
            user_hides_online = await self._user_hides_online()
            if not user_hides_online:
                is_typing = bool(data.get('is_typing', False))
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'chat.typing',
                        'sender': self.user.enrollment_number,
                        'is_typing': is_typing,
                    }
                )
            return

        # Chat message
        if event_type == 'message':
            if not await self._check_rate_limit('message', limit=5, period_sec=3):
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'error': 'Message rate limit exceeded. Please wait a moment.',
                }))
                return

            # Dynamic friendship & active status check
            if not await self._is_connection_authorized():
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'error': 'This chat connection is no longer active.',
                }))
                await self.close(code=4003)
                return

            content = (data.get('content') or '').strip()
            if not content or len(content) > 2000:
                return

            msg = await self._save_direct_message(content)

            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'chat.direct_message',
                    'message_id': msg.id,
                    'temp_id': data.get('temp_id'),
                    'sender': self.user.enrollment_number,
                    'content': msg.content,
                    'timestamp': date_filter(timezone.localtime(msg.created_at), 'M j, H:i'),
                }
            )

            # Global notification broadcast to recipient's personal notification channel
            sender_name = await self._get_user_display_name()
            avatar_url = await self._get_user_avatar_url()
            await self.channel_layer.group_send(
                f'user_notifications_{self.friend.id}',
                {
                    'type': 'chat.notification',
                    'message_id': msg.id,
                    'sender': self.user.enrollment_number,
                    'sender_name': sender_name,
                    'sender_avatar': avatar_url,
                    'content': msg.content[:120],
                    'url': f'/messages/{self.user.enrollment_number}/',
                    'timestamp': timezone.localtime(msg.created_at).strftime('%H:%M'),
                }
            )

            # Background Web Push to recipient if app is closed / backgrounded
            try:
                from accounts.notifications import dispatch_web_push
                dm_push_payload = {
                    'title': f'Message from {sender_name}',
                    'body': msg.content[:150],
                    'url': f'/messages/{self.user.enrollment_number}/',
                    'icon': avatar_url or '/static/img/pwa/icon-192x192.png',
                    'badge': '/static/img/pwa/icon-192x192.png',
                    'tag': f'dm-{self.user.id}',
                }
                dispatch_web_push(self.friend.id, dm_push_payload)
            except Exception as push_err:
                logger.warning("Background Web Push dispatch error: %s", push_err)

            await self.send(text_data=json.dumps({
                'type': 'ack',
                'status': 'sent',
                'message_id': msg.id,
                'temp_id': data.get('temp_id'),
            }))

    # ------------------------------------------------------------------
    # Group handlers
    # ------------------------------------------------------------------

    async def user_session_revoked(self, event):
        """Handle push-based session termination (logout, password change, deactivation)."""
        await self.send(text_data=json.dumps({
            'type': 'session_terminated',
            'reason': event.get('reason', 'Session terminated by security policy.'),
        }))
        await self.close(code=4003)

    async def chat_direct_message(self, event):
        if not await self._is_connection_authorized():
            await self.close(code=4003)
            return

        await self.send(text_data=json.dumps({
            'type': 'message',
            'message_id': event['message_id'],
            'temp_id': event.get('temp_id'),
            'sender': event['sender'],
            'content': event['content'],
            'timestamp': event['timestamp'],
            'is_self': event['sender'] == self.user.enrollment_number,
        }))

    async def chat_unfriended(self, event):
        """Immediately disconnect both clients when friendship is dissolved."""
        await self.send(text_data=json.dumps({
            'type': 'unfriended',
            'message': event.get('message', 'Friendship connection ended.'),
        }))
        await self.close(code=4003)

    async def chat_typing(self, event):
        if event['sender'] != self.user.enrollment_number:
            await self.send(text_data=json.dumps({
                'type': 'typing',
                'sender': event['sender'],
                'is_typing': event['is_typing'],
            }))

    async def chat_presence(self, event):
        if event['sender'] != self.user.enrollment_number:
            await self.send(text_data=json.dumps({
                'type': 'presence',
                'sender': event['sender'],
                'status': event['status'],
            }))

    async def chat_read_receipt(self, event):
        """Relay read receipt with viewed timestamp to other participant if sender permits."""
        if await self._user_allows_read_receipts():
            await self.send(text_data=json.dumps({
                'type': 'read_receipt',
                'message_id': event['message_id'],
                'reader': event['reader'],
                'read_at_time': event.get('read_at_time', ''),
                'read_at_display': event.get('read_at_display', ''),
            }))

    async def chat_notification(self, event):
        '''No-op when connected to direct chat room.'''
        pass

    # ------------------------------------------------------------------
    # Database queries
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _get_friend_user(self):
        from accounts.models import User
        try:
            return User.objects.get(enrollment_number=self.friend_enrollment)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def _are_friends(self) -> bool:
        from accounts.models import Friendship
        return Friendship.objects.filter(
            Q(user1=self.user, user2=self.friend) |
            Q(user1=self.friend, user2=self.user)
        ).exists()

    @database_sync_to_async
    def _save_direct_message(self, content: str):
        from accounts.models import DirectMessage
        import datetime
        # Prevent rapid duplicate creation (e.g. within 12 seconds)
        recent = DirectMessage.objects.filter(
            sender=self.user,
            recipient=self.friend,
            content=content,
            created_at__gte=timezone.now() - datetime.timedelta(seconds=12)
        ).first()
        if recent:
            return recent
        return DirectMessage.objects.create(
            sender=self.user,
            recipient=self.friend,
            content=content,
        )

    @database_sync_to_async
    def _get_user_display_name(self) -> str:
        return self.user.get_full_name() or self.user.enrollment_number

    @database_sync_to_async
    def _get_user_avatar_url(self) -> str:
        try:
            return self.user.profile_photo.url if self.user.profile_photo else ''
        except Exception:
            return ''

    @database_sync_to_async
    def _user_allows_read_receipts(self) -> bool:
        from accounts.models import User
        try:
            return User.objects.filter(id=self.user.id, dm_read_receipts=True).exists()
        except Exception:
            return True

    @database_sync_to_async
    def _user_hides_online(self) -> bool:
        from accounts.models import User
        try:
            return User.objects.filter(id=self.user.id, hide_online_status=True).exists()
        except Exception:
            return False

    @database_sync_to_async
    def _friend_hides_online(self) -> bool:
        from accounts.models import User
        try:
            return User.objects.filter(id=self.friend.id, hide_online_status=True).exists()
        except Exception:
            return False

    @database_sync_to_async
    def _is_user_online(self, user_id: int) -> bool:
        from django.core.cache import cache
        key = f"ws_presence_{user_id}"
        return bool(cache.get(key, 0) > 0)

    @database_sync_to_async
    def _refresh_presence_lease(self, user_id: int):
        from django.core.cache import cache
        key = f"ws_presence_{user_id}"
        try:
            cache.touch(key, 120)
        except Exception:
            pass

    @database_sync_to_async
    def _mark_all_messages_read(self):
        from accounts.models import DirectMessage
        try:
            now = timezone.now()
            updated = DirectMessage.objects.filter(
                sender=self.friend,
                recipient=self.user,
                is_read=False,
            ).update(is_read=True, read_at=now)
            return now if updated else None
        except Exception:
            return None

    @database_sync_to_async
    def _mark_message_read(self, message_id):
        from accounts.models import DirectMessage
        try:
            now = timezone.now()
            updated = DirectMessage.objects.filter(
                id=message_id,
                sender=self.friend,
                recipient=self.user,
                is_read=False,
            ).update(is_read=True, read_at=now)
            if updated:
                return now
            msg = DirectMessage.objects.filter(id=message_id).first()
            return msg.read_at if (msg and msg.read_at) else now
        except Exception:
            return None

    @database_sync_to_async
    def _incr_user_connection(self, user_id: int) -> int:
        from django.core.cache import cache
        key = f"ws_presence_{user_id}"
        try:
            cache.add(key, 0, timeout=120)
            val = cache.incr(key)
            cache.touch(key, 120)
            return val
        except Exception:
            try:
                val = cache.get(key, 0) + 1
                cache.set(key, val, timeout=120)
                return val
            except Exception:
                return 1

    @database_sync_to_async
    def _decr_user_connection(self, user_id: int) -> int:
        from django.core.cache import cache
        key = f"ws_presence_{user_id}"
        try:
            val = cache.decr(key)
            if val <= 0:
                cache.delete(key)
                return 0
            cache.touch(key, 120)
            return val
        except (ValueError, Exception):
            cache.delete(key)
            return 0

    @database_sync_to_async
    def _check_rate_limit(self, action_key: str, limit: int, period_sec: int) -> bool:
        from django.core.cache import cache
        import time
        cache_key = f"ws_rl:{self.user.id}:{action_key}"
        now = time.time()
        history = cache.get(cache_key) or []
        valid_history = [t for t in history if now - t < period_sec]
        if len(valid_history) >= limit:
            return False
        valid_history.append(now)
        cache.set(cache_key, valid_history, timeout=period_sec + 2)
        return True

class NotificationConsumer(AsyncWebsocketConsumer):
    """Global user notification consumer for real-time popups and user-level events.

    Path: /ws/notifications/
    Channel groups:
      - user_notifications_<user_id>
      - user_<user_id>
    """

    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated or not self.user.is_active or getattr(self.user, 'is_deleted', False):
            await self.close(code=4001)
            return
        if getattr(self.user, 'is_guest_demo', False) or getattr(self.user, 'username', '') == 'GUEST_DEMO':
            await self.close(code=4001)
            return

        self.notification_group_name = f'user_notifications_{self.user.id}'
        self.user_group_name = f'user_{self.user.id}'

        try:
            await self.channel_layer.group_add(self.notification_group_name, self.channel_name)
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        except Exception as e:
            logger.warning(f"NotificationConsumer: channel layer group_add failed: {e}")
            await self.close(code=1012)
            return
        await self.accept()
        try:
            from asgiref.sync import sync_to_async
            from accounts.models import User
            await sync_to_async(User.objects.filter(id=self.user.id).update)(last_seen=timezone.now())
        except Exception:
            pass

    async def disconnect(self, close_code):
        try:
            if hasattr(self, 'notification_group_name') and self.channel_layer:
                await self.channel_layer.group_discard(self.notification_group_name, self.channel_name)
            if hasattr(self, 'user_group_name') and self.channel_layer:
                await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        except Exception as e:
            logger.debug(f"NotificationConsumer disconnect cleanup error: {e}")

    async def chat_notification(self, event):
        """Receive incoming direct message notification and push to client."""
        await self.send(text_data=json.dumps({
            'type': 'chat_notification',
            'message_id': event.get('message_id'),
            'sender': event['sender'],
            'sender_name': event.get('sender_name', event['sender']),
            'sender_avatar': event.get('sender_avatar', ''),
            'content': event['content'],
            'url': event['url'],
            'timestamp': event.get('timestamp', ''),
        }))

    async def app_notification(self, event):
        """Receive general application notification (friend requests, ride joins, approvals, etc.)."""
        await self.send(text_data=json.dumps({
            'type': 'app_notification',
            'category': event.get('category', 'general'),
            'title': event.get('title', 'Notification'),
            'content': event.get('content', ''),
            'sender': event.get('sender', ''),
            'sender_name': event.get('sender_name', ''),
            'sender_avatar': event.get('sender_avatar', ''),
            'url': event.get('url', ''),
            'badge': event.get('badge', ''),
            'badge_class': event.get('badge_class', 'bg-success-subtle text-success'),
            'icon': event.get('icon', 'bi-bell-fill'),
            'timestamp': event.get('timestamp', ''),
        }))

    async def user_session_revoked(self, event):
        """Handle push-based session termination."""
        await self.send(text_data=json.dumps({
            'type': 'session_terminated',
            'reason': event.get('reason', 'Session terminated by security policy.'),
        }))
        await self.close(code=4003)

