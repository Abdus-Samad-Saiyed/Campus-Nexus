"""
Tests for deleted account identifier recycling and tombstone lifecycle.
Ensures that deleting an account frees up its enrollment number and email,
allowing new users to register without collision, while preserving foreign key
relations and assigning brand new primary keys.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, DirectMessage, AccountApplication
from accounts.forms import EarlyLaunchDeclarationForm, AccountApplicationForm
from accounts.utils import tombstone_deleted_user, release_deleted_user_identifiers


class DeletedAccountRecyclingTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.test_email = "student.recycled@example.com"
        self.test_enrollment = "2024BCSE888"

    def test_tombstoning_frees_email_and_enrollment_identifiers(self):
        """Tombstoning an account updates its unique fields with a DEL_ prefix and releases original values."""
        user = User.objects.create_user(
            username=self.test_enrollment,
            enrollment_number=self.test_enrollment,
            email=self.test_email,
            password="InitialPassword123!",
            is_active=True,
        )
        old_id = user.id

        # Attach historical data (e.g., direct message)
        peer = User.objects.create_user(
            username="2024BCSE002",
            enrollment_number="2024BCSE002",
            email="peer@nitsri.ac.in",
            password="PeerPassword123!",
        )
        dm = DirectMessage.objects.create(
            sender=user,
            recipient=peer,
            content="Historical conversation that must be preserved."
        )

        # Soft delete & tombstone
        tombstone_deleted_user(user)
        user.refresh_from_db()

        self.assertTrue(user.is_deleted)
        self.assertFalse(user.is_active)
        self.assertTrue(user.enrollment_number.startswith("DEL_"))
        self.assertTrue(user.username.startswith("del_"))
        self.assertTrue(user.email.startswith("del_"))

        # Verify historical foreign key is still perfectly attached to old_id
        dm.refresh_from_db()
        self.assertEqual(dm.sender_id, old_id)

        # Verify original identifiers are now completely absent from active queries
        self.assertFalse(User.objects.filter(email=self.test_email, is_deleted=False).exists())
        self.assertFalse(User.objects.filter(enrollment_number=self.test_enrollment, is_deleted=False).exists())

    def test_new_user_can_register_on_deleted_account_email_and_enrollment(self):
        """A new user can self-declare in early launch using the same email and enrollment as a deleted user."""
        old_user = User.objects.create_user(
            username=self.test_enrollment,
            enrollment_number=self.test_enrollment,
            email=self.test_email,
            password="OldPassword123!",
            is_deleted=True,
            is_active=False,
        )
        old_id = old_user.id

        # Form submission with exact same email and enrollment
        form_data = {
            'enrollment_number': self.test_enrollment,
            'full_name': 'New Student Name',
            'gender': 'M',
            'phone_number': '+919876543210',
            'email': self.test_email,
            'password': 'BrandNewSecurePassword123!',
            'confirm_password': 'BrandNewSecurePassword123!',
        }

        form = EarlyLaunchDeclarationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

        new_user = form.save()
        self.assertNotEqual(new_user.id, old_id)
        self.assertEqual(new_user.enrollment_number, self.test_enrollment)
        self.assertEqual(new_user.email, self.test_email)
        self.assertTrue(new_user.is_active)
        self.assertFalse(new_user.is_deleted)

        # Old user still exists in the DB with tombstoned values
        old_user.refresh_from_db()
        self.assertEqual(old_user.id, old_id)
        self.assertTrue(old_user.is_deleted)
        self.assertTrue(old_user.enrollment_number.startswith("DEL_"))

    def test_account_application_form_accepts_deleted_account_email(self):
        """AccountApplicationForm clean methods allow emails and enrollments belonging to deleted accounts."""
        User.objects.create_user(
            username="2024BCSE887",
            enrollment_number="2024BCSE887",
            email="recycled.applicant@example.com",
            password="OldPassword123!",
            is_deleted=True,
            is_active=False,
        )

        form = AccountApplicationForm(data={
            'enrollment_number': '2024BCSE887',
            'full_name': 'Prospective Applicant',
            'phone_number': '+919988776655',
            'email': 'recycled.applicant@example.com',
            'gender': 'F',
            'password': 'ApplicantPassword123!',
            'confirm_password': 'ApplicantPassword123!',
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['enrollment_number'], '2024BCSE887')
        self.assertEqual(form.cleaned_data['email'], 'recycled.applicant@example.com')
