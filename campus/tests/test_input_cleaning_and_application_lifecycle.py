"""Tests for platform-wide input sanitization (names & 10-digit phones)
and the complete application lifecycle (immediate temporary account on submission,
application emails, and admin rejection reason capture).
"""

from unittest.mock import patch
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from accounts.models import AccountApplication, AuditEvent, User
from accounts.two_factor import clean_person_name, create_phone_otp, normalize_phone_number, validate_phone_number


class InputSanitizationUnitTests(TestCase):
    """Test person name casing and 10-digit Indian mobile number cleaning across all scenarios."""

    def test_clean_person_name_capitalization(self):
        cases = [
            ("muhammed Farahi", "Muhammed Farahi"),
            ("brij sharma", "Brij Sharma"),
            ("sanamdeep singh", "Sanamdeep Singh"),
            ("VARDHANYA VAID", "Vardhanya Vaid"),
            ("anne-marie smith", "Anne-Marie Smith"),
            ("AdminUser", "AdminUser"),
            ("TestStudent", "TestStudent"),
            ("", ""),
            ("   ", ""),
        ]
        for raw, expected in cases:
            self.assertEqual(clean_person_name(raw), expected)

    def test_normalize_phone_number_ten_digits(self):
        cases = [
            ("+919045144767", "9045144767"),
            ("+91 90451 44767", "9045144767"),
            ("919045144767", "9045144767"),
            ("09045144767", "9045144767"),
            ("+91 (90451) 44767", "9045144767"),
            ("+91-90451-44767", "9045144767"),
            ("6006928461", "6006928461"),
            ("+916006928461", "6006928461"),
            ("", ""),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_phone_number(raw), expected)

    def test_validate_phone_number(self):
        self.assertEqual(validate_phone_number("+919045144767"), "9045144767")
        self.assertEqual(validate_phone_number("6006928461"), "6006928461")
        with self.assertRaises(ValidationError):
            validate_phone_number("1234567890")  # does not start with 6-9
        with self.assertRaises(ValidationError):
            validate_phone_number("98765")       # short length

    def test_user_and_application_model_lifecycle_cleaning(self):
        # User save automatically capitalizes names and cleans phone number
        user = User.objects.create_user(
            username="2024BCSE890",
            enrollment_number="2024BCSE890",
            first_name="muhammed",
            last_name="farahi",
            phone_number="+91 90451 44767",
            gender="M",
            password="SecurePassword123!",
        )
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Muhammed")
        self.assertEqual(user.last_name, "Farahi")
        self.assertEqual(user.phone_number, "9045144767")

        # AccountApplication save automatically capitalizes full_name and cleans phone_number
        app = AccountApplication.objects.create(
            enrollment_number="2024BCSE891",
            full_name="brij sharma",
            email="brij891@nitsri.ac.in",
            phone_number="+917011450685",
            gender="M",
            password="HashedPassword123!",
            application_number="APP89100",
            phone_verified=True,
        )
        app.refresh_from_db()
        self.assertEqual(app.full_name, "Brij Sharma")
        self.assertEqual(app.phone_number, "7011450685")


class ApplicationLifecycleAndEmailsTests(TestCase):
    """Test immediate temporary account creation, application emails, and admin rejection flow."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="2024BCSE000",
            enrollment_number="2024BCSE000",
            first_name="Admin",
            last_name="Coordinator",
            email="admin@nitsri.ac.in",
            phone_number="9999999999",
            password="AdminPassword123!",
            verified=True,
        )

    def test_immediate_temporary_account_on_application_submission(self):
        """Submitting a permanent verification application immediately activates a temporary user account."""
        enrollment = "2024BCSE850"
        email = "applicant850@nitsri.ac.in"
        phone = "+919876543850"

        # Pre-generate OTP
        _, otp_code = create_phone_otp(phone_number="9876543850", email=email, purpose="SIGNUP")

        mail.outbox.clear()
        res = self.client.post(
            reverse("apply_account"),
            data={
                "registration_mode": "permanent",
                "enrollment_number": enrollment,
                "full_name": "tariq ahmad dar",
                "gender": "M",
                "email": email,
                "phone_number": phone,
                "phone_otp": otp_code,
                "password": "StudentSecurePass123!",
                "confirm_password": "StudentSecurePass123!",
            },
        )
        self.assertEqual(res.status_code, 200)

        # 1. AccountApplication record created with cleaned name and 10-digit phone
        app = AccountApplication.objects.filter(enrollment_number=enrollment).first()
        self.assertIsNotNone(app)
        self.assertEqual(app.full_name, "Tariq Ahmad Dar")
        self.assertEqual(app.phone_number, "9876543850")
        self.assertFalse(app.approved)

        # 2. Temporary active User created immediately
        user = User.objects.filter(enrollment_number=enrollment).first()
        self.assertIsNotNone(user)
        self.assertEqual(user.first_name, "Tariq")
        self.assertEqual(user.last_name, "Ahmad Dar")
        self.assertEqual(user.phone_number, "9876543850")
        self.assertFalse(user.verified)  # Untrusted / pending physical verification
        self.assertTrue(user.is_active)  # Active for campus rides immediately

        # 3. User is automatically logged in
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

        # 4. Received confirmation email was dispatched
        self.assertTrue(any("Student Verification Application Received" in m.subject for m in mail.outbox))
        recvd_mail = next(m for m in mail.outbox if "Student Verification Application Received" in m.subject)
        self.assertIn(email, recvd_mail.to)
        self.assertIn("Your account has been activated immediately", recvd_mail.body)

    def test_admin_rejection_captures_reason_and_dispatches_email(self):
        """Admin rejecting application selects reason, which is recorded in audit log and emailed to student."""
        app = AccountApplication.objects.create(
            enrollment_number="2024BCSE851",
            full_name="Farhan Qadir",
            email="farhan851@nitsri.ac.in",
            phone_number="9876543851",
            gender="M",
            password="HashedPassword123!",
            application_number="APP85100",
            phone_verified=True,
        )

        self.client.force_login(self.admin)
        # Establish recent auth session
        session = self.client.session
        from django.utils import timezone
        from datetime import timedelta
        session["auth_timestamp"] = timezone.now().timestamp()
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=15)).isoformat()
        session.save()

        mail.outbox.clear()
        reject_res = self.client.post(
            reverse("reject_application", args=[app.id]),
            data={
                "rejection_reason_choice": "DETAILS_MISMATCH",
                "rejection_reason_custom": "Name does not match enrollment record in CSE department roster.",
            },
        )
        self.assertEqual(reject_res.status_code, 302)

        # Application deleted
        self.assertFalse(AccountApplication.objects.filter(id=app.id).exists())

        # AuditEvent logs the specific rejection reason
        audit = AuditEvent.objects.filter(event_type="APP_REJECTED", target_id="2024BCSE851").first()
        self.assertIsNotNone(audit)
        self.assertIn("Student profile details", audit.description)
        self.assertIn("Name does not match enrollment record", audit.description)

        # Student received rejection notification email
        self.assertTrue(any("Verification Application Status Update" in m.subject for m in mail.outbox))
        rejection_mail = next(m for m in mail.outbox if "Verification Application Status Update" in m.subject)
        self.assertIn("farhan851@nitsri.ac.in", rejection_mail.to)
        self.assertIn("Name does not match enrollment record in CSE department roster", rejection_mail.body)
        self.assertIn("asaiyed_2024BCSE065@nitsri.ac.in", rejection_mail.body)
