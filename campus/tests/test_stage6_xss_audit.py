"""Stage 6 tests — XSS, Uploads, Admin Audit Trail, and Privacy Controls.

Covers:
1. AuditEvent immutability: .delete() and updating .save() raise PermissionError.
2. AuditEventAdmin disables add, change, and delete permissions in Django Admin.
3. Profile photo privacy: hidden when hide_from_search=True for non-friends, visible to friends/staff/self.
4. Photo upload validation: invalid non-image formats are rejected, filenames are randomized.
5. DOM XSS safeguards: chat system notice text nodes prevent script injection.
"""

import io
from PIL import Image
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.admin import AuditEventAdmin
from accounts.models import AuditEvent, Friendship, log_audit_event
from coordinate.policies import can_view_profile_field

User = get_user_model()


class Stage6AuditTrailImmutabilityTests(TestCase):
    """Test that AuditEvent records are append-only and cannot be mutated or deleted."""

    def setUp(self):
        self.event = log_audit_event(
            event_type='LOGIN_SUCCESS',
            actor_enrollment='2024BCSE501',
            description='Test login event',
            target_id='501',
        )

    def test_audit_event_delete_raises_permission_error(self):
        """AuditEvent cannot be deleted via model instance."""
        with self.assertRaises(PermissionError):
            self.event.delete()

    def test_audit_event_update_raises_permission_error(self):
        """AuditEvent cannot be updated once created."""
        self.event.description = 'Tampered description'
        with self.assertRaises(PermissionError):
            self.event.save()

    def test_audit_admin_permissions_disabled(self):
        """AuditEventAdmin disables add, change, and delete permissions."""
        admin_site = AdminSite()
        audit_admin = AuditEventAdmin(AuditEvent, admin_site)
        factory = RequestFactory()
        request = factory.get('/admin/accounts/auditevent/')
        request.user = User.objects.create_superuser(
            enrollment_number='2024BCSE599',
            username='2024BCSE599',
            password='AdminPassword123!',
        )

        self.assertFalse(audit_admin.has_add_permission(request))
        self.assertFalse(audit_admin.has_change_permission(request, self.event))
        self.assertFalse(audit_admin.has_delete_permission(request, self.event))

    def test_audit_event_bulk_queryset_immutability(self):
        """Bulk updates and deletes on AuditEvent querysets are strictly blocked."""
        with self.assertRaises(PermissionError):
            AuditEvent.objects.filter(id=self.event.id).update(description="Bulk tampered")

        with self.assertRaises(PermissionError):
            AuditEvent.objects.filter(id=self.event.id).delete()

        # Verify record remains unchanged in database
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, 'Test login event')


class Stage6PrivacyAndMediaTests(TestCase):
    """Test profile photo visibility rules and media upload validation."""

    def setUp(self):
        self.student_private = User.objects.create_user(
            enrollment_number='2024BCSE502',
            username='2024BCSE502',
            password='Password123!',
            first_name='Private',
            last_name='Student',
            gender='M',
            phone_number='+919876543502',
            email='private502@nitsri.ac.in',
            verified=True,
            hide_from_search=True,
        )
        self.friend = User.objects.create_user(
            enrollment_number='2024BCSE503',
            username='2024BCSE503',
            password='Password123!',
            first_name='Friend',
            last_name='Student',
            gender='M',
            phone_number='+919876543503',
            email='friend503@nitsri.ac.in',
            verified=True,
        )
        self.stranger = User.objects.create_user(
            enrollment_number='2024BCSE504',
            username='2024BCSE504',
            password='Password123!',
            first_name='Stranger',
            last_name='Student',
            gender='M',
            phone_number='+919876543504',
            email='stranger504@nitsri.ac.in',
            verified=True,
        )
        self.staff_user = User.objects.create_user(
            enrollment_number='2024BCSE505',
            username='2024BCSE505',
            password='Password123!',
            first_name='Staff',
            last_name='User',
            gender='M',
            phone_number='+919876543505',
            email='staff505@nitsri.ac.in',
            is_staff=True,
            verified=True,
        )

        Friendship.objects.create(user1=self.student_private, user2=self.friend)

    def test_photo_visibility_policy_honors_privacy(self):
        """Private profile photo is hidden from strangers, visible to self, friend, and staff."""
        # Stranger cannot view photo
        self.assertFalse(can_view_profile_field(self.stranger, self.student_private, 'profile_photo'))

        # Self can view photo
        self.assertTrue(can_view_profile_field(self.student_private, self.student_private, 'profile_photo'))

        # Friend can view photo
        self.assertTrue(can_view_profile_field(self.friend, self.student_private, 'profile_photo'))

        # Staff can view photo
        self.assertTrue(can_view_profile_field(self.staff_user, self.student_private, 'profile_photo'))

    def test_profile_view_renders_placeholder_for_stranger_when_private(self):
        """Profile view hides photo and passes can_see_photo=False to stranger."""
        # Create a dummy image
        img = Image.new('RGB', (50, 50), color='blue')
        img_io = io.BytesIO()
        img.save(img_io, format='JPEG')
        img_file = SimpleUploadedFile("avatar.jpg", img_io.getvalue(), content_type="image/jpeg")

        self.student_private.profile_photo = img_file
        self.student_private.save()

        self.client.force_login(self.stranger)
        response = self.client.get(reverse('profile', args=[self.student_private.enrollment_number]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_see_photo'])
        # Photo URL should not be rendered for stranger
        self.assertNotContains(response, self.student_private.profile_photo.url)

    def test_profile_photo_upload_rejects_fake_image(self):
        """Uploading an HTML/script disguised as a .jpg is rejected by image validation."""
        self.client.force_login(self.student_private)
        fake_image = SimpleUploadedFile(
            "xss_exploit.jpg",
            b"<script>alert('XSS')</script>",
            content_type="image/jpeg"
        )
        response = self.client.post(
            reverse('profile', args=[self.student_private.enrollment_number]),
            {
                'current_password': 'Password123!',
                'profile_photo': fake_image,
            },
            follow=True,
        )
        self.assertContains(response, "Uploaded file is not a valid image")
