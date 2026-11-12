"""Tests for Notification Center API, multi-platform WebSocket token authentication, and UI integration."""

import datetime
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Announcement, FriendRequest, UserPlatformSession
from campus.api.channels_middleware import get_user_from_token
from coordinate.models import ParticipantStatus, RideJoinRequest, RideParticipant, RideRequest

User = get_user_model()


class NotificationCenterTests(TransactionTestCase):

    def setUp(self):
        self.user_a = User.objects.create_user(
            username='2024BCSE801',
            enrollment_number='2024BCSE801',
            gender='M',
            phone_number='9876543801',
            password='Password123',
            verified=True,
        )
        self.user_b = User.objects.create_user(
            username='2024BCSE802',
            enrollment_number='2024BCSE802',
            gender='F',
            phone_number='9876543802',
            password='Password123',
            verified=True,
        )

    def test_anonymous_access_denied(self):
        """Anonymous requests to /api/v1/notifications/recent/ receive 401."""
        res = self.client.get(reverse('api_recent_notifications'))
        self.assertEqual(res.status_code, 401)

    def test_authenticated_notifications_empty(self):
        """Authenticated user with no pending items receives success and empty list."""
        self.client.force_login(self.user_a)
        res = self.client.get(reverse('api_recent_notifications'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['notifications'], [])
        self.assertEqual(data['unread_count'], 0)

    def test_announcements_in_notifications_feed(self):
        """Active announcements targeting the user are returned in the notification feed."""
        Announcement.objects.create(
            title="Winter Vacation Shuttle Schedule",
            content="Shuttles will operate on a revised timetable starting Friday.",
            priority="INFO",
            target_type="ALL",
            is_active=True,
            created_by=self.user_a,
        )
        self.client.force_login(self.user_b)
        res = self.client.get(reverse('api_recent_notifications'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data['notifications']), 1)
        self.assertEqual(data['notifications'][0]['title'], "Winter Vacation Shuttle Schedule")
        self.assertEqual(data['notifications'][0]['category'], "announcement")
        self.assertEqual(data['unread_count'], 1)

    def test_friend_request_in_notifications_feed(self):
        """Pending friend requests targeting the user appear in the feed."""
        FriendRequest.objects.create(sender=self.user_a, receiver=self.user_b)
        self.client.force_login(self.user_b)
        res = self.client.get(reverse('api_recent_notifications'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(n['category'] == 'social' for n in data['notifications']))
        social_notif = next(n for n in data['notifications'] if n['category'] == 'social')
        self.assertIn("2024BCSE801", social_notif['title'])

    def test_ride_application_in_notifications_feed(self):
        """Ride host sees pending passenger join requests in the notification feed."""
        ride = RideRequest.objects.create(
            creator=self.user_a,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + datetime.timedelta(days=2),
            total_spots=3,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=ride, user=self.user_a, status=ParticipantStatus.CONFIRMED)
        RideJoinRequest.objects.create(ride=ride, user=self.user_b, status='PENDING')

        self.client.force_login(self.user_a)
        res = self.client.get(reverse('api_recent_notifications'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(n['category'] == 'ride' for n in data['notifications']))
        ride_notif = next(n for n in data['notifications'] if n['category'] == 'ride')
        self.assertIn("2024BCSE802", ride_notif['content'])

    def test_channels_multi_platform_session_key_resolution(self):
        """Channels get_user_from_token resolves user when user.current_session_key has multiple comma-separated keys."""
        session = SessionStore()
        session['_auth_user_id'] = str(self.user_a.id)
        session.create()
        token_key = session.session_key

        # Multi-platform session registration
        UserPlatformSession.objects.create(user=self.user_a, platform='web', session_key=token_key)
        UserPlatformSession.objects.create(user=self.user_a, platform='android', session_key="token_platform_app_67890")
        self.user_a.current_session_key = f"{token_key},token_platform_app_67890"
        self.user_a.save(update_fields=['current_session_key'])

        resolved = async_to_sync(get_user_from_token)(token_key)
        self.assertEqual(resolved.id, self.user_a.id)
