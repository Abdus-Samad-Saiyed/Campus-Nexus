"""Unit tests for user real-time notifications (friend requests, ride joins, approvals, etc.)."""

import datetime
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Announcement, FriendRequest, Friendship, User
from accounts.notifications import send_user_notification
from coordinate.models import ParticipantStatus, RideJoinRequest, RideMessage, RideParticipant, RideRequest, RideWaitlist
from coordinate.services import (
    alter_ride_capacity_service,
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
        self.assertEqual(call_kwargs['url'], f'/profile/{self.user_a.enrollment_number}/')
        self.assertTrue(call_kwargs['tag'].startswith('fr-'))

    @patch('accounts.views.send_user_notification')
    def test_reciprocal_friend_request_auto_accept_triggers_notification(self, mock_notify):
        """Sending a friend request when the other user already requested auto-accepts and notifies."""
        FriendRequest.objects.create(sender=self.user_b, receiver=self.user_a)
        self.client.force_login(self.user_a)
        self.client.post(reverse('send_friend_request', args=[self.user_b.enrollment_number]))

        self.assertTrue(Friendship.objects.filter(user1__in=[self.user_a, self.user_b], user2__in=[self.user_a, self.user_b]).exists())
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args[1]
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'friend_accepted')
        self.assertEqual(call_kwargs['tag'], f'fr-acc-{self.user_a.id}-{self.user_b.id}')

    @patch('accounts.notifications.send_user_notification')
    def test_admin_create_announcement_dispatches_targeted_notifications(self, mock_notify):
        """Creating an active announcement dispatches WS + WebPush notifications to targeted users."""
        staff_user = User.objects.create_user(
            username='2024BCSE599',
            enrollment_number='2024BCSE599',
            gender='M',
            phone_number='9876543599',
            password='Password123',
            verified=True,
            is_staff=True,
        )
        self.client.force_login(staff_user)
        resp = self.client.post(reverse('admin_create_announcement'), {
            'title': 'Bus Schedule Update',
            'content': 'Evening buses depart 15 minutes early today.',
            'priority': 'WARNING',
            'target_type': 'ALL',
        })
        self.assertEqual(resp.status_code, 302)
        ann = Announcement.objects.get(title='Bus Schedule Update')
        notified_ids = {call.kwargs['user_id'] for call in mock_notify.call_args_list}
        self.assertIn(self.user_a.id, notified_ids)
        self.assertIn(self.user_b.id, notified_ids)
        first_kwargs = mock_notify.call_args_list[0].kwargs
        self.assertEqual(first_kwargs['category'], 'announcement')
        self.assertEqual(first_kwargs['tag'], f'ann-{ann.id}')

    @patch('accounts.notifications.send_user_notification')
    def test_http_send_ride_message_notifies_confirmed_co_participants(self, mock_notify):
        """HTTP fallback send_message notifies other CONFIRMED ride participants with ride_chat category."""
        RideParticipant.objects.create(ride=self.ride, user=self.user_b, status=ParticipantStatus.CONFIRMED)
        self.client.force_login(self.user_a)
        resp = self.client.post(reverse('send_message', args=[self.ride.id]), {'content': 'Leaving hostel gate now!'})
        self.assertEqual(resp.status_code, 302)

        msg = RideMessage.objects.get(ride=self.ride, content='Leaving hostel gate now!')
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'ride_chat')
        self.assertEqual(call_kwargs['tag'], f'rchat-{msg.id}')
        self.assertEqual(call_kwargs['url'], f'/coordinate/{self.ride.id}/')

    @patch('accounts.notifications.send_user_notification')
    def test_alter_ride_capacity_kick_notifies_passenger(self, mock_notify):
        """Reducing capacity and kicking a passenger triggers ride_kicked notification."""
        RideParticipant.objects.create(ride=self.ride, user=self.user_b, status=ParticipantStatus.CONFIRMED)
        ok, msg, details = alter_ride_capacity_service(
            ride_id=self.ride.id,
            host_user=self.user_a,
            new_capacity=1,
            kick_user_ids=[self.user_b.id],
        )
        self.assertTrue(ok, msg)
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'ride_kicked')
        self.assertEqual(call_kwargs['tag'], f'ride-kick-{self.ride.id}-{self.user_b.id}')

    @patch('accounts.notifications.send_user_notification')
    def test_complete_ride_notifies_co_participants(self, mock_notify):
        """Marking a departed ride completed notifies co-passengers."""
        RideParticipant.objects.create(ride=self.ride, user=self.user_b, status=ParticipantStatus.CONFIRMED)
        self.ride.departure_time = timezone.now() - datetime.timedelta(minutes=30)
        self.ride.save(update_fields=['departure_time'])

        self.client.force_login(self.user_a)
        resp = self.client.post(reverse('complete_ride', args=[self.ride.id]))
        self.assertEqual(resp.status_code, 302)
        mock_notify.assert_called()
        call_kwargs = mock_notify.call_args.kwargs
        self.assertEqual(call_kwargs['user_id'], self.user_b.id)
        self.assertEqual(call_kwargs['category'], 'ride_completed')
        self.assertEqual(call_kwargs['tag'], f'ride-complete-{self.ride.id}')

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
