"""Tests for Friend Direct Messaging (1-on-1 Chat), Profile Photo, and Friends List Privacy."""

from django.test import TestCase
from django.urls import reverse

from datetime import timedelta
from django.utils import timezone
from accounts.models import DirectMessage, DirectMessageReport, Friendship, User


class FriendDirectChatAndPrivacyTests(TestCase):

    def setUp(self):
        self.user1 = User.objects.create_user(
            username='2024BCSE301',
            enrollment_number='2024BCSE301',
            gender='M',
            phone_number='9876543301',
            password='Password123',
            verified=True,
            show_friends_publicly=True,
        )
        self.user2 = User.objects.create_user(
            username='2024BCSE302',
            enrollment_number='2024BCSE302',
            gender='F',
            phone_number='9876543302',
            password='Password123',
            verified=True,
            show_friends_publicly=False,  # Private friends list
        )
        self.stranger = User.objects.create_user(
            username='2024BCSE303',
            enrollment_number='2024BCSE303',
            gender='M',
            phone_number='9876543303',
            password='Password123',
            verified=True,
        )

        # Make user1 and user2 friends
        Friendship.objects.create(user1=self.user1, user2=self.user2)

    def test_direct_messaging_between_friends(self):
        """Friends can exchange direct messages and view conversation history."""
        self.client.force_login(self.user1)

        # Send direct message via HTTP fallback / form
        response = self.client.post(
            reverse('direct_chat', args=[self.user2.enrollment_number]),
            {'content': 'Hello, are you taking the morning cab?'}
        )
        self.assertRedirects(response, reverse('direct_chat', args=[self.user2.enrollment_number]))

        # Verify message created
        msg = DirectMessage.objects.filter(sender=self.user1, recipient=self.user2).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.content, 'Hello, are you taking the morning cab?')
        self.assertFalse(msg.is_read)

        # Recipient opens chat room: message is marked read
        self.client.force_login(self.user2)
        resp2 = self.client.get(reverse('direct_chat', args=[self.user1.enrollment_number]))
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, 'Hello, are you taking the morning cab?')

        msg.refresh_from_db()
        self.assertTrue(msg.is_read)
        self.assertIsNotNone(msg.read_at)
        self.assertIn("Today", msg.read_at_display)
        self.assertTrue(len(msg.read_at_time) >= 4)

    def test_non_friends_can_access_and_send_direct_chat(self):
        """Non-friends can directly message each other without prior friendship."""
        self.client.force_login(self.stranger)
        response = self.client.get(reverse('direct_chat', args=[self.user1.enrollment_number]))
        self.assertEqual(response.status_code, 200)

        # Stranger can send message to user1
        post_resp = self.client.post(
            reverse('direct_chat', args=[self.user1.enrollment_number]),
            {'content': 'Hi user1, reaching out directly!'},
        )
        self.assertRedirects(post_resp, reverse('direct_chat', args=[self.user1.enrollment_number]))
        self.assertTrue(
            DirectMessage.objects.filter(
                sender=self.stranger,
                recipient=self.user1,
                content='Hi user1, reaching out directly!'
            ).exists()
        )

    def test_report_direct_chat_without_transcript(self):
        """Report can be filed without sharing chat history."""
        self.client.force_login(self.user1)
        resp = self.client.post(
            reverse('report_direct_chat', args=[self.user2.enrollment_number]),
            {
                'reason_category': 'SPAM_SCAM',
                'statement': 'Sending unsolicited sales links.',
            }
        )
        self.assertRedirects(resp, reverse('direct_chat', args=[self.user2.enrollment_number]))

        report = DirectMessageReport.objects.filter(reporter=self.user1, reported_user=self.user2).first()
        self.assertIsNotNone(report)
        self.assertEqual(report.reason_category, 'SPAM_SCAM')
        self.assertEqual(report.statement, 'Sending unsolicited sales links.')
        self.assertFalse(report.share_transcript)
        self.assertEqual(report.transcript_timeframe, '')
        self.assertEqual(report.transcript_snapshot, '')
        self.assertEqual(report.transcript_message_count, 0)

    def test_report_direct_chat_with_transcript_timeframes(self):
        """Test evidentiary transcript filtering across 15m, 1h, 1d, and lifetime."""
        now = timezone.now()

        # Message 10 minutes ago
        m1 = DirectMessage.objects.create(
            sender=self.user2, recipient=self.user1, content='Recent message (10m ago)'
        )
        DirectMessage.objects.filter(id=m1.id).update(created_at=now - timedelta(minutes=10))

        # Message 30 minutes ago
        m2 = DirectMessage.objects.create(
            sender=self.user2, recipient=self.user1, content='Older message (30m ago)'
        )
        DirectMessage.objects.filter(id=m2.id).update(created_at=now - timedelta(minutes=30))

        # Message 5 hours ago
        m3 = DirectMessage.objects.create(
            sender=self.user1, recipient=self.user2, content='Hours ago message (5h ago)'
        )
        DirectMessage.objects.filter(id=m3.id).update(created_at=now - timedelta(hours=5))

        # Message 3 days ago
        m4 = DirectMessage.objects.create(
            sender=self.user2, recipient=self.user1, content='Ancient message (3d ago)'
        )
        DirectMessage.objects.filter(id=m4.id).update(created_at=now - timedelta(days=3))

        self.client.force_login(self.user1)

        # 1. 15 minutes timeframe: should only include m1
        self.client.post(
            reverse('report_direct_chat', args=[self.user2.enrollment_number]),
            {
                'reason_category': 'HARASSMENT',
                'statement': 'Harassment in last 15 mins',
                'share_transcript': '1',
                'transcript_timeframe': '15_MINUTES',
            }
        )
        rep15 = DirectMessageReport.objects.filter(transcript_timeframe='15_MINUTES').first()
        self.assertIsNotNone(rep15)
        self.assertEqual(rep15.transcript_message_count, 1)
        self.assertIn('Recent message (10m ago)', rep15.transcript_snapshot)
        self.assertNotIn('Older message (30m ago)', rep15.transcript_snapshot)

        # 2. 1 hour timeframe: should include m1 and m2
        self.client.post(
            reverse('report_direct_chat', args=[self.user2.enrollment_number]),
            {
                'reason_category': 'HARASSMENT',
                'statement': 'Harassment in last hour',
                'share_transcript': '1',
                'transcript_timeframe': '1_HOUR',
            }
        )
        rep1h = DirectMessageReport.objects.filter(transcript_timeframe='1_HOUR').first()
        self.assertIsNotNone(rep1h)
        self.assertEqual(rep1h.transcript_message_count, 2)
        self.assertIn('Recent message (10m ago)', rep1h.transcript_snapshot)
        self.assertIn('Older message (30m ago)', rep1h.transcript_snapshot)
        self.assertNotIn('Hours ago message (5h ago)', rep1h.transcript_snapshot)

        # 3. 1 day timeframe: should include m1, m2, m3
        self.client.post(
            reverse('report_direct_chat', args=[self.user2.enrollment_number]),
            {
                'reason_category': 'HARASSMENT',
                'statement': 'Harassment today',
                'share_transcript': '1',
                'transcript_timeframe': '1_DAY',
            }
        )
        rep1d = DirectMessageReport.objects.filter(transcript_timeframe='1_DAY').first()
        self.assertIsNotNone(rep1d)
        self.assertEqual(rep1d.transcript_message_count, 3)
        self.assertIn('Hours ago message (5h ago)', rep1d.transcript_snapshot)
        self.assertNotIn('Ancient message (3d ago)', rep1d.transcript_snapshot)

        # 4. Lifetime timeframe: should include all 4 messages
        self.client.post(
            reverse('report_direct_chat', args=[self.user2.enrollment_number]),
            {
                'reason_category': 'HARASSMENT',
                'statement': 'Full history report',
                'share_transcript': '1',
                'transcript_timeframe': 'LIFETIME',
            }
        )
        replife = DirectMessageReport.objects.filter(transcript_timeframe='LIFETIME').first()
        self.assertIsNotNone(replife)
        self.assertEqual(replife.transcript_message_count, 4)
        self.assertIn('Ancient message (3d ago)', replife.transcript_snapshot)

    def test_report_increments_behavior_reports_counter(self):
        """Filing a report increments the reported user's behavior_reports counter."""
        initial_reports = self.user2.behavior_reports
        self.client.force_login(self.user1)
        self.client.post(
            reverse('report_direct_chat', args=[self.user2.enrollment_number]),
            {
                'reason_category': 'OTHER',
                'statement': 'Inappropriate behavior',
            }
        )
        self.user2.refresh_from_db()
        self.assertEqual(self.user2.behavior_reports, initial_reports + 1)

    def test_cannot_report_self(self):
        """Users cannot submit a conduct report against themselves."""
        self.client.force_login(self.user1)
        resp = self.client.post(
            reverse('report_direct_chat', args=[self.user1.enrollment_number]),
            {
                'reason_category': 'OTHER',
            }
        )
        self.assertRedirects(resp, reverse('dashboard'))
        self.assertFalse(DirectMessageReport.objects.filter(reporter=self.user1, reported_user=self.user1).exists())

    def test_messages_inbox_lists_conversations(self):
        """Messages inbox lists friends and displays unread count."""
        DirectMessage.objects.create(
            sender=self.user1,
            recipient=self.user2,
            content='Test unread message',
            is_read=False,
        )

        self.client.force_login(self.user2)
        response = self.client.get(reverse('messages_inbox'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user1.enrollment_number)
        self.assertContains(response, 'Test unread message')

    def test_friend_list_privacy_toggle(self):
        """Public friend list is visible, but private friend list is hidden to non-moderator strangers."""
        self.client.force_login(self.stranger)

        # user1 has show_friends_publicly = True
        resp1 = self.client.get(reverse('profile', args=[self.user1.enrollment_number]))
        self.assertContains(resp1, self.user2.enrollment_number)

        # user2 has show_friends_publicly = False
        resp2 = self.client.get(reverse('profile', args=[self.user2.enrollment_number]))
        self.assertContains(resp2, 'Friend list is private')
        self.assertNotContains(resp2, self.user1.enrollment_number)

    def test_unfriend_user(self):
        """Users can terminate friendship."""
        self.client.force_login(self.user1)
        response = self.client.post(reverse('unfriend_user', args=[self.user2.enrollment_number]))
        self.assertRedirects(response, reverse('profile', args=[self.user2.enrollment_number]))
        self.assertFalse(Friendship.objects.filter(user1=self.user1, user2=self.user2).exists())
