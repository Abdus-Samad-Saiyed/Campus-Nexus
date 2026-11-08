"""Unit and integration tests verifying direct message responsiveness and deduplication."""

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse

from accounts.consumers import FriendChatConsumer
from accounts.models import DirectMessage, Friendship

User = get_user_model()


class DirectMessageResponsivenessTests(TransactionTestCase):

    def setUp(self):
        self.user1 = User.objects.create_user(
            username='2024BCSE901',
            enrollment_number='2024BCSE901',
            gender='M',
            phone_number='9876543901',
            password='Password123',
            verified=True,
        )
        self.user2 = User.objects.create_user(
            username='2024BCSE902',
            enrollment_number='2024BCSE902',
            gender='F',
            phone_number='9876543902',
            password='Password123',
            verified=True,
        )
        Friendship.objects.create(user1=self.user1, user2=self.user2)

    def test_http_post_deduplication(self):
        """Rapid double POST of the exact same message does not create duplicate DirectMessage records."""
        self.client.force_login(self.user1)
        url = reverse('direct_chat', args=[self.user2.enrollment_number])

        # Send first message
        res1 = self.client.post(url, {'content': 'Are you at the library?'})
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(DirectMessage.objects.filter(sender=self.user1, recipient=self.user2).count(), 1)

        # Immediate rapid second click (duplicate content)
        res2 = self.client.post(url, {'content': 'Are you at the library?'})
        self.assertEqual(res2.status_code, 302)
        # Count must remain exactly 1
        self.assertEqual(DirectMessage.objects.filter(sender=self.user1, recipient=self.user2).count(), 1)

    def test_ajax_post_returns_json_and_deduplicates(self):
        """AJAX submissions return JSON and safely deduplicate rapid clicks."""
        self.client.force_login(self.user1)
        url = reverse('direct_chat', args=[self.user2.enrollment_number])

        res = self.client.post(url, {'content': 'Hello friend!'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['content'], 'Hello friend!')
        self.assertFalse(data.get('is_duplicate', False))

        # Second rapid click via AJAX
        res_dup = self.client.post(url, {'content': 'Hello friend!'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_dup.status_code, 200)
        data_dup = res_dup.json()
        self.assertEqual(data_dup['status'], 'success')
        self.assertTrue(data_dup.get('is_duplicate', False))
        self.assertEqual(DirectMessage.objects.filter(sender=self.user1, recipient=self.user2).count(), 1)

    def test_ajax_post_with_accept_header(self):
        """Requests with Accept: application/json receive JSON responses instead of 302 redirects."""
        self.client.force_login(self.user1)
        url = reverse('direct_chat', args=[self.user2.enrollment_number])

        res = self.client.post(url, {'content': 'Mobile fetch test'}, HTTP_ACCEPT='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['content'], 'Mobile fetch test')

    def test_direct_chat_view_renders_properly(self):
        """GET /messages/<friend>/ renders HTML successfully containing direct chat container elements."""
        self.client.force_login(self.user1)
        url = reverse('direct_chat', args=[self.user2.enrollment_number])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'direct-chat-container')
        self.assertContains(res, 'direct-chat-footer')
        self.assertContains(res, 'reconcilePendingMessage')

    def test_consumer_save_direct_message_deduplication(self):
        """Consumer _save_direct_message returns existing message on rapid duplicate send."""
        consumer = FriendChatConsumer()
        consumer.user = self.user1
        consumer.friend = self.user2

        msg1 = async_to_sync(consumer._save_direct_message)("Testing consumer save")
        msg2 = async_to_sync(consumer._save_direct_message)("Testing consumer save")

        self.assertEqual(msg1.id, msg2.id)
        self.assertEqual(DirectMessage.objects.filter(sender=self.user1, recipient=self.user2).count(), 1)

    def test_consumer_mark_all_messages_read(self):
        """Consumer _mark_all_messages_read marks all unread incoming messages in one atomic operation."""
        # User 2 sends 3 messages to User 1
        DirectMessage.objects.create(sender=self.user2, recipient=self.user1, content="Msg 1")
        DirectMessage.objects.create(sender=self.user2, recipient=self.user1, content="Msg 2")
        DirectMessage.objects.create(sender=self.user2, recipient=self.user1, content="Msg 3")
        self.assertEqual(DirectMessage.objects.filter(sender=self.user2, recipient=self.user1, is_read=False).count(), 3)

        consumer = FriendChatConsumer()
        consumer.user = self.user1
        consumer.friend = self.user2

        read_time = async_to_sync(consumer._mark_all_messages_read)()
        self.assertIsNotNone(read_time)
        self.assertEqual(DirectMessage.objects.filter(sender=self.user2, recipient=self.user1, is_read=False).count(), 0)
