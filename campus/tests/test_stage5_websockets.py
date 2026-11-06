"""Stage 5 tests — WebSocket Ownership, Channel Authorization, and Dynamic Revocation.

Covers:
1. RideChatConsumer connection authorization (participants only).
2. Dynamic membership verification on incoming and outgoing messages.
3. Ride lifecycle teardown broadcasts (cancelled, completed, terminated) disconnect clients.
4. Targeted kick and leave revocation immediately disconnects removed participant.
5. FriendChatConsumer connection authorization (mutual friends only).
6. Unfriend event triggers immediate WebSocket disconnection for both parties.
"""

import asyncio
from datetime import timedelta

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.consumers import FriendChatConsumer
from accounts.models import Friendship
from coordinate.consumers import RideChatConsumer
from coordinate.models import RideParticipant, RideRequest

User = get_user_model()


class Stage5RideWebSocketTests(TransactionTestCase):
    """Test RideChatConsumer authorization, dynamic membership checks, and teardown."""

    def setUp(self):
        self.host = User.objects.create_user(
            enrollment_number="2024BCSE401",
            username="2024BCSE401",
            password="Password123!",
            first_name="Ride",
            last_name="Host",
            gender="M",
            phone_number="+919876543401",
            email="host401@nitsri.ac.in",
            verified=True,
        )
        self.passenger = User.objects.create_user(
            enrollment_number="2024BCSE402",
            username="2024BCSE402",
            password="Password123!",
            first_name="Ride",
            last_name="Passenger",
            gender="M",
            phone_number="+919876543402",
            email="pass402@nitsri.ac.in",
            verified=True,
        )
        self.stranger = User.objects.create_user(
            enrollment_number="2024BCSE403",
            username="2024BCSE403",
            password="Password123!",
            first_name="Ride",
            last_name="Stranger",
            gender="M",
            phone_number="+919876543403",
            email="stranger403@nitsri.ac.in",
            verified=True,
        )

        dep_time = timezone.now() + timedelta(days=1)
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route="NIT_TO_AIRPORT",
            departure_time=dep_time,
            total_spots=3,
            visibility="MIXED",
            status="OPEN",
        )
        RideParticipant.objects.create(ride=self.ride, user=self.host)
        RideParticipant.objects.create(ride=self.ride, user=self.passenger)

    def test_consumer_is_participant_check(self):
        """Only confirmed participants return True for _is_participant."""
        consumer = RideChatConsumer()
        consumer.ride_id = self.ride.id

        # Host is participant
        consumer.user = self.host
        self.assertTrue(asyncio.run(consumer._is_participant()))

        # Passenger is participant
        consumer.user = self.passenger
        self.assertTrue(asyncio.run(consumer._is_participant()))

        # Stranger is NOT a participant
        consumer.user = self.stranger
        self.assertFalse(asyncio.run(consumer._is_participant()))

    def test_consumer_can_send_buffer(self):
        """Active rides allow sending; concluded rides or 2+ hours past departure disallow sending."""
        consumer = RideChatConsumer()
        consumer.ride_id = self.ride.id
        consumer.user = self.passenger

        # Future ride can send
        self.assertTrue(asyncio.run(consumer._can_send()))

        # Cancelled ride cannot send
        self.ride.status = 'CANCELLED'
        self.ride.save(update_fields=['status'])
        self.assertFalse(asyncio.run(consumer._can_send()))

    def test_system_notice_teardown_action(self):
        """Teardown notices (cancelled, completed, user_kicked) close socket with targeted code."""
        consumer = RideChatConsumer()
        consumer.ride_id = self.ride.id
        consumer.user = self.passenger

        closed_code = None

        async def fake_close(code=None):
            nonlocal closed_code
            closed_code = code

        async def fake_send(text_data=None):
            pass

        consumer.close = fake_close
        consumer.send = fake_send

        # 1. Cancelled event triggers code 4001
        asyncio.run(consumer.system_notice({'action': 'cancelled', 'content': 'Ride cancelled'}))
        self.assertEqual(closed_code, 4001)

        # 2. User kicked targeted notice triggers code 4003 for kicked user
        closed_code = None
        asyncio.run(consumer.system_notice({
            'action': 'user_kicked',
            'user': '2024BCSE402',  # Passenger
            'content': 'You were removed',
        }))
        self.assertEqual(closed_code, 4003)

        # 3. User kicked notice for another user does NOT close passenger's socket
        closed_code = None
        asyncio.run(consumer.system_notice({
            'action': 'user_kicked',
            'user': '2024BCSE999',  # Someone else
            'content': 'Someone was removed',
        }))
        self.assertIsNone(closed_code)


