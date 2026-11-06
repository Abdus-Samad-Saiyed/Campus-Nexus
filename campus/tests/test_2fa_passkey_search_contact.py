"""
Automated tests for:
1. 2-Factor Authentication (Phone SMS OTP) engine with rate limiting and tamper resistance
2. WebAuthn / Passkey challenge and assertion verification
3. Strict credential sharing disciplinary notice on login page
4. Designated application approvers listed on sign-up page
5. Student Directory Search with hide_from_search privacy safeguard
6. Contact Us communications directory
7. GitHub Issues routing on feedback page
"""

import base64
import json
from datetime import timedelta
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasskeyCredential, PhoneOTP, User
from accounts.two_factor import (
    can_resend_otp,
    create_phone_otp,
    generate_webauthn_challenge,
    hash_otp,
    verify_passkey_assertion,
    verify_phone_otp,
)


class TwoFactorAndPasskeyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            enrollment_number="2024BCSE101",
            username="2024BCSE101",
            password="StrongPassword123!",
            first_name="Test",
            last_name="Student",
            phone_number="+919876543210",
            gender="M",
            verified=True,
            two_factor_enabled=True,
        )

    def test_phone_otp_creation_and_hashing(self):
        otp, raw_code = create_phone_otp(self.user)
        self.assertEqual(len(raw_code), 6)
        self.assertTrue(raw_code.isdigit())
        self.assertEqual(otp.attempts_left, 3)
        self.assertFalse(otp.is_used)
        self.assertEqual(otp.otp_hash, hash_otp(raw_code))

    def test_phone_otp_successful_verification(self):
        otp, raw_code = create_phone_otp(self.user)
        success, msg = verify_phone_otp(self.user, raw_code)
        self.assertTrue(success)
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    def test_phone_otp_rate_limiting_max_three_attempts(self):
        otp, raw_code = create_phone_otp(self.user)

        # Attempt 1 (wrong)
        success, msg = verify_phone_otp(self.user, "000000")
        self.assertFalse(success)
        otp.refresh_from_db()
        self.assertEqual(otp.attempts_left, 2)

        # Attempt 2 (wrong)
        success, msg = verify_phone_otp(self.user, "111111")
        self.assertFalse(success)
        otp.refresh_from_db()
        self.assertEqual(otp.attempts_left, 1)

        # Attempt 3 (wrong) -> should lock out
        success, msg = verify_phone_otp(self.user, "222222")
        self.assertFalse(success)
        otp.refresh_from_db()
        self.assertEqual(otp.attempts_left, 0)
        self.assertTrue(otp.is_used)

        # Attempt 4 with correct code after lockout -> should still fail
        success, msg = verify_phone_otp(self.user, raw_code)
        self.assertFalse(success)

    def test_phone_otp_expiration(self):
        otp, raw_code = create_phone_otp(self.user)
        # Manually expire
        otp.expires_at = timezone.now() - timedelta(seconds=10)
        otp.save()

        success, msg = verify_phone_otp(self.user, raw_code)
        self.assertFalse(success)
        self.assertIn("expired", msg.lower())

    def test_resend_cooldown_enforcement(self):
        create_phone_otp(self.user)
        can_resend, wait_sec = can_resend_otp(self.user)
        self.assertFalse(can_resend)
        self.assertGreater(wait_sec, 0)

    def test_passkey_assertion_verification(self):
        from webauthn.helpers import bytes_to_base64url
        from campus.tests.webauthn_test_utils import generate_test_keypair, make_assertion_response
        priv_key, cose_key_bytes, pubkey_b64 = generate_test_keypair()
        cred_bytes = b"test_cred_id_123"
        cred_b64 = bytes_to_base64url(cred_bytes)
        passkey = PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key=pubkey_b64,
            name="My Windows Hello Key",
        )

        challenge = generate_webauthn_challenge()
        assertion = make_assertion_response(
            private_key=priv_key,
            challenge_b64=challenge,
            credential_id=cred_bytes,
            origin='http://testserver',
            rp_id='testserver',
        )

        # Valid cryptographic assertion
        success, msg = verify_passkey_assertion(
            user=self.user,
            credential_id=cred_b64,
            full_credential=assertion,
            session_challenge=challenge,
            expected_rp_id='testserver',
            expected_origin='http://testserver',
        )
        self.assertTrue(success)
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 1)
        self.assertIsNotNone(passkey.last_used_at)

        # Tampered signature fails
        bad_assertion = make_assertion_response(
            private_key=priv_key,
            challenge_b64=challenge,
            credential_id=cred_bytes,
            origin='http://testserver',
            rp_id='testserver',
            tamper_signature=True,
        )
        fail_success, fail_msg = verify_passkey_assertion(
            user=self.user,
            credential_id=cred_b64,
            full_credential=bad_assertion,
            session_challenge=challenge,
            expected_rp_id='testserver',
            expected_origin='http://testserver',
        )
        self.assertFalse(fail_success)


class AuthenticationAndSecurityPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            enrollment_number="2024BCSE999",
            username="2024BCSE999",
            password="AdminPassword123!",
            first_name="Admin",
            last_name="Superuser",
            phone_number="+919876543299",
            gender="M",
            verified=True,
        )

    def test_login_page_renders_disciplinary_warning(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keep your login credentials secure")
        self.assertContains(response, "Never share your password or 2FA codes")

    def test_apply_page_lists_designated_application_approvers(self):
        response = self.client.get(reverse("apply_account"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Designated Application Approvers")
        self.assertContains(response, "2024BCSE999")
        self.assertContains(response, "Admin Superuser")

    def test_contact_page_renders_successfully(self):
        response = self.client.get(reverse("contact"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "asaiyed_2024bcse065@nitsri.ac.in")
        self.assertContains(response, "No Physical Location Yet")
        self.assertContains(response, "We do not have a physical location or campus office yet.")

    def test_feedback_page_routes_to_github_issues(self):
        response = self.client.get(reverse("feedback"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Route Directly to Repository Issues")
        self.assertContains(response, "https://github.com/Abdus-Samad-Saiyed/Campus-Nexus/issues/new")
        self.assertContains(response, "routeToGitHubIssue()")


class StudentSearchDirectoryTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.searcher = User.objects.create_user(
            enrollment_number="2024BCSE010",
            username="2024BCSE010",
            password="Password123!",
            first_name="Alice",
            last_name="Student",
            phone_number="+919876543201",
            gender="F",
            verified=True,
        )
        self.visible_student = User.objects.create_user(
            enrollment_number="2024BCSE011",
            username="2024BCSE011",
            password="Password123!",
            first_name="Bob",
            last_name="Visible",
            phone_number="+919876543202",
            gender="M",
            verified=True,
            hide_from_search=False,
        )
        self.hidden_student = User.objects.create_user(
            enrollment_number="2024BECE012",
            username="2024BECE012",
            password="Password123!",
            first_name="Charlie",
            last_name="Incognito",
            phone_number="+919876543203",
            gender="M",
            verified=True,
            hide_from_search=True,
        )

    def test_student_search_respects_hide_from_search_privacy(self):
        self.client.force_login(self.searcher)

        # Search for Bob (visible)
        res_bob = self.client.get(reverse("user_search"), {"q": "2024BCSE011"})
        self.assertEqual(res_bob.status_code, 200)
        self.assertContains(res_bob, "2024BCSE011")
        self.assertContains(res_bob, "Bob Visible")

        # Search for Charlie (hidden)
        res_charlie = self.client.get(reverse("user_search"), {"q": "2024BECE012"})
        self.assertEqual(res_charlie.status_code, 200)
        self.assertEqual(len(res_charlie.context["student_list"]), 0)
        self.assertNotContains(res_charlie, "Charlie Incognito")
        self.assertContains(res_charlie, "No Students Found")

        # Self should not appear in search results
        res_self = self.client.get(reverse("user_search"), {"q": "2024BCSE010"})
        self.assertEqual(len(res_self.context["student_list"]), 0)
        self.assertContains(res_self, "No Students Found")
