"""Test suite verifying multiple non-overlapping rides per student.

Validates:
1. Students can host multiple rides across different dates/weeks (e.g., Diwali trip, return trip, exam trip).
2. Students can participate in multiple non-overlapping rides simultaneously.
3. Rides departing within 2 hours of each other are strictly prevented from overlapping.
4. My Rides reflects all active non-overlapping participations.
"""

import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from coordinate.models import RideRequest, RideParticipant, ParticipantStatus

User = get_user_model()


class MultiRideNonOverlappingTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='2024BCSE080',
            enrollment_number='2024BCSE080',
            password='TestPassword123!',
            first_name='Aasif',
            last_name='Mir',
            verified=True,
            is_active=True,
        )

        self.student2 = User.objects.create_user(
            username='2024BCSE081',
            enrollment_number='2024BCSE081',
            password='TestPassword123!',
            first_name='Bilal',
            last_name='Dar',
            verified=True,
            is_active=True,
        )

        self.client = Client()
        self.client.force_login(self.student)

    def test_student_can_create_multiple_non_overlapping_rides(self):
        """Student can schedule a Diwali trip this week, return next week, and trip later this month."""
        t_diwali = timezone.now() + datetime.timedelta(days=2, hours=4)
        t_return = timezone.now() + datetime.timedelta(days=6, hours=10)
        t_exams = timezone.now() + datetime.timedelta(days=20, hours=2)

        # 1. Create Diwali trip
        resp1 = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': t_diwali.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 4,
        }, follow=True)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(RideRequest.objects.filter(creator=self.student).count(), 1)

        # 2. Create return trip (4 days later)
        resp2 = self.client.post(reverse('create_ride'), {
            'route': 'AIRPORT_TO_NIT',
            'visibility': 'MIXED',
            'departure_time': t_return.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 4,
        }, follow=True)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(RideRequest.objects.filter(creator=self.student).count(), 2)

        # 3. Create post-exam trip (later this month)
        resp3 = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_STATION',
            'visibility': 'MIXED',
            'departure_time': t_exams.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 3,
        }, follow=True)
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(RideRequest.objects.filter(creator=self.student).count(), 3)

        # Verify My Rides lists all three scheduled active rides
        my_rides_resp = self.client.get(reverse('my_rides'))
        self.assertEqual(len(my_rides_resp.context['active_rides']), 3)

    def test_overlapping_ride_creation_is_rejected(self):
        """Creating a ride departing within 2 hours of an existing confirmed ride is rejected."""
        t_base = timezone.now() + datetime.timedelta(days=3, hours=10)
        t_overlap = t_base + datetime.timedelta(hours=1)  # 1 hour later (within 2-hour window)

        # Create base ride
        self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_AIRPORT',
            'visibility': 'MIXED',
            'departure_time': t_base.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 4,
        })
        self.assertEqual(RideRequest.objects.filter(creator=self.student).count(), 1)

        # Attempt to create overlapping ride
        resp_overlap = self.client.post(reverse('create_ride'), {
            'route': 'NIT_TO_STATION',
            'visibility': 'MIXED',
            'departure_time': t_overlap.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 3,
        })
        # Should not create ride and should show validation error on form
        self.assertEqual(RideRequest.objects.filter(creator=self.student).count(), 1)
        form = resp_overlap.context['form']
        self.assertTrue(form.errors.get('departure_time'))
        self.assertIn('Rides cannot overlap', form.errors['departure_time'][0])

    def test_student_can_join_multiple_non_overlapping_rides(self):
        """Student can be a confirmed participant in different non-overlapping rides."""
        t_ride1 = timezone.now() + datetime.timedelta(days=2)
        t_ride2 = timezone.now() + datetime.timedelta(days=5)

        # Student2 creates two rides on different days
        ride1 = RideRequest.objects.create(
            creator=self.student2,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=t_ride1,
            total_spots=4,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=ride1, user=self.student2, status=ParticipantStatus.CONFIRMED)

        ride2 = RideRequest.objects.create(
            creator=self.student2,
            route='AIRPORT_TO_NIT',
            visibility='MIXED',
            departure_time=t_ride2,
            total_spots=4,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=ride2, user=self.student2, status=ParticipantStatus.CONFIRMED)

        # Student requests to join both rides
        self.client.post(reverse('join_ride', kwargs={'ride_id': ride1.id}))
        self.client.post(reverse('join_ride', kwargs={'ride_id': ride2.id}))

        # Student2 approves both requests
        c2 = Client()
        c2.force_login(self.student2)

        req1 = ride1.join_requests.get(user=self.student)
        c2.post(reverse('approve_join_request', kwargs={'ride_id': ride1.id, 'request_id': req1.id}))

        req2 = ride2.join_requests.get(user=self.student)
        c2.post(reverse('approve_join_request', kwargs={'ride_id': ride2.id, 'request_id': req2.id}))

        # Student should now be confirmed participant in both non-overlapping rides
        self.assertTrue(RideParticipant.objects.filter(ride=ride1, user=self.student, status=ParticipantStatus.CONFIRMED).exists())
        self.assertTrue(RideParticipant.objects.filter(ride=ride2, user=self.student, status=ParticipantStatus.CONFIRMED).exists())

    def test_joining_overlapping_ride_is_prevented(self):
        """Student cannot join a ride that departs within 2 hours of another confirmed ride."""
        t_base = timezone.now() + datetime.timedelta(days=4, hours=10)
        t_conflict = t_base + datetime.timedelta(minutes=45)

        # Student confirmed in Ride 1
        ride1 = RideRequest.objects.create(
            creator=self.student,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=t_base,
            total_spots=4,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=ride1, user=self.student, status=ParticipantStatus.CONFIRMED)

        # Student 2 creates conflicting Ride 2
        ride2 = RideRequest.objects.create(
            creator=self.student2,
            route='NIT_TO_STATION',
            visibility='MIXED',
            departure_time=t_conflict,
            total_spots=4,
            status='OPEN',
        )
        RideParticipant.objects.create(ride=ride2, user=self.student2, status=ParticipantStatus.CONFIRMED)

        # Student attempts to join conflicting ride2
        resp = self.client.post(reverse('join_ride', kwargs={'ride_id': ride2.id}), follow=True)
        self.assertEqual(resp.status_code, 200)
        # Join request should not be created
        self.assertFalse(ride2.join_requests.filter(user=self.student).exists())