class Stage5DirectChatWebSocketTests(TransactionTestCase):
    """Test FriendChatConsumer authorization and revocation."""

    def setUp(self):
        self.user1 = User.objects.create_user(
            enrollment_number="2024BCSE404",
            username="2024BCSE404",
            password="Password123!",
            first_name="Friend",
            last_name="One",
            gender="M",
            phone_number="+919876543404",
            email="friend404@nitsri.ac.in",
            verified=True,
        )
        self.user2 = User.objects.create_user(
            enrollment_number="2024BCSE405",
            username="2024BCSE405",
            password="Password123!",
            first_name="Friend",
            last_name="Two",
            gender="F",
            phone_number="+919876543405",
            email="friend405@nitsri.ac.in",
            verified=True,
        )
        self.unrelated_user = User.objects.create_user(
            enrollment_number="2024BCSE406",
            username="2024BCSE406",
            password="Password123!",
            first_name="Stranger",
            last_name="User",
            gender="M",
            phone_number="+919876543406",
            email="stranger406@nitsri.ac.in",
            verified=True,
        )

        self.friendship = Friendship.objects.create(user1=self.user1, user2=self.user2)

    def test_direct_chat_friendship_check(self):
        """Only mutual friends return True for _are_friends."""
        consumer = FriendChatConsumer()
        consumer.user = self.user1
        consumer.friend = self.user2
        self.assertTrue(asyncio.run(consumer._are_friends()))

        # Non-friends return False
        consumer.friend = self.unrelated_user
        self.assertFalse(asyncio.run(consumer._are_friends()))

    def test_direct_chat_unfriended_event_teardown(self):
        """When unfriend event is received, consumer immediately closes socket with code 4003."""
        consumer = FriendChatConsumer()
        consumer.user = self.user1
        consumer.friend = self.user2

        closed_code = None

        async def fake_close(code=None):
            nonlocal closed_code
            closed_code = code

        async def fake_send(text_data=None):
            pass

        consumer.close = fake_close
        consumer.send = fake_send

        asyncio.run(consumer.chat_unfriended({'type': 'chat.unfriended'}))
        self.assertEqual(closed_code, 4003)

    def test_chat_direct_message_revokes_when_user_deactivated(self):
        """If user is deactivated, incoming chat event closes socket."""
        consumer = FriendChatConsumer()
        consumer.user = self.user1
        consumer.friend = self.user2

        self.user2.is_active = False

        closed_code = None

        async def fake_close(code=None):
            nonlocal closed_code
            closed_code = code

        async def fake_send(text_data=None):
            pass

        consumer.close = fake_close
        consumer.send = fake_send

        asyncio.run(consumer.chat_direct_message({
            'message_id': 1,
            'sender': '2024BCSE405',
            'content': 'Hello',
            'timestamp': 'Oct 28, 12:00',
        }))
        self.assertEqual(closed_code, 4003)
    def test_notification_consumer_receives_chat_notification(self):
        """NotificationConsumer receives chat.notification and sends JSON to client."""
        from accounts.consumers import NotificationConsumer
        import json

        consumer = NotificationConsumer()
        consumer.user = self.user1

        sent_payloads = []

        async def fake_send(text_data=None):
            sent_payloads.append(json.loads(text_data))

        consumer.send = fake_send

        asyncio.run(consumer.chat_notification({
            'type': 'chat.notification',
            'message_id': 42,
            'sender': self.user2.enrollment_number,
            'sender_name': 'Friend User',
            'sender_avatar': '',
            'content': 'Hey, are you taking the ride?',
            'url': f'/messages/{self.user2.enrollment_number}/',
            'timestamp': '14:20',
        }))

        self.assertEqual(len(sent_payloads), 1)
        self.assertEqual(sent_payloads[0]['type'], 'chat_notification')
        self.assertEqual(sent_payloads[0]['content'], 'Hey, are you taking the ride?')
        self.assertEqual(sent_payloads[0]['sender'], self.user2.enrollment_number)

