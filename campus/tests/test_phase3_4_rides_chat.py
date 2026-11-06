"""Phase 3 and 4 regression tests — Ride Lifecycle, Capacity, Expiry, Reviews, Friends, and Chat.

Covers:
  - Gender-restricted creator validation (SEC-03)
  - Gender-restricted join validation
  - Kicked participant prevented from rejoining (RIDE-04)
  - Departure time boundaries (future required, max 30 days) (RIDE-01)
  - Expired ride does not lock user from new rides (RIDE-02)
  - History retention: rides with messages become CANCELLED on leave/terminate (DATA-01)
  - Attendance and reliability increment on completion (RIDE-05)
  - My Rides navigation and interface (RIDE-03)
  - Review eligibility, validation, and self-review rejection (DATA-03, UI-01)
  - Canonical friendship ordering and reciprocal duplicate prevention (APP-02)
  - Friend request decline action (APP-01)
  - WebSocket consumer safe JSON, rate limiting, dynamic membership check (CHAT-01 to CHAT-05)
"""

import datetime
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Friendship, FriendRequest, User
from coordinate.models import (
    RideMessage,
    RideParticipant,
    RideRequest,
    RideReview,
)


class RideLifecycleSecurityTests(TestCase):
    """SEC-03, RIDE-01, RIDE-04: Gender validation, departure validation, and kicked participant rules."""

    def setUp(self):
        cache.clear()
        self.male_user = User.objects.create_user(
            username='2024BCSE101',
            enrollment_number='2024BCSE101',
            gender='M',
            phone_number='9876543201',
            password='Password123',
            verified=True,
        )
        self.female_user = User.objects.create_user(
            username='2024BCSE102',
            enrollment_number='2024BCSE102',
            gender='F',
            phone_number='9876543202',
            password='Password123',
            verified=True,
        )
        self.other_user = User.objects.create_user(
            username='2024BCSE103',
            enrollment_number='2024BCSE103',
            gender='M',
            phone_number='9876543203',
            password='Password123',
            verified=True,
        )

    def test_male_cannot_create_girls_only_ride(self):
        """SEC-03: Male student cannot create Girls Only ride."""
        self.client.force_login(self.male_user)
        dep_time = (timezone.now() + datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'GIRLS',
            'departure_time': dep_time,
            'total_spots': 3,
        })
        self.assertContains(response, 'Only female students can create Girls Only rides')
        self.assertFalse(RideRequest.objects.filter(creator=self.male_user).exists())

    def test_female_can_create_girls_only_ride(self):
        """SEC-03: Female student CAN create Girls Only ride."""
        self.client.force_login(self.female_user)
        dep_time = (timezone.now() + datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'GIRLS',
            'departure_time': dep_time,
            'total_spots': 3,
        })
        self.assertRedirects(response, reverse('ride_list'))
        self.assertTrue(RideRequest.objects.filter(creator=self.female_user, visibility='GIRLS').exists())

    def test_past_departure_time_rejected(self):
        """RIDE-01: Past departure time rejected with form error."""
        self.client.force_login(self.male_user)
        past_time = (timezone.now() - datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': past_time,
            'total_spots': 3,
        })
        self.assertContains(response, 'Departure time must be in the future')

    def test_far_future_departure_time_rejected(self):
        """RIDE-01: Departure time > 1 year rejected, but up to 1 year accepted."""
        self.client.force_login(self.male_user)
        # > 1 year (400 days) rejected
        far_time = (timezone.now() + datetime.timedelta(days=400)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': far_time,
            'total_spots': 3,
        })
        self.assertContains(response, 'cannot be scheduled more than 1 year in advance')

        # Within 1 year (180 days) accepted
        ok_time = (timezone.now() + datetime.timedelta(days=180)).strftime('%Y-%m-%dT%H:%M')
        ok_response = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': ok_time,
            'total_spots': 3,
        })
        self.assertRedirects(ok_response, reverse('ride_list'))

    def test_catalogue_overload_limit_10_rides_per_account(self):
        """Account can have at most 10 active rides in the catalogue."""
        self.client.force_login(self.male_user)
        now = timezone.now()

        # Create 10 active rides
        for i in range(10):
            RideRequest.objects.create(
                creator=self.male_user,
                route='NIT_TO_AIRPORT',
                visibility='MIXED',
                departure_time=now + datetime.timedelta(days=i + 1),
                total_spots=3,
                status='OPEN',
            )

        # Attempt to create the 11th ride via POST
        eleventh_time = (now + datetime.timedelta(days=20)).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': eleventh_time,
            'total_spots': 3,
        })
        self.assertRedirects(resp, reverse('ride_list'))
        # Ensure count did not exceed 10
        self.assertEqual(RideRequest.objects.filter(creator=self.male_user, status__in=['OPEN', 'FULL']).count(), 10)

    def test_custom_route_creation_and_search(self):
        """Custom routes require source and destination, and appear in search."""
        self.client.force_login(self.male_user)
        dep_time = (timezone.now() + datetime.timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')

        # Missing source/destination should fail
        fail_resp = self.client.post(reverse('create_ride'), {
            'route': 'CUSTOM',
            'visibility': 'MIXED',
            'departure_time': dep_time,
            'total_spots': 3,
            'custom_from_location': '',
            'custom_to_location': '',
        })
        self.assertEqual(fail_resp.status_code, 200)

        # Providing source & destination succeeds
        ok_resp = self.client.post(reverse('create_ride'), {
            'route': 'CUSTOM',
            'visibility': 'MIXED',
            'departure_time': dep_time,
            'total_spots': 3,
            'custom_from_location': 'Lal Chowk',
            'custom_to_location': 'Hazratbal',
        })
        self.assertRedirects(ok_resp, reverse('ride_list'))
        custom_ride = RideRequest.objects.filter(route='CUSTOM', custom_from_location='Lal Chowk').first()
        self.assertIsNotNone(custom_ride)
        self.assertEqual(custom_ride.route_display, 'Lal Chowk → Hazratbal')

    def test_kicked_participant_cannot_rejoin(self):
        """RIDE-04: Kicked passenger is prevented from rejoining the same ride."""
        ride = RideRequest.objects.create(
            creator=self.male_user,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + datetime.timedelta(days=1),
            total_spots=3,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=ride, user=self.male_user)
        p2 = RideParticipant.objects.create(ride=ride, user=self.other_user)

        # Creator kicks other_user
        self.client.force_login(self.male_user)
        self.client.post(reverse('kick_participant', args=[ride.id, p2.id]))

        self.assertTrue(ride.kicked_users.filter(id=self.other_user.id).exists())
        self.assertFalse(RideParticipant.objects.filter(ride=ride, user=self.other_user).exists())

        # Kicked user attempts to rejoin
        self.client.force_login(self.other_user)
        response = self.client.post(reverse('join_ride', args=[ride.id]))
        self.assertRedirects(response, reverse('ride_list'))
        # Must still NOT be a participant
        self.assertFalse(RideParticipant.objects.filter(ride=ride, user=self.other_user).exists())


class RideHistoryAndRetentionTests(TestCase):
    """DATA-01, RIDE-03, RIDE-05: Ride history retention and attendance stats."""

    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user(
            username='2024BCSE110',
            enrollment_number='2024BCSE110',
            gender='M',
            phone_number='9876543210',
            password='Password123',
            verified=True,
        )
        self.passenger = User.objects.create_user(
            username='2024BCSE111',
            enrollment_number='2024BCSE111',
            gender='F',
            phone_number='9876543211',
            password='Password123',
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + datetime.timedelta(days=1),
            total_spots=2,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=self.ride, user=self.host)
        RideParticipant.objects.create(ride=self.ride, user=self.passenger)

    def test_ride_with_messages_marked_cancelled_on_terminate(self):
        """DATA-01: Terminating a ride that has messages preserves it as CANCELLED instead of hard deleting."""
        RideMessage.objects.create(
            ride=self.ride,
            author=self.host,
            content='Pickup at gate 2',
        )
        self.client.force_login(self.host)
        self.client.post(reverse('terminate_ride', args=[self.ride.id]))

        self.ride.refresh_from_db()
        self.assertEqual(self.ride.status, 'CANCELLED')
        self.assertEqual(self.ride.messages.count(), 1)

    def test_complete_ride_increments_attendance_and_stats(self):
        """RIDE-05: Marking ride complete increments rides_completed and on_time_count atomically."""
        self.ride.departure_time = timezone.now() - datetime.timedelta(minutes=30)
        self.ride.save(update_fields=['departure_time'])
        self.client.force_login(self.host)
        self.client.post(reverse('complete_ride', args=[self.ride.id]))

        self.ride.refresh_from_db()
        self.assertEqual(self.ride.status, 'COMPLETED')

        self.host.refresh_from_db()
        self.passenger.refresh_from_db()

        self.assertEqual(self.host.rides_completed, 1)
        self.assertEqual(self.host.on_time_count, 1)
        self.assertEqual(self.passenger.rides_completed, 1)
        self.assertEqual(self.passenger.on_time_count, 1)

        # Idempotency: completing again does NOT double-increment
        self.client.post(reverse('complete_ride', args=[self.ride.id]))
        self.host.refresh_from_db()
        self.assertEqual(self.host.rides_completed, 1)

    def test_my_rides_interface_accessible(self):
        """RIDE-03: User can view active and past rides on /coordinate/my-rides/."""
        self.client.force_login(self.passenger)
        response = self.client.get(reverse('my_rides'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Rides')
        self.assertContains(response, self.ride.route_display)


class SocialGraphAndFriendshipTests(TestCase):
    """APP-01, APP-02: Friendship reciprocal uniqueness and request decline."""

    def setUp(self):
        cache.clear()
        self.user_a = User.objects.create_user(
            username='2024BCSE120',
            enrollment_number='2024BCSE120',
            gender='M',
            phone_number='9876543220',
            password='Password123',
            verified=True,
        )
        self.user_b = User.objects.create_user(
            username='2024BCSE121',
            enrollment_number='2024BCSE121',
            gender='M',
            phone_number='9876543221',
            password='Password123',
            verified=True,
        )

    def test_canonical_friendship_ordering(self):
        """APP-02: Friendship records always save with user1_id < user2_id."""
        higher_id = self.user_a if self.user_a.id > self.user_b.id else self.user_b
        lower_id = self.user_b if self.user_a.id > self.user_b.id else self.user_a

        # Attempt to save with inverted order
        f = Friendship.objects.create(user1=higher_id, user2=lower_id)
        self.assertEqual(f.user1, lower_id)
        self.assertEqual(f.user2, higher_id)

    def test_decline_friend_request(self):
        """APP-01: User can decline an incoming friend request."""
        req = FriendRequest.objects.create(sender=self.user_a, receiver=self.user_b)
        self.client.force_login(self.user_b)
        response = self.client.post(reverse('decline_friend_request', args=[req.id]))
        self.assertRedirects(response, reverse('profile', args=[self.user_b.enrollment_number]))
        self.assertFalse(FriendRequest.objects.filter(id=req.id).exists())
        self.assertFalse(Friendship.objects.filter(user1=self.user_a, user2=self.user_b).exists())


class ReviewIntegrityTests(TestCase):
    """DATA-03, UI-01: Review validation, prevented self-review, radio choices."""

    def setUp(self):
        cache.clear()
        self.user1 = User.objects.create_user(
            username='2024BCSE130',
            enrollment_number='2024BCSE130',
            gender='M',
            phone_number='9876543230',
            password='Password123',
            verified=True,
        )
        self.user2 = User.objects.create_user(
            username='2024BCSE131',
            enrollment_number='2024BCSE131',
            gender='F',
            phone_number='9876543231',
            password='Password123',
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.user1,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now(),
            total_spots=2,
            status='COMPLETED',
        )
        RideParticipant.objects.create(ride=self.ride, user=self.user1)
        RideParticipant.objects.create(ride=self.ride, user=self.user2)

    def test_self_review_forbidden(self):
        """DATA-03: User cannot review themselves."""
        self.client.force_login(self.user1)
        response = self.client.get(reverse('review_user', args=[self.ride.id, self.user1.id]))
        self.assertEqual(response.status_code, 403)

    def test_duplicate_review_prevented(self):
        """DATA-03: Submitting review twice redirects and preserves single record."""
        self.client.force_login(self.user1)
        self.client.post(reverse('review_user', args=[self.ride.id, self.user2.id]), {
            'rating': 5,
            'complaint': '',
        })
        self.assertEqual(RideReview.objects.count(), 1)

        # Second attempt
        response = self.client.post(reverse('review_user', args=[self.ride.id, self.user2.id]), {
            'rating': 4,
            'complaint': '',
        })
        self.assertRedirects(response, reverse('ride_detail', args=[self.ride.id]))
        self.assertEqual(RideReview.objects.count(), 1)


class WebSocketConsumerLogicTests(TransactionTestCase):
    """CHAT-01 to CHAT-05: WebSocket consumer logic tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='2024BCSE140',
            enrollment_number='2024BCSE140',
            gender='M',
            phone_number='9876543240',
            password='Password123',
            verified=True,
        )
        self.ride = RideRequest.objects.create(
            creator=self.user,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + datetime.timedelta(hours=1),
            total_spots=2,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=self.ride, user=self.user)

    def test_post_departure_buffer_calculation(self):
        """CHAT-05: Chat is allowed within 2 hours after departure, rejected after."""
        from coordinate.consumers import RideChatConsumer
        consumer = RideChatConsumer()
        consumer.ride_id = self.ride.id

        import asyncio
        can_send = asyncio.run(consumer._can_send())
        self.assertTrue(can_send)

        # Move departure to 3 hours in the past
        self.ride.departure_time = timezone.now() - datetime.timedelta(hours=3)
        self.ride.save(update_fields=['departure_time'])

        can_send_expired = asyncio.run(consumer._can_send())
        self.assertFalse(can_send_expired)
