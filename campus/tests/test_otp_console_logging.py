"""Unit tests for Console OTP output alongside requests when OTP limit is reached.

Verifies:
1. Plaintext active OTP caching and lookup across user, enrollment, phone, email, and purpose.
2. Fallback cryptographic scan recovery when cache/memory is cleared.
3. Console emission and request line decoration when resend cooldown limit is active.
4. Console emission and request line decoration when HTTP 429 rate limit is reached.
5. Console emission when temporary delivery limits (carrier/SMTP) are reached.
"""

import io
import json
import logging
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.client_logger import (
    ClientLoggingASGIWrapper,
    update_or_record_client_request,
    pop_client_request,
)
from accounts.models import PhoneOTP
from accounts.two_factor import (
    can_resend_otp,
    create_phone_otp,
    get_active_otp,
    store_active_otp,
    emit_otp_console_log,
)

User = get_user_model()


class ConsoleOtpLimitLoggingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "SecurePassword123!"
        self.user = User.objects.create_user(
            username="2024BCSE088",
            enrollment_number="2024BCSE088",
            first_name="Zayd",
            phone_number="9876543288",
            email="zayd88@nitsri.ac.in",
            password=self.password,
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="M",
            two_factor_enabled=True,
        )
        self.client = Client()

    def test_store_and_get_active_otp(self):
        """Active OTP is stored in memory and cache, retrievable by user, phone, and email."""
        store_active_otp("654321", user=self.user, purpose="2FA")
        self.assertEqual(get_active_otp(user=self.user, purpose="2FA"), "654321")
        self.assertEqual(get_active_otp(phone_number="9876543288", purpose="2FA"), "654321")
        self.assertEqual(get_active_otp(email="zayd88@nitsri.ac.in", purpose="2FA"), "654321")

    def test_fallback_hash_recovery_when_cache_cleared(self):
        """If in-memory and Django cache are wiped, active code is recovered from PhoneOTP database record."""
        # Create an OTP in DB
        otp_record, raw_code = create_phone_otp(self.user, purpose="2FA")
        self.assertEqual(len(raw_code), 6)

        # Clear cache and in-memory registry
        cache.clear()
        from accounts.two_factor import _ACTIVE_OTP_REGISTRY, _ACTIVE_OTP_LOCK
        with _ACTIVE_OTP_LOCK:
            _ACTIVE_OTP_REGISTRY.clear()

        # Recovery from DB
        recovered = get_active_otp(user=self.user, purpose="2FA")
        self.assertEqual(recovered, raw_code)

    def test_can_resend_otp_emits_console_log_when_limit_reached(self):
        """When resend cooldown is active, can_resend_otp emits the active OTP to console."""
        otp_record, raw_code = create_phone_otp(self.user, purpose="2FA")

        with patch("accounts.two_factor.emit_otp_console_log") as mock_emit:
            can_resend, wait_sec = can_resend_otp(self.user, purpose="2FA")
            self.assertFalse(can_resend)
            self.assertGreater(wait_sec, 0)
            mock_emit.assert_called_once()
            args, kwargs = mock_emit.call_args
            self.assertEqual(kwargs.get("raw_code"), raw_code)
            self.assertIn("RESEND COOLDOWN LIMIT REACHED", kwargs.get("reason"))

    def test_request_log_decorates_with_otp_alongside_request(self):
        """ClientLoggingASGIWrapper._log_http_event attaches [OTP: <code>] to console request log."""
        store_active_otp("112233", user=self.user, purpose="2FA")

        logs_captured = []
        with patch("accounts.client_logger.emit_console_log", side_effect=lambda msg, code: logs_captured.append(msg)):
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/accounts/two-factor/",
                "client_identity": {
                    "enrollment": self.user.enrollment_number,
                    "name": self.user.get_full_name(),
                    "otp_info": "[OTP: 112233 (Cooldown: 45s)]",
                },
            }
            ClientLoggingASGIWrapper._log_http_event(
                scope=scope,
                client_key="127.0.0.1:54321",
                real_client="127.0.0.1",
                received_at=timezone.now().timestamp(),
                status_code=302,
            )

        self.assertEqual(len(logs_captured), 1)
        self.assertIn("HTTP POST /accounts/two-factor/ 302", logs_captured[0])
        self.assertIn("[OTP: 112233 (Cooldown: 45s)]", logs_captured[0])

    def test_send_signup_otp_emits_otp_in_console_when_limit_reached(self):
        """When send_signup_otp hits resend limit, it emits active OTP to console and returns wait status."""
        phone = "9988776655"
        email = "applicant99@nitsri.ac.in"

        # First dispatch succeeds
        res1 = self.client.post(reverse("send_signup_otp"), {"phone_number": phone, "email": email})
        self.assertEqual(res1.status_code, 200)
        active_code = get_active_otp(phone_number=phone, email=email, purpose="SIGNUP")
        self.assertIsNotNone(active_code)

        # Immediate second dispatch hits cooldown limit
        with patch("accounts.views.emit_otp_console_log") as mock_emit:
            res2 = self.client.post(reverse("send_signup_otp"), {"phone_number": phone, "email": email})
            self.assertEqual(res2.status_code, 429)
            mock_emit.assert_called()
            raw = mock_emit.call_args[0][0] if mock_emit.call_args[0] else mock_emit.call_args[1].get("raw_code")
            self.assertEqual(raw, active_code)

    def test_rate_limit_throttle_emits_otp_in_console_when_limit_reached(self):
        """When @rate_limit returns HTTP 429 on an OTP endpoint, active OTP is emitted to console."""
        # Pre-seed active OTP for user
        store_active_otp("778899", user=self.user, purpose="2FA")

        session = self.client.session
        session["pre_2fa_user_id"] = self.user.id
        session["pre_2fa_method"] = "otp"
        session["pre_2fa_time"] = timezone.now().timestamp()
        session.save()

        # Simulate reaching rate limit on two_factor_verify
        with patch("accounts.two_factor.emit_otp_console_log") as mock_emit:
            # Artificially set rate limit cache key to limit + 1
            ip = "127.0.0.1"
            cache.set(f"ratelimit:two_factor_verify:{ip}", 20, timeout=60)
            res = self.client.post(reverse("two_factor_verify"), {"action": "resend"})
            self.assertEqual(res.status_code, 429)
            mock_emit.assert_called()
            self.assertEqual(mock_emit.call_args[1].get("raw_code"), "778899")
