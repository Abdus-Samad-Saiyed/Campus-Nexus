"""
Automated regression tests for:
1. Namespace segregation: 20XX synthetic debug accounts bypass 2FA & phone verification
2. Single concurrent session enforcement (superseded session termination)
3. 10-minute session grace window for security setting modifications
4. Profile password change directly from profile interface
5. Phone OTP verification on registration application
6. Forgotten password flow: Phone SMS OTP vs In-Person Physical Verification Desk
7. Admin Identity Desk: phone update, physical verification resolution, soft deletion preserving trip history
"""

import json
import os
from datetime import timedelta
from django.contrib.auth import authenticate
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    AccountApplication,
    AuditEvent,
    PhoneOTP,
    PhysicalVerificationRequest,
    User,
)
from accounts.two_factor import create_phone_otp, verify_phone_otp
from coordinate.models import RideRequest, RideParticipant


class IdentitySecurityArchitectureTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Superaccount (User's personal enrollment)
        self.superaccount = User.objects.create_superuser(
            enrollment_number="2024BCSE065",
            username="2024BCSE065",
            password="SuperTestPass@123",
            first_name=os.environ.get("PERSONAL_SUPERACCOUNT_FIRST_NAME", "Abdus"),
            last_name=os.environ.get("PERSONAL_SUPERACCOUNT_LAST_NAME", "Samad Saiyed"),
            phone_number=os.environ.get("PERSONAL_SUPERACCOUNT_PHONE", "+919999999999"),
            gender="M",
            verified=True,
            two_factor_enabled=True,
        )

        # 20XX Synthetic Debug account
        self.debug_user = User.objects.create_user(
            enrollment_number="20XXBCSE001",
            username="20XXBCSE001",
            password="Debug@123",
            first_name="Debug",
            last_name="CSE",
            phone_number="+919876543210",
            gender="M",
            verified=True,
            two_factor_enabled=False,
        )

        # Standard real student
        self.student = User.objects.create_user(
            enrollment_number="2024BCSE099",
            username="2024BCSE099",
            password="StudentPass@123",
            first_name="Real",
            last_name="Student",
            phone_number="+919999988888",
            gender="M",
            verified=True,
            two_factor_enabled=True,
        )

    def test_debug_account_bypasses_2fa_on_login(self):
        """Synthetic 20XX accounts must bypass 2FA intercept directly into authenticated state."""
        self.assertTrue(self.debug_user.is_debug_account)
        res = self.client.post(reverse("login"), {
            "username": "20XXBCSE001",
            "password": "Debug@123",
        }, follow=True)

        self.assertRedirects(res, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.debug_user.id)

    def test_real_student_with_2fa_intercepted(self):
        """Real student accounts with 2FA enabled must redirect to 2FA choice without eager OTP dispatch."""
        self.assertFalse(self.student.is_debug_account)
        res = self.client.post(reverse("login"), {
            "username": "2024BCSE099",
            "password": "StudentPass@123",
        })

        self.assertRedirects(res, reverse("two_factor_verify"))
        self.assertEqual(self.client.session.get("pre_2fa_user_id"), self.student.id)

        # Unless OTP is explicitly chosen, no OTP is generated
        otp_before_choice = PhoneOTP.objects.filter(user=self.student, is_used=False, purpose="2FA").first()
        self.assertIsNone(otp_before_choice)

        # Intermediate choice screen renders with option to use passkey or send OTP
        choice_page = self.client.get(reverse("two_factor_verify"))
        self.assertEqual(choice_page.status_code, 200)
        self.assertIn("Passkey / Windows Hello", choice_page.content.decode())
        self.assertIn("Send Verification Code", choice_page.content.decode())

        # When user chooses OTP, the OTP is generated and dispatched
        send_res = self.client.post(reverse("two_factor_verify"), {"action": "send_otp"})
        self.assertRedirects(send_res, reverse("two_factor_verify"))

        otp = PhoneOTP.objects.filter(user=self.student, is_used=False, purpose="2FA").first()
        self.assertIsNotNone(otp)

    def test_single_active_session_enforcement(self):
        """Logging in from a new device/browser terminates any prior active session."""
        browser_1 = Client()
        browser_2 = Client()

        # Login on Device 1
        browser_1.post(reverse("login"), {
            "username": "20XXBCSE001",
            "password": "Debug@123",
        })
        key_1 = browser_1.session.session_key
        self.debug_user.refresh_from_db()
        self.assertEqual(self.debug_user.current_session_key, key_1)

        # Login on Device 2
        browser_2.post(reverse("login"), {
            "username": "20XXBCSE001",
            "password": "Debug@123",
        })
        key_2 = browser_2.session.session_key
        self.debug_user.refresh_from_db()
        self.assertEqual(self.debug_user.current_session_key, key_2)
        self.assertNotEqual(key_1, key_2)

        # Device 1 subsequent request must be terminated by SingleSessionMiddleware
        res_device_1 = browser_1.get(reverse("dashboard"))
        self.assertRedirects(res_device_1, reverse("login") + "?next=" + reverse("dashboard"))
        self.assertFalse(browser_1.session.get("_auth_user_id"))

    def test_ten_minute_security_grace_window(self):
        """Security settings modifications within 10 minutes proceed without OTP challenge."""
        self.client.force_login(self.student)
        # Establish active 10-minute grace window in session
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        # Update password and phone within grace window
        res = self.client.post(reverse("profile", args=[self.student.enrollment_number]), {
            "current_password": "StudentPass@123",
            "phone_number": "+918888877777",
            "new_password": "NewSecretPass@123",
            "confirm_password": "NewSecretPass@123",
        }, follow=True)

        self.assertContains(res, "Profile and security settings updated successfully.")
        self.student.refresh_from_db()
        self.assertEqual(self.student.phone_number, "8888877777")
        self.assertTrue(self.student.check_password("NewSecretPass@123"))

    def test_verify_security_otp_inline_activates_grace(self):
        """Inline security OTP verification activates 10m grace window without page reload."""
        from accounts.two_factor import create_phone_otp
        self.client.force_login(self.student)
        _, raw_code = create_phone_otp(self.student, purpose="SECURITY_CHANGE")

        # Empty OTP code returns 400
        res_empty = self.client.post(reverse("verify_security_otp"), data=json.dumps({"otp_code": ""}), content_type="application/json")
        self.assertEqual(res_empty.status_code, 400)

        # Invalid OTP returns 400
        res_bad = self.client.post(reverse("verify_security_otp"), data=json.dumps({"otp_code": "000000"}), content_type="application/json")
        self.assertEqual(res_bad.status_code, 400)

        # Valid OTP returns 200 and activates grace
        res_ok = self.client.post(reverse("verify_security_otp"), data=json.dumps({"otp_code": raw_code}), content_type="application/json")
        self.assertEqual(res_ok.status_code, 200)
        self.assertTrue(res_ok.json().get("success"))
        self.assertIn("security_otp_verified_until", self.client.session)

    def test_phone_change_with_otp_does_not_require_password(self):
        """Updating phone or 2FA with valid OTP succeeds without demanding current password."""
        from accounts.two_factor import create_phone_otp
        self.client.force_login(self.student)
        _, raw_code = create_phone_otp(self.student, purpose="SECURITY_CHANGE")

        res = self.client.post(reverse("profile", args=[self.student.enrollment_number]), {
            "phone_number": "+917777766666",
            "security_otp_code": raw_code,
        }, follow=True)

        self.assertContains(res, "Profile and security settings updated successfully.")
        self.student.refresh_from_db()
        self.assertEqual(self.student.phone_number, "7777766666")

    def test_password_change_still_requires_current_password(self):
        """Changing password still strictly enforces verification of current password."""
        self.client.force_login(self.student)
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        res = self.client.post(reverse("profile", args=[self.student.enrollment_number]), {
            "new_password": "NewSecretPass@123",
            "confirm_password": "NewSecretPass@123",
            "current_password": "",
        }, follow=True)

        self.assertContains(res, "Current password is required")
        self.student.refresh_from_db()
        self.assertFalse(self.student.check_password("NewSecretPass@123"))

    def test_security_change_requires_otp_when_grace_expired(self):
        """Security settings change with expired grace window must be blocked without valid OTP."""
        self.client.force_login(self.student)
        # Expired grace window
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() - timedelta(minutes=5)).isoformat()
        session.save()

        # Attempt to change phone number without OTP
        res = self.client.post(reverse("profile", args=[self.student.enrollment_number]), {
            "phone_number": "+911111122222",
        }, follow=True)

        self.assertContains(res, "Security verification required")
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.phone_number, "+911111122222")

    def test_forgot_password_via_phone_otp(self):
        """Student can reset password self-service via Phone SMS OTP."""
        # Step 1: Request OTP
        res1 = self.client.post(reverse("forgot_password"), {
            "action": "request_otp",
            "enrollment_number": "2024BCSE099",
        })
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(self.client.session.get("reset_user_id"), self.student.id)

        # Generate OTP
        otp, code = create_phone_otp(self.student, purpose="PASSWORD_RESET")

        # Step 2: Submit OTP & new password
        res2 = self.client.post(reverse("forgot_password"), {
            "action": "verify_reset",
            "otp_code": code,
            "new_password": "CompletelyNewPassword123!",
            "confirm_password": "CompletelyNewPassword123!",
        }, follow=True)

        self.assertRedirects(res2, reverse("login"))
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("CompletelyNewPassword123!"))

    def test_physical_verification_request_filing_and_admin_resolution(self):
        """Student with lost phone files in-person request; Admin verifies ID card and sets password."""
        # Filing
        res_file = self.client.post(reverse("forgot_password"), {
            "action": "physical_request",
            "enrollment_number": "2024BCSE099",
            "contact_phone": "+917777766666",
            "contact_email": "lostsim@example.com",
            "student_notes": "SIM card lost during transit, presenting ID card at desk",
        }, follow=True)

        self.assertRedirects(res_file, reverse("login"))
        pvr = PhysicalVerificationRequest.objects.get(user=self.student)
        self.assertEqual(pvr.status, "PENDING")

        # Admin Resolution at Operations Desk
        self.client.force_login(self.superaccount)
        res_resolve = self.client.post(reverse("admin_resolve_physical_verification", args=[pvr.id]), {
            "action": "verify_reset_password",
            "new_password": "TempDeskPassword@999",
            "notes": "Student presented official NIT ID card in person",
        }, follow=True)

        self.assertRedirects(res_resolve, reverse("operational_dashboard"))
        pvr.refresh_from_db()
        self.assertEqual(pvr.status, "VERIFIED")
        self.assertEqual(pvr.verified_by, self.superaccount)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("TempDeskPassword@999"))

    def test_soft_delete_account_preserves_trip_history(self):
        """Admin soft-deleting an account closes login but preserves trip log records and reviews."""
        # Create a ride with student participation
        ride = RideRequest.objects.create(
            creator=self.student,
            route="NIT_TO_AIRPORT",
            visibility="MIXED",
            departure_time=timezone.now() + timedelta(days=1),
            total_spots=4,
            status="OPEN",
        )
        participant = RideParticipant.objects.create(
            ride=ride,
            user=self.student,
        )

        self.client.force_login(self.superaccount)
        res_delete = self.client.post(reverse("admin_delete_account", args=[self.student.id]), follow=True)

        self.assertRedirects(res_delete, reverse("operational_dashboard"))
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_deleted)
        self.assertFalse(self.student.is_active)
        self.assertIn("(Closed Account)", self.student.display_name)

        # Ride history is intact and not cascade-deleted
        self.assertTrue(RideRequest.objects.filter(id=ride.id).exists())
        self.assertTrue(RideParticipant.objects.filter(id=participant.id).exists())
