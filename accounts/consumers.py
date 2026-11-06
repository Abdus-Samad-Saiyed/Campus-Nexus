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
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q


class FriendChatConsumer(AsyncWebsocketConsumer):

    async def _is_connection_authorized(self) -> bool:
        """Verify both users are authenticated, active, verified, not soft-deleted, and friends."""
        if not self.user or not self.user.is_authenticated or not self.user.is_active or getattr(self.user, 'is_deleted', False):
            return False
        if not self.friend or not self.friend.is_active or getattr(self.friend, 'is_deleted', False):
            return False
        return await self._are_friends()

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

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

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

        # Read receipt
        if event_type == 'mark_read':
            msg_id = data.get('message_id')
            if msg_id:
                await self._mark_message_read(msg_id)
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
                    'error': 'You are no longer connected as friends with this user.',
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
                    'sender': self.user.enrollment_number,
                    'content': msg.content,
                    'timestamp': msg.created_at.strftime('%b %d, %H:%M'),
                }
            )

            await self.send(text_data=json.dumps({
                'type': 'ack',
                'status': 'sent',
                'message_id': msg.id,
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
        return DirectMessage.objects.create(
            sender=self.user,
            recipient=self.friend,
            content=content,
        )

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
    def _mark_message_read(self, message_id):
        from accounts.models import DirectMessage
        try:
            DirectMessage.objects.filter(
                id=message_id,
                sender=self.friend,
                recipient=self.user,
                is_read=False,
            ).update(is_read=True)
        except Exception:
            pass

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
