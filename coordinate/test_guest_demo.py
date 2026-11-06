"""Comprehensive test suite for Campus Nexus Guest Demo Mode.

Verifies:
1. Ephemeral third-database isolation (`guest_demo` DB).
2. Student privacy safeguards (zero student leakage in directory, profiles, messages, media).
3. Redaction of real student rides (timings & identities protected).
4. Prevention of joining or accessing real student coordination rooms.
5. Ephemeral test ride booking mechanism in third database.
6. Complete session cleanup on exit.
"""

import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.demo import (
    ensure_guest_demo_ready,
    ensure_guest_demo_user,
    is_guest_demo,
    cleanup_guest_demo_session,
)
from coordinate.models import RideRequest, RideParticipant, ParticipantStatus
from coordinate.policies import (
    is_verified_student,
    can_view_student_directory,
    can_view_profile_field,
    can_view_ride,
)

User = get_user_model()


class GuestDemoModeTests(TestCase):
    databases = {'default', 'guest_demo'}

    def setUp(self):
        # Create a real verified student in the default database
        self.student = User.objects.create_user(
            username='2024BCSE001',
            enrollment_number='2024BCSE001',
            password='TestPassword123!',
            first_name='Zaid',
            last_name='Khan',
            email='zaid@campus.internal',
            phone_number='9876543210',
            verified=True,
            is_active=True,
        )

        # Create another verified student co-rider
        self.student2 = User.objects.create_user(
            username='2024BCSE002',
            enrollment_number='2024BCSE002',
            password='TestPassword123!',
            first_name='Umar',
            last_name='Farooq',
            email='umar@campus.internal',
            phone_number='9876543211',
            verified=True,
            is_active=True,
        )

        # Create a real student ride in default database
        self.dept_time = timezone.now() + datetime.timedelta(days=2, hours=3)
        self.real_ride = RideRequest.objects.create(
            creator=self.student,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=self.dept_time,
            total_spots=4,
            status='OPEN',
        )
        RideParticipant.objects.create(
            ride=self.real_ride,
            user=self.student,
            status=ParticipantStatus.CONFIRMED,
        )
        RideParticipant.objects.create(
            ride=self.real_ride,
            user=self.student2,
            status=ParticipantStatus.CONFIRMED,
        )

        # Set up guest demo user
        ensure_guest_demo_ready()
        self.guest_user = User.objects.get(username='GUEST_DEMO')

    def test_policy_guarantees_guest_is_not_verified_student(self):
        """Guest demo user must strictly never be treated as a verified student."""
        self.assertFalse(is_verified_student(self.guest_user))
        self.assertFalse(can_view_student_directory(self.guest_user))
        self.assertFalse(can_view_profile_field(self.guest_user, self.student, 'email'))
        self.assertFalse(can_view_profile_field(self.guest_user, self.student, 'phone_number'))
        self.assertFalse(can_view_profile_field(self.guest_user, self.student, 'profile_photo'))

    def test_guest_demo_entry_and_session(self):
        """Entering guest demo sets session and logs in as GUEST_DEMO."""
        client = Client()
        response = client.get(reverse('enter_guest_demo'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(client.session.get('is_guest_demo'))
        self.assertEqual(response.context['user'].username, 'GUEST_DEMO')

    def test_guest_quarantine_student_directory(self):
        """Guest cannot access student directory; redirected to dashboard."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        response = client.get(reverse('user_search'), follow=True)
        self.assertEqual(response.status_code, 200)
        # Should be redirected to dashboard
        self.assertTemplateUsed(response, 'accounts/dashboard.html')
        # Student names and enrollments must not be leaked
        self.assertNotContains(response, '2024BCSE001')
        self.assertNotContains(response, 'Zaid Khan')

    def test_guest_quarantine_student_profile(self):
        """Guest cannot view student profiles."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        response = client.get(reverse('profile', kwargs={'enrollment_number': '2024BCSE001'}), follow=True)
        self.assertEqual(response.status_code, 200)
        # Redirected away to dashboard
        self.assertTemplateUsed(response, 'accounts/dashboard.html')
        self.assertNotContains(response, '9876543210')

    def test_guest_quarantine_messages_and_chat(self):
        """Guest cannot access student direct messaging or inbox."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        inbox_resp = client.get(reverse('messages_inbox'), follow=True)
        self.assertEqual(inbox_resp.status_code, 200)
        self.assertTemplateUsed(inbox_resp, 'accounts/dashboard.html')

        chat_resp = client.get(reverse('direct_chat', kwargs={'enrollment_number': '2024BCSE001'}), follow=True)
        self.assertEqual(chat_resp.status_code, 200)
        self.assertTemplateUsed(chat_resp, 'accounts/dashboard.html')

    def test_guest_quarantine_protected_media(self):
        """Guest is forbidden from accessing any protected media."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        response = client.get('/media/profiles/test_photo.jpg')
        self.assertEqual(response.status_code, 403)

    def test_guest_sees_redacted_student_rides_on_coordinate(self):
        """Guest sees real ride route & seat count, but timing, host name & co-riders are redacted."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        response = client.get(reverse('ride_list'))
        self.assertEqual(response.status_code, 200)

        # Route must be visible
        self.assertContains(response, 'NIT Srinagar → Airport')
        # Capacity must be visible (2 occupied / 4 total)
        self.assertContains(response, 'Occupied')

        # Personal details of student must be COMPLETELY REDACTED
        self.assertNotContains(response, '2024BCSE001')
        self.assertNotContains(response, 'Zaid Khan')
        self.assertNotContains(response, '2024BCSE002')
        self.assertNotContains(response, 'Umar Farooq')

        # Redacted indicators must be shown
        self.assertContains(response, 'Protected in Demo')
        self.assertContains(response, 'Verified Student (Protected)')

    def test_guest_cannot_join_or_waitlist_real_ride(self):
        """Guest clicking join or waitlist on real ride is rejected."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        # Attempt to join
        join_resp = client.post(reverse('join_ride', kwargs={'ride_id': self.real_ride.id}), follow=True)
        self.assertEqual(join_resp.status_code, 200)
        # Must not be added as participant
        self.assertFalse(RideParticipant.objects.filter(ride=self.real_ride, user=self.guest_user).exists())

        # Attempt to waitlist
        wait_resp = client.post(reverse('join_waitlist', kwargs={'ride_id': self.real_ride.id}), follow=True)
        self.assertEqual(wait_resp.status_code, 200)

    def test_guest_cannot_enter_real_ride_live_room(self):
        """Guest cannot access live coordination room of real student ride."""
        client = Client()
        client.get(reverse('enter_guest_demo'))

        response = client.get(reverse('ride_detail', kwargs={'ride_id': self.real_ride.id}), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'coordinate/ride_list.html')
        self.assertContains(response, 'Live coordination rooms for real student rides are strictly private')

    def test_guest_can_create_ephemeral_ride_in_third_db(self):
        """Guest can create a demo ride which is saved exclusively into guest_demo DB."""
        client = Client()
        client.get(reverse('enter_guest_demo'))
        session_key = client.session.session_key

        dept = timezone.now() + datetime.timedelta(days=1, hours=4)
        create_resp = client.post(reverse('create_ride'), {
            'route': 'NIT_TO_STATION',
            'visibility': 'MIXED',
            'departure_time': dept.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 3,
        }, follow=True)

        self.assertEqual(create_resp.status_code, 200)

        # Ride MUST NOT exist in default database
        self.assertEqual(RideRequest.objects.using('default').filter(route='NIT_TO_STATION').count(), 0)

        # Ride MUST exist in third database: guest_demo
        demo_rides_count = RideRequest.objects.using('guest_demo').filter(
            demo_session_key=session_key,
            route='NIT_TO_STATION',
        ).count()
        self.assertEqual(demo_rides_count, 1)

        # When browsing rides, guest sees their demo ride with DEMO TEST RIDE badge
        browse_resp = client.get(reverse('ride_list'))
        self.assertContains(browse_resp, 'DEMO TEST RIDE')
        self.assertContains(browse_resp, 'NIT Srinagar → Railway Station')

        # Real student browsing rides NEVER sees this demo ride
        student_client = Client()
        student_client.force_login(self.student)
        student_browse = student_client.get(reverse('ride_list'))
        self.assertNotContains(student_browse, 'DEMO TEST RIDE')

    def test_guest_session_cleanup_on_exit(self):
        """Exiting guest demo wipes all ephemeral demo rides from third database."""
        client = Client()
        client.get(reverse('enter_guest_demo'))
        session_key = client.session.session_key

        dept = timezone.now() + datetime.timedelta(days=1, hours=2)
        client.post(reverse('create_ride'), {
            'route': 'NIT_TO_STATION',
            'visibility': 'MIXED',
            'departure_time': dept.strftime('%Y-%m-%d %H:%M'),
            'total_spots': 4,
        })
        self.assertEqual(RideRequest.objects.using('guest_demo').filter(demo_session_key=session_key).count(), 1)

        # Exit demo
        exit_resp = client.get(reverse('exit_guest_demo'), follow=True)
        self.assertEqual(exit_resp.status_code, 200)
        self.assertTemplateUsed(exit_resp, 'landing.html')

        # Third database MUST now be empty of this session's rides
        self.assertEqual(RideRequest.objects.using('guest_demo').filter(demo_session_key=session_key).count(), 0)
