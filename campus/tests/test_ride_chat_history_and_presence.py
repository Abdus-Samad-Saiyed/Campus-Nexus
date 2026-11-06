"""Tests for ride chat history isolation, participant profile links, presence, and read receipts."""

import datetime
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import DirectMessage, User
from coordinate.models import ParticipantStatus, RideMessage, RideParticipant, RideRequest


class RideChatHistoryAndPresenceTests(TestCase):

    def setUp(self):
        self.host = User.objects.create_user(
            username='2024BCSE601',
            enrollment_number='2024BCSE601',
            gender='M',
            password='Password123',
            verified=True,
            dm_read_receipts=True,
        )
        self.late_joiner = User.objects.create_user(
            username='2024BCSE602',
            enrollment_number='2024BCSE602',
            gender='F',
            password='Password123',
            verified=True,
            dm_read_receipts=False,  # DM read receipts turned off
        )
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route='NIT_TO_AIRPORT',
            visibility='MIXED',
            departure_time=timezone.now() + datetime.timedelta(days=2),
            total_spots=4,
            status='OPEN',
            share_chat_history=False,
        )
        RideParticipant.objects.create(
            ride=self.ride,
            user=self.host,
            status=ParticipantStatus.CONFIRMED,
            joined_at=timezone.now() - datetime.timedelta(hours=2),
        )

        # Host sends message 1 hour ago
        self.msg1 = RideMessage.objects.create(
            ride=self.ride,
            author=self.host,
            content='Message sent before joiner joined.',
            created_at=timezone.now() - datetime.timedelta(hours=1),
        )

    def test_later_joiner_cannot_see_past_messages_by_default(self):
        """Late joiner should NOT see messages sent prior to their join timestamp when share_chat_history is False."""
        # Late joiner joins now
        joiner_part = RideParticipant.objects.create(
            ride=self.ride,
            user=self.late_joiner,
            status=ParticipantStatus.CONFIRMED,
        )

        # Host can see the message
        self.client.force_login(self.host)
        res_host = self.client.get(reverse('ride_detail', args=[self.ride.id]))
        self.assertEqual(res_host.status_code, 200)
        self.assertContains(res_host, self.msg1.content)

        # Late joiner views the ride: cannot see msg1
        self.client.force_login(self.late_joiner)
        res_joiner = self.client.get(reverse('ride_detail', args=[self.ride.id]))
        self.assertEqual(res_joiner.status_code, 200)
        self.assertNotContains(res_joiner, self.msg1.content)

        # New message sent after late joiner joined
        msg2 = RideMessage.objects.create(
            ride=self.ride,
            author=self.host,
            content='Welcome new passenger!',
            created_at=timezone.now() + datetime.timedelta(seconds=2),
        )
        res_joiner2 = self.client.get(reverse('ride_detail', args=[self.ride.id]))
        self.assertContains(res_joiner2, msg2.content)
        self.assertNotContains(res_joiner2, self.msg1.content)

    def test_share_chat_history_allows_late_joiner_to_see_past_messages(self):
        """When share_chat_history is toggled/enabled, late joiners CAN see all past messages."""
        RideParticipant.objects.create(
            ride=self.ride,
            user=self.late_joiner,
            status=ParticipantStatus.CONFIRMED,
        )

        # Host toggles chat history
        self.client.force_login(self.host)
        toggle_res = self.client.post(reverse('toggle_ride_chat_history', args=[self.ride.id]))
        self.assertRedirects(toggle_res, reverse('ride_detail', args=[self.ride.id]))

        self.ride.refresh_from_db()
        self.assertTrue(self.ride.share_chat_history)

        # Now late joiner CAN see past message
        self.client.force_login(self.late_joiner)
        res_joiner = self.client.get(reverse('ride_detail', args=[self.ride.id]))
        self.assertEqual(res_joiner.status_code, 200)
        self.assertContains(res_joiner, self.msg1.content)

    def test_participant_sidebar_links_to_profile(self):
        """Clicking on participant in ride sidebar links directly to their profile page."""
        RideParticipant.objects.create(
            ride=self.ride,
            user=self.late_joiner,
            status=ParticipantStatus.CONFIRMED,
        )
        self.client.force_login(self.host)
        res = self.client.get(reverse('ride_detail', args=[self.ride.id]))
        self.assertEqual(res.status_code, 200)
        profile_url = reverse('profile', args=[self.late_joiner.enrollment_number])
        self.assertContains(res, f'href="{profile_url}"')

    def test_dm_read_receipts_toggle_in_profile_settings(self):
        """Users can toggle DM read receipts setting via profile settings."""
        self.client.force_login(self.host)
        self.assertTrue(self.host.dm_read_receipts)

        # Disable DM read receipts
        self.client.post(reverse('profile', args=[self.host.enrollment_number]), {
            'bio': 'Updated bio',
            'show_friends_publicly': 'on',
            # 'dm_read_receipts' omitted => False
        })
        self.host.refresh_from_db()
        self.assertFalse(self.host.dm_read_receipts)

        # Re-enable DM read receipts
        self.client.post(reverse('profile', args=[self.host.enrollment_number]), {
            'bio': 'Updated bio',
            'show_friends_publicly': 'on',
            'dm_read_receipts': 'on',
        })
        self.host.refresh_from_db()
        self.assertTrue(self.host.dm_read_receipts)
