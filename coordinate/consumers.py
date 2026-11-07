"""WebSocket consumer for ride chat.

Each ride has its own channel group: 'ride_<ride_id>'.
Messages are saved to the DB and broadcast to all connected participants.
Includes dynamic membership checks, safe frame parsing, rate limiting, and post-departure buffering.
"""

import datetime
import json
import logging
import time

from channels.db import database_sync_to_async
from django.template.defaultfilters import date as date_filter
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

logger = logging.getLogger(__name__)


class RideChatConsumer(AsyncWebsocketConsumer):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_message_times = []

    async def connect(self):
        raw_id = self.scope['url_route']['kwargs']['ride_id']
        try:
            self.ride_id = int(raw_id)
        except (ValueError, TypeError):
            await self.close()
            return

        self.group_name = f'ride_{self.ride_id}'
        self.user = self.scope.get('user')

        if not self.user or not self.user.is_authenticated:
            await self.close()
            return

        if getattr(self.user, 'is_guest_demo', False) or getattr(self.user, 'username', '') == 'GUEST_DEMO':
            await self.close()
            return

        if not await self._is_participant():
            await self.close()
            return

        self.user_group_name = f'user_{self.user.id}'
        try:
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        except Exception as e:
            logger.warning(f"RideChatConsumer: channel layer group_add failed: {e}")
            await self.close(code=1012)
            return
        await self.accept()

        # Track presence: mark user online in this ride
        await self._add_presence()
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'chat.presence',
                'user_id': self.user.id,
                'enrollment_number': self.user.enrollment_number,
                'status': 'online',
            }
        )
        online_ids = await self._get_online_ids()
        await self.send(text_data=json.dumps({
            'type': 'presence_initial',
            'online_user_ids': online_ids,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'ride_id') and hasattr(self, 'user') and getattr(self.user, 'is_authenticated', False):
            await self._remove_presence()
            if hasattr(self, 'group_name') and self.channel_layer:
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'chat.presence',
                        'user_id': self.user.id,
                        'enrollment_number': self.user.enrollment_number,
                        'status': 'offline',
                    }
                )
        if hasattr(self, 'user_group_name') and self.channel_layer:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        if hasattr(self, 'group_name') and self.channel_layer:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # CHAT-01: Safely ignore binary frames
        if bytes_data is not None or not text_data:
            return

        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': 'Invalid message format.',
            }))
            return

        if not isinstance(data, dict):
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': 'Invalid message format.',
            }))
            return

        # Handle read receipts (always-on in rides)
        if data.get('type') == 'mark_read':
            msg_id = data.get('message_id')
            if msg_id:
                read_count = await self._mark_ride_message_read(msg_id)
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        'type': 'chat.read_receipt',
                        'message_id': msg_id,
                        'reader': self.user.enrollment_number,
                        'read_count': read_count,
                    }
                )
            return

        content = (data.get('content') or '').strip()
        if not content:
            return

        if len(content) > 1000:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': 'Message exceeds 1000 characters limit.',
            }))
            return

        # CHAT-03: Rate limiting on chat (max 5 messages per 3 seconds per connection)
        now_ts = time.time()
        self.last_message_times = [t for t in self.last_message_times if now_ts - t < 3.0]
        if len(self.last_message_times) >= 5:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': 'Chat rate limit reached. Slow down.',
            }))
            return
        self.last_message_times.append(now_ts)

        # SEC-04: Dynamic membership re-check (in case user was kicked after connecting)
        if not await self._is_participant():
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': 'You are no longer a participant in this ride.',
            }))
            await self.close()
            return

        # CHAT-05: Check send permission (departure buffer of 2 hours)
        if not await self._can_send():
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': 'Chat is closed for this ride.',
            }))
            return

        # Persist message
        message = await self._save_message(content)

        # Dispatch real-time user notification to confirmed ride participants
        try:
            await self._notify_ride_participants(message)
        except Exception as e:
            logger.debug(f"Failed to dispatch ride chat notification: {e}")

        # Broadcast to ride group
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'chat.message',
                'message_id': message.id,
                'author': self.user.enrollment_number,
                'content': message.content,
                'timestamp': date_filter(timezone.localtime(message.created_at), 'M j, H:i'),
            },
        )

        # CHAT-02: Delivery acknowledgement to sender
        await self.send(text_data=json.dumps({
            'type': 'ack',
            'status': 'sent',
            'message_id': message.id,
        }))

    async def chat_presence(self, event):
        """Relay presence changes to this WebSocket connection."""
        await self.send(text_data=json.dumps({
            'type': 'presence_update',
            'user_id': event['user_id'],
            'enrollment_number': event['enrollment_number'],
            'status': event['status'],
        }))

    async def chat_read_receipt(self, event):
        """Relay read receipt updates to this WebSocket connection."""
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            'message_id': event['message_id'],
            'reader': event['reader'],
            'read_count': event.get('read_count', 1),
        }))

    async def chat_message(self, event):
        """Relay a group message to this specific WebSocket connection."""
        if not await self._is_participant():
            await self.close(code=4003)
            return

        await self.send(text_data=json.dumps({
            'type': 'message',
            'message_id': event.get('message_id'),
            'author': event['author'],
            'content': event['content'],
            'timestamp': event['timestamp'],
            'is_self': event['author'] == self.user.enrollment_number,
        }))

    async def system_notice(self, event):
        """Relay system announcements (lifecycle teardown, rescheduling, standby promotion)."""
        action = event.get('action')
        await self.send(text_data=json.dumps({
            'type': 'system_notice',
            'action': action,
            'content': event.get('content', ''),
            'departure_time': event.get('departure_time'),
            'user': event.get('user'),
        }))
        if action in ['cancelled', 'completed', 'terminated']:
            await self.close(code=4001)
        elif action in ['user_kicked', 'user_left', 'membership_revoked'] and event.get('user') == self.user.enrollment_number:
            await self.close(code=4003)

    async def ride_terminated(self, event):
        """Teardown handler: closes WebSocket connection when ride ends."""
        await self.system_notice(event)

    async def ride_rescheduled(self, event):
        """Reschedule handler: notifies all clients of new departure time."""
        await self.system_notice(event)

    async def user_session_revoked(self, event):
        """Handle push-based session termination."""
        await self.send(text_data=json.dumps({
            'type': 'session_terminated',
            'reason': event.get('reason', 'Session terminated by security policy.'),
        }))
        await self.close(code=4003)

    async def chat_notification(self, event):
        """No-op when user is connected to a ride chat room."""
        pass

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    @database_sync_to_async
    def _add_presence(self):
        from django.core.cache import cache
        key = f"ride_online_{self.ride_id}"
        users = cache.get(key) or set()
        users.add(self.user.id)
        cache.set(key, users, timeout=7200)

    @database_sync_to_async
    def _remove_presence(self):
        from django.core.cache import cache
        key = f"ride_online_{self.ride_id}"
        users = cache.get(key) or set()
        users.discard(self.user.id)
        cache.set(key, users, timeout=7200)

    @database_sync_to_async
    def _get_online_ids(self):
        from django.core.cache import cache
        key = f"ride_online_{self.ride_id}"
        users = cache.get(key) or set()
        return list(users)

    @database_sync_to_async
    def _mark_ride_message_read(self, message_id: int) -> int:
        from coordinate.models import RideMessage
        try:
            msg = RideMessage.objects.filter(ride_id=self.ride_id, id=message_id).first()
            if msg:
                msg.read_by.add(self.user)
                return msg.read_by.count()
        except Exception:
            pass
        return 0

    @database_sync_to_async
    def _is_participant(self) -> bool:
        from coordinate.models import RideParticipant
        return RideParticipant.objects.filter(
            ride_id=self.ride_id,
            user=self.user,
            status='CONFIRMED',
        ).exists()

    @database_sync_to_async
    def _can_send(self) -> bool:
        from coordinate.models import RideRequest
        try:
            ride = RideRequest.objects.get(id=self.ride_id)
            if ride.status in ['COMPLETED', 'CANCELLED']:
                return False
            # Post-departure chat buffer: 2 hours after scheduled departure
            return ride.departure_time + datetime.timedelta(hours=2) > timezone.now()
        except RideRequest.DoesNotExist:
            return False

    @database_sync_to_async
    def _save_message(self, content: str):
        from coordinate.models import RideMessage
        return RideMessage.objects.create(
            ride_id=self.ride_id,
            author=self.user,
            content=content,
        )

    @database_sync_to_async
    def _notify_ride_participants(self, message):
        from coordinate.models import RideParticipant, RideRequest
        from accounts.notifications import send_user_notification
        try:
            ride = RideRequest.objects.filter(id=self.ride_id).first()
            if not ride:
                return
            participant_ids = list(
                RideParticipant.objects.filter(ride_id=self.ride_id, status='CONFIRMED')
                .exclude(user_id=self.user.id)
                .values_list('user_id', flat=True)
            )
            sender_name = self.user.get_full_name() or self.user.enrollment_number
            route = ride.route_display or f"Ride #{ride.id}"
            for uid in participant_ids:
                send_user_notification(
                    user_id=uid,
                    title=f"Ride Chat ({route})",
                    content=f"{sender_name}: {message.content[:80]}",
                    url=f"/coordinate/{ride.id}/",
                    category="ride_chat",
                    sender_user=self.user,
                    badge="Ride Message",
                    badge_class="bg-info-subtle text-info border border-info-subtle",
                    icon="bi-chat-dots-fill",
                )
        except Exception as e:
            logger.debug(f"Failed to dispatch ride chat notifications: {e}")
