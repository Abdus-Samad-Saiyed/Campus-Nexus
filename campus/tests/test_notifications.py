"""Unit tests for user real-time notifications (friend requests, ride joins, approvals, etc.)."""

import datetime
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import FriendRequest, Friendship, User
from accounts.notifications import send_user_notification
from coordinate.models import ParticipantStatus, RideJoinRequest, RideParticipant, RideRequest, RideWaitlist
from coordinate.services import (
    approve_ride_join_request,
    decline_ride_join_request,
    promote_waitlist_passenger,
    request_to_join_ride,
)


class UserNotificationsTests(TestCase):

    def setUp(self):
        self.user_a = User.objects.create_user(
            username='2024BCSE501',
            enrollment_number='2024BCSE501',
            gender='M',
            phone_number='9876543501',
            password='Password123',
            verified=True,
        )
        self.user_b = User.objects.create_user(
            username='2024BCSE502',
            enrollment_number='2024BCSE502',
            gender='F',
            phone_number='9876543502',
            password='Password123',
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.user_a,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + datetime.timedelta(days=2),
            total_spots=3,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=self.ride, user=self.user_a, status=ParticipantStatus.CONFIRMED)

    @patch('accounts.views.send_user_notification')
    def test_send_friend_request_triggers_notification(self, mock_notify):
        """Sending a friend request notifies the recipient."""
        self.client.force_login(self.user_a)
        self.client.post(reverse('send_friend_request', args=[self.user_b.enrollment_number]))

        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'friend_request')

    @patch('accounts.views.send_user_notification')
    def test_accept_friend_request_triggers_notification(self, mock_notify):
        """Accepting a friend request notifies the sender that they were accepted."""
        fr = FriendRequest.objects.create(sender=self.user_a, receiver=self.user_b)
        self.client.force_login(self.user_b)
        self.client.post(reverse('accept_friend_request', args=[fr.id]))

        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.user_a.id)
        self.assertEqual(call_kwargs['category'], 'friend_accepted')

    @patch('accounts.notifications.send_user_notification')
    def test_request_ride_join_triggers_notification_to_host(self, mock_notify):
        """Applying to enter a ride sends a notification to the host."""
        ok, msg, details = request_to_join_ride(
            ride_id=self.ride.id,
            user=self.user_b,
            disclose_identity=True,
            share_contact=True,
        )
        self.assertTrue(ok)
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.ride.creator_id)
        self.assertEqual(call_kwargs['category'], 'ride_join_request')

    @patch('accounts.notifications.send_user_notification')
    def test_approve_ride_join_triggers_notification_to_requester(self, mock_notify):
        """Host approving join request notifies the applicant student."""
        join_req = RideJoinRequest.objects.create(
            ride=self.ride,
            user=self.user_b,
            status='PENDING',
        )
        ok, msg, details = approve_ride_join_request(
            ride_id=self.ride.id,
            request_id=join_req.id,
            host_user=self.user_a,
        )
        self.assertTrue(ok)
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'ride_approved')

    @patch('accounts.notifications.send_user_notification')
    def test_decline_ride_join_triggers_notification_to_requester(self, mock_notify):
        """Host declining join request notifies the applicant student."""
        join_req = RideJoinRequest.objects.create(
            ride=self.ride,
            user=self.user_b,
            status='PENDING',
        )
        ok, msg, details = decline_ride_join_request(
            ride_id=self.ride.id,
            request_id=join_req.id,
            host_user=self.user_a,
        )
        self.assertTrue(ok)
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'ride_declined')

    @patch('accounts.notifications.send_user_notification')
    def test_promote_waitlist_passenger_triggers_notification(self, mock_notify):
        """Standby promotion notifies the waitlisted candidate."""
        RideWaitlist.objects.create(ride=self.ride, user=self.user_b)
        promoted = promote_waitlist_passenger(self.ride)
        self.assertEqual(promoted, self.user_b)
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'standby_promotion')
