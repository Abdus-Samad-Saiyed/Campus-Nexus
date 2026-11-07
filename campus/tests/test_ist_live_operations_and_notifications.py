"""Unit and regression tests for IST timezone compliance, live Operations/Applications channels,
desktop/phone push notifications, and user last_online status option."""

import json
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from coordinate.models import RideRequest, RideParticipant, ParticipantStatus
from accounts.models import AccountApplication, AuditEvent, log_audit_event

User = get_user_model()


class ISTTimezoneAndDisplayTests(TestCase):
    """Ensure IST (Asia/Kolkata) is strictly enforced and displayed across platform."""

    def setUp(self):
        self.staff_user = User.objects.create_superuser(
            enrollment_number="2024BCSE999",
            username="2024BCSE999",
            email="staff@campus-nexus.online",
            password="StrongPassword123!",
            gender="M",
            verified=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client = Client()
        self.client.force_login(self.staff_user)

    def test_operational_dashboard_renders_ist_server_time(self):
        """Operational dashboard renders explicit IST time in telemetry and audit table."""
        AuditEvent.objects.create(
            event_type='LOGIN_SUCCESS',
            actor_enrollment='2024BCSE999',
            description='Test login',
        )
        resp = self.client.get(reverse('operational_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "IST")
        self.assertContains(resp, "Timestamp (IST)")

    def test_service_worker_root_endpoint_served(self):
        """Root /sw.js serves valid JavaScript with Service-Worker-Allowed header."""
        resp = self.client.get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Type'), 'application/javascript')
        self.assertEqual(resp.headers.get('Service-Worker-Allowed'), '/')
        content = b"".join(resp.streaming_content) if hasattr(resp, 'streaming_content') else resp.content
        self.assertIn(b'notificationclick', content)


class UserLastOnlineOptionTests(TestCase):
    """Test user last_seen tracking, online/active status computation, and privacy options."""

    def setUp(self):
        self.user = User.objects.create_user(
            enrollment_number="2024BCSE001",
            username="2024BCSE001",
            email="user1@campus-nexus.online",
            password="StrongPassword123!",
            gender="M",
            verified=True,
        )
        self.viewer = User.objects.create_user(
            enrollment_number="2024BCSE002",
            username="2024BCSE002",
            email="user2@campus-nexus.online",
            password="StrongPassword123!",
            gender="F",
            verified=True,
        )
        self.client = Client()
        self.client.force_login(self.viewer)

    def test_last_seen_active_now_when_recent(self):
        """User seen within last 2 minutes reports Active Now."""
        self.user.last_seen = timezone.now() - timedelta(seconds=30)
        self.user.hide_online_status = False
        self.user.save()

        self.assertTrue(self.user.is_online_now)
        self.assertEqual(self.user.last_seen_display, "Active now")

        resp = self.client.get(reverse('profile', kwargs={'enrollment_number': self.user.enrollment_number}))
        self.assertContains(resp, "Active Now")

    def test_last_seen_hidden_when_privacy_option_enabled(self):
        """When hide_online_status is True, active status and last seen are concealed."""
        self.user.last_seen = timezone.now() - timedelta(seconds=10)
        self.user.hide_online_status = True
        self.user.save()

        self.assertFalse(self.user.is_online_now)
        self.assertEqual(self.user.last_seen_display, "Hidden")

        resp = self.client.get(reverse('profile', kwargs={'enrollment_number': self.user.enrollment_number}))
        self.assertContains(resp, "Last online hidden")
        self.assertNotContains(resp, "Active Now")

    def test_last_seen_formatted_ist_display(self):
        """Offline user last seen shows formatted IST time."""
        past_time = timezone.now() - timedelta(hours=2)
        self.user.last_seen = past_time
        self.user.hide_online_status = False
        self.user.save()

        self.assertFalse(self.user.is_online_now)
        self.assertIn("IST", self.user.last_seen_display)


class RideNotificationDispatchTests(TransactionTestCase):
    """Test ride update notifications for cancellation and rescheduling."""

    def setUp(self):
        self.host = User.objects.create_user(
            enrollment_number="2024BCSE010",
            username="2024BCSE010",
            email="host@campus-nexus.online",
            password="StrongPassword123!",
            gender="M",
            verified=True,
        )
        self.passenger = User.objects.create_user(
            enrollment_number="2024BCSE020",
            username="2024BCSE020",
            email="passenger@campus-nexus.online",
            password="StrongPassword123!",
            gender="F",
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route="CAMPUS_AIRPORT",
            departure_time=timezone.now() + timedelta(days=2),
            total_spots=3,
            status="OPEN",
        )
        RideParticipant.objects.create(
            ride=self.ride,
            user=self.passenger,
            status=ParticipantStatus.CONFIRMED,
        )
        self.client = Client()
        self.client.force_login(self.host)

    @patch('accounts.notifications.send_user_notification')
    def test_reschedule_ride_dispatches_user_notification(self, mock_notify):
        """Host rescheduling ride notifies confirmed participants."""
        new_time = (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('reschedule_ride', kwargs={'ride_id': self.ride.id}), {
            'departure_time': new_time,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(mock_notify.called)
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.passenger.id)
        self.assertEqual(call_kwargs['title'], 'Ride Rescheduled')
        self.assertEqual(call_kwargs['category'], 'ride_update')

    @patch('accounts.notifications.send_user_notification')
    def test_cancel_ride_service_dispatches_user_notification(self, mock_notify):
        """Cancelling ride dispatches notifications to participants."""
        from coordinate.services import cancel_ride_service
        success, msg = cancel_ride_service(self.ride.id, self.host)
        self.assertTrue(success)
        self.assertTrue(mock_notify.called)
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.passenger.id)
        self.assertEqual(call_kwargs['title'], 'Ride Cancelled')

    @patch('accounts.notifications.send_user_notification')
    def test_ride_chat_message_notifies_participants(self, mock_notify):
        """Posting a ride chat message dispatches notifications to other confirmed participants."""
        from coordinate.consumers import RideChatConsumer
        from coordinate.models import RideMessage

        consumer = RideChatConsumer()
        consumer.ride_id = self.ride.id
        consumer.user = self.host

        msg = RideMessage.objects.create(
            ride=self.ride,
            author=self.host,
            content="Hey all, leaving from main gate at 5 PM!",
        )

        import asyncio
        asyncio.run(consumer._notify_ride_participants(msg))

        self.assertTrue(mock_notify.called)
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.passenger.id)
        self.assertIn("Ride Chat", call_kwargs['title'])
        self.assertEqual(call_kwargs['category'], 'ride_chat')
        self.assertIn("leaving from main gate", call_kwargs['content'])
