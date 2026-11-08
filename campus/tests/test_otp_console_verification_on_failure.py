from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PhoneOTP
from accounts.two_factor import (
    create_phone_otp,
    get_active_otp,
    verify_phone_otp,
    clear_active_otp,
)

User = get_user_model()


class OtpConsoleVerificationOnFailureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "SecurePassword123!"
        self.user = User.objects.create_user(
            username="2024BCSE065",
            enrollment_number="2024BCSE065",
            first_name="Farhan",
            phone_number="7355463857",
            email="farhan65@nitsri.ac.in",
            password=self.password,
            is_active=True,
            verified=True,
            two_factor_enabled=True,
        )
        self.client = Client()

    def test_otp_persists_and_is_accepted_when_carrier_and_smtp_fail(self):
        """When SMS and SMTP delivery fail (dispatch limit/failure), OTP must NOT be deleted and must be accepted."""
        with patch("accounts.two_factor.dispatch_sms", return_value=False), \
             patch("accounts.two_factor.dispatch_email_otp", return_value=False), \
             patch("accounts.two_factor.emit_otp_console_log") as mock_emit:

            otp, raw_code = create_phone_otp(self.user, purpose="2FA")

            # 1. Verification that console log was emitted
            mock_emit.assert_called_once()
            self.assertEqual(mock_emit.call_args[0][0], raw_code)
            self.assertIn("DISPATCH LIMIT/FAILURE", mock_emit.call_args[1].get("reason"))

            # 2. Crucial fix: The OTP must NOT be deleted from the database!
            self.assertFalse(otp.any_delivered)
            db_record = PhoneOTP.objects.filter(user=self.user, purpose="2FA", is_used=False).first()
            self.assertIsNotNone(db_record, "PhoneOTP record was incorrectly deleted on delivery failure!")

            # 3. verify_phone_otp must succeed with the raw console OTP
            success, msg = verify_phone_otp(self.user, submitted_code=raw_code, purpose="2FA")
            self.assertTrue(success, f"Verification failed with: {msg}")
            self.assertEqual(msg, "Verification successful.")

            # Record must now be marked as used
            db_record.refresh_from_db()
            self.assertTrue(db_record.is_used)

    def test_two_factor_verify_flow_with_carrier_failure(self):
        """In 2FA login view, when dispatch fails, student entering console OTP successfully logs in."""
        # Setup pre-2FA session for user
        session = self.client.session
        session["pre_2fa_user_id"] = self.user.id
        session["pre_2fa_time"] = timezone.now().timestamp()
        session.save()

        # Step 1: Request OTP with carrier/SMTP mocked as failing
        with patch("accounts.two_factor.dispatch_sms", return_value=False), \
             patch("accounts.two_factor.dispatch_email_otp", return_value=False), \
             patch("accounts.two_factor.emit_otp_console_log") as mock_emit:

            res1 = self.client.post(reverse("two_factor_verify"), {"action": "send_otp"})
            self.assertEqual(res1.status_code, 302)
            self.assertGreaterEqual(mock_emit.call_count, 1)
            raw_code = mock_emit.call_args_list[0][0][0]

        # Step 2: Student enters the raw code from the console into the 2FA verify form
        res2 = self.client.post(reverse("two_factor_verify"), {
            "action": "verify",
            "otp_code": raw_code,
        })
        self.assertEqual(res2.status_code, 302)
        self.assertEqual(res2.url, "/dashboard/")

        # Verify student is now authenticated
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_dual_layer_cached_otp_verification_fallback(self):
        """Even if PhoneOTP table row is manually removed, cached active OTP validates successfully."""
        from accounts.two_factor import store_active_otp
        store_active_otp("839497", user=self.user, purpose="2FA")

        # Confirm no PhoneOTP row exists in DB
        PhoneOTP.objects.filter(user=self.user).delete()

        # verify_phone_otp must still accept the active cached code via fallback
        success, msg = verify_phone_otp(self.user, submitted_code="839497", purpose="2FA")
        self.assertTrue(success)
        self.assertEqual(msg, "Verification successful.")

        # Re-submitting should now fail (invalidated)
        success2, msg2 = verify_phone_otp(self.user, submitted_code="839497", purpose="2FA")
        self.assertFalse(success2)
