from django.contrib.auth import authenticate
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AccountApplication, FriendRequest, Friendship, User
from .two_factor import create_phone_otp


class AccountWorkflowTests(TestCase):

    def setUp(self):

        self.admin = User.objects.create_superuser(
            username='Admin',
            enrollment_number='2024BCSE010',
            gender='M',
            phone_number='9999999900',
            password='password123'
        )

        self.user = User.objects.create_user(
            username='User',
            enrollment_number='2024BCSE011',
            gender='M',
            phone_number='9999999901',
            password='password123',
            verified=True,
        )

        self.friend = User.objects.create_user(
            username='Friend',
            enrollment_number='2024BCSE012',
            gender='F',
            phone_number='9999999902',
            password='password123',
            verified=True,
        )

    def test_valid_application_normalizes_and_hashes_password(self):
        _, raw_otp = create_phone_otp(phone_number='9999999903', email='applicant2024@nitsri.ac.in', purpose='SIGNUP')
        response = self.client.post(
            reverse('apply_account'),
            {
                'enrollment_number': '2024bcse013',
                'full_name': 'Applicant',
                'phone_number': '9999999903',
                'email': 'applicant2024@nitsri.ac.in',
                'phone_otp': raw_otp,
                'gender': 'M',
                'password': 'CampusPass@2024!',
                'confirm_password': 'CampusPass@2024!',
            }
        )

        self.assertEqual(response.status_code, 200)

        application = AccountApplication.objects.get()

        self.assertEqual(
            application.enrollment_number,
            '2024BCSE013'
        )
        self.assertEqual(
            application.email,
            'applicant2024@nitsri.ac.in'
        )
        self.assertNotEqual(
            application.password,
            'CampusPass@2024!'
        )

    def test_invalid_and_duplicate_applications_are_rejected(self):

        response = self.client.post(
            reverse('apply_account'),
            {
                'enrollment_number': 'bad',
                'full_name': 'Applicant',
                'phone_number': '9999999903',
                'email': 'applicant2024@nitsri.ac.in',
                'gender': 'M',
                'password': 'password12345',
            }
        )

        self.assertContains(
            response,
            'Invalid enrollment number format'
        )

        AccountApplication.objects.create(
            enrollment_number='2024BCSE013',
            full_name='Applicant',
            phone_number='9999999903',
            email='applicant2024@nitsri.ac.in',
            gender='M',
            password='hashed',
            application_number='APP001'
        )

        response = self.client.post(
            reverse('apply_account'),
            {
                'enrollment_number': '2024BCSE013',
                'full_name': 'Applicant',
                'phone_number': '9999999903',
                'email': 'applicant2024@nitsri.ac.in',
                'gender': 'M',
                'password': 'password12345',
            }
        )

        self.assertContains(
            response,
            'already pending'
        )

    def test_approval_requires_post_and_admin(self):

        application = AccountApplication.objects.create(
            enrollment_number='2024BCSE013',
            full_name='Applicant',
            phone_number='9999999903',
            email='applicant2024@nitsri.ac.in',
            gender='M',
            password='pbkdf2_sha256$1000000$example$hash',
            application_number='APP001'
        )

        self.client.force_login(self.user)

        self.assertEqual(
            self.client.post(
                reverse('approve_application', args=[application.id])
            ).status_code,
            302
        )

        self.assertTrue(
            AccountApplication.objects.filter(
                id=application.id
            ).exists()
        )

        self.client.force_login(self.admin)

        self.assertEqual(
            self.client.get(
                reverse('approve_application', args=[application.id])
            ).status_code,
            405
        )

        self.client.post(
            reverse('approve_application', args=[application.id])
        )

        self.assertTrue(
            User.objects.filter(
                enrollment_number='2024BCSE013'
            ).exists()
        )
        self.assertFalse(
            AccountApplication.objects.filter(
                id=application.id
            ).exists()
        )

    def test_enrollment_number_authentication(self):

        user = authenticate(
            enrollment_number='2024BCSE011',
            password='password123'
        )

        self.assertEqual(user, self.user)

    def test_friend_request_requires_post_and_accepts_once(self):

        self.client.force_login(self.user)

        self.assertEqual(
            self.client.get(
                reverse(
                    'send_friend_request',
                    args=[self.friend.enrollment_number]
                )
            ).status_code,
            405
        )

        self.client.post(
            reverse(
                'send_friend_request',
                args=[self.friend.enrollment_number]
            )
        )
        self.client.post(
            reverse(
                'send_friend_request',
                args=[self.friend.enrollment_number]
            )
        )

        self.assertEqual(FriendRequest.objects.count(), 1)

        friend_request = FriendRequest.objects.get()

        self.client.force_login(self.friend)
        self.client.post(
            reverse(
                'accept_friend_request',
                args=[friend_request.id]
            )
        )

        self.assertEqual(Friendship.objects.count(), 1)
        self.assertEqual(FriendRequest.objects.count(), 0)

        # Resubmitting the same accepted request ID redirects gracefully with info message (no 404)
        repeat_res = self.client.post(
            reverse('accept_friend_request', args=[friend_request.id])
        )
        self.assertEqual(repeat_res.status_code, 302)

    def test_accept_friend_request_by_user_id_and_enrollment(self):
        """accept_friend_request accepts by sender User ID or enrollment number without 404."""
        # 1. Accept by user ID
        self.client.force_login(self.user)
        self.client.post(reverse('send_friend_request', args=[self.friend.enrollment_number]))
        self.client.force_login(self.friend)
        res1 = self.client.post(reverse('accept_friend_request', args=[self.user.id]))
        self.assertEqual(res1.status_code, 302)
        self.assertTrue(Friendship.objects.filter(user1=self.user, user2=self.friend).exists() or
                        Friendship.objects.filter(user1=self.friend, user2=self.user).exists())

        # Clean up friendship for enrollment test
        Friendship.objects.all().delete()

        # 2. Accept by enrollment number string
        self.client.force_login(self.user)
        self.client.post(reverse('send_friend_request', args=[self.friend.enrollment_number]))
        self.client.force_login(self.friend)
        res2 = self.client.post(reverse('accept_friend_request', args=[self.user.enrollment_number]))
        self.assertEqual(res2.status_code, 302)
        self.assertTrue(Friendship.objects.filter(user1=self.user, user2=self.friend).exists() or
                        Friendship.objects.filter(user1=self.friend, user2=self.user).exists())

    def test_accept_nonexistent_friend_request_does_not_404(self):
        """Accepting an unknown or expired friend request redirects gracefully without throwing 404."""
        self.client.force_login(self.user)
        res = self.client.post(reverse('accept_friend_request', args=[999999]))
        self.assertEqual(res.status_code, 302)

    def test_profile_photo_replaces_and_deletes_old_file_from_storage(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.core.files.storage import default_storage

        # 1. Upload first photo
        file1 = SimpleUploadedFile('avatar1.jpg', b'image_content_1', content_type='image/jpeg')
        self.user.profile_photo = file1
        self.user.save()

        path1 = self.user.profile_photo.name
        self.assertTrue(default_storage.exists(path1))

        # 2. Upload second photo (replacing first)
        file2 = SimpleUploadedFile('avatar2.jpg', b'image_content_2', content_type='image/jpeg')
        self.user.profile_photo = file2
        with self.captureOnCommitCallbacks(execute=True):
            self.user.save()

        path2 = self.user.profile_photo.name
        self.assertNotEqual(path1, path2)
        # Previous photo must be deleted from storage
        self.assertFalse(default_storage.exists(path1))
        # New photo must exist
        self.assertTrue(default_storage.exists(path2))

        # 3. Deleting the user cleans up the remaining photo
        self.user.delete()
        self.assertFalse(default_storage.exists(path2))

    def test_cleanup_orphaned_media_command(self):
        from django.core.management import call_command
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.core.files.storage import default_storage
        from io import StringIO

        # 1. Real user photo
        file_real = SimpleUploadedFile('real_avatar.jpg', b'real_image_data', content_type='image/jpeg')
        self.user.profile_photo = file_real
        self.user.save()
        real_path = self.user.profile_photo.name

        # 2. Orphaned fake file manually placed in profiles storage
        orphan_rel_path = 'profiles/fake_orphan.jpg'
        default_storage.save(orphan_rel_path, SimpleUploadedFile('fake_orphan.jpg', b'fake_data'))
        self.assertTrue(default_storage.exists(orphan_rel_path))

        # 3. Dry run: does not delete
        out = StringIO()
        call_command('cleanup_orphaned_media', '--dry-run', stdout=out)
        self.assertIn('[DRY-RUN] Would delete', out.getvalue())
        self.assertTrue(default_storage.exists(orphan_rel_path))
        self.assertTrue(default_storage.exists(real_path))

        # 4. Actual run: purges orphan, preserves real user photo
        out2 = StringIO()
        call_command('cleanup_orphaned_media', stdout=out2)
        self.assertIn('Successfully purged', out2.getvalue())
        self.assertFalse(default_storage.exists(orphan_rel_path))
        self.assertTrue(default_storage.exists(real_path))

    def test_send_friend_request_to_inactive_or_unverified_rejected(self):
        """Cannot send friend requests to inactive or unverified users."""
        unverified_target = User.objects.create_user(
            username='2024BCSE076',
            enrollment_number='2024BCSE076',
            gender='M',
            phone_number='9999999976',
            password='password123',
            verified=False,
            is_active=True,
        )
        self.client.force_login(self.friend)
        resp = self.client.post(reverse('send_friend_request', args=[unverified_target.enrollment_number]))
        self.assertRedirects(resp, reverse('user_search'))
        self.assertFalse(FriendRequest.objects.filter(receiver=unverified_target).exists())

    def test_two_factor_verify_blocks_deactivated_user(self):
        """User deactivated while pending 2FA cannot complete sign-in."""
        session = self.client.session
        session['pre_2fa_user_id'] = self.friend.id
        session['pre_2fa_time'] = timezone.now().timestamp()
        session.save()

        self.friend.is_active = False
        self.friend.save(update_fields=['is_active'])

        resp = self.client.post(reverse('two_factor_verify'), {'action': 'verify', 'otp_code': '123456'})
        self.assertRedirects(resp, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)
    def test_dashboard_displays_recent_conversations_and_unread_counts(self):
        """Dashboard displays friend conversations with unread badges and snippets."""
        from accounts.models import DirectMessage
        Friendship.objects.create(user1=self.user, user2=self.friend)
        DirectMessage.objects.create(
            sender=self.friend,
            recipient=self.user,
            content="Hey are you ready for the commute?",
            is_read=False,
        )

        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('recent_conversations', resp.context)
        self.assertEqual(len(resp.context['recent_conversations']), 1)
        self.assertEqual(resp.context['total_unread_messages'], 1)
        self.assertIn("Hey are you ready for the commute?", resp.content.decode())

    def test_direct_chat_post_creates_message_and_marks_read_on_view(self):
        """Direct chat HTTP POST saves message and viewing chat marks them read."""
        from accounts.models import DirectMessage
        Friendship.objects.create(user1=self.user, user2=self.friend)

        self.client.force_login(self.user)
        post_resp = self.client.post(
            reverse('direct_chat', args=[self.friend.enrollment_number]),
            {'content': 'Hello from sender!'}
        )
        self.assertRedirects(post_resp, reverse('direct_chat', args=[self.friend.enrollment_number]))

        msg = DirectMessage.objects.get(sender=self.user, recipient=self.friend)
        self.assertEqual(msg.content, 'Hello from sender!')
        self.assertFalse(msg.is_read)

        # Recipient opens chat room
        self.client.force_login(self.friend)
        get_resp = self.client.get(reverse('direct_chat', args=[self.user.enrollment_number]))
        self.assertEqual(get_resp.status_code, 200)

        msg.refresh_from_db()
        self.assertTrue(msg.is_read)

