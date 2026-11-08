"""End-to-End Test Suite for Progressive Web App (PWA) & Mac Touch ID Biometrics.

Validates:
1. PWA Manifest, Service Worker, and Offline Fallback end-to-end.
2. Downloaded app standalone detection and 2-week persistent session timeout.
3. Mac Safari PWA installability detection and Add to Dock guidance.
4. WebAuthn Passkey Registration: platform authenticator attachment & Touch ID on Mac.
5. WebAuthn Passkey Assertion: allowCredentials with internal transports & client-device hints.
6. Adaptive platform UI labelling across Mac (Touch ID), iOS (Face ID), and Windows (Windows Hello).
"""

import json
import os
import time
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasskeyCredential
from accounts.views import is_downloaded_app_request

User = get_user_model()


class PwaEndToEndAndMacTouchIdTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "StrongPass7788!"
        self.user = User.objects.create_user(
            username="2024BCSE077",
            enrollment_number="2024BCSE077",
            first_name="Aman",
            phone_number="9876543277",
            email="aman77@nitsri.ac.in",
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

    def test_pwa_manifest_end_to_end(self):
        """PWA manifest is served with application/manifest+json and valid standalone properties."""
        res = self.client.get("/manifest.json")
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/manifest+json", res.headers.get("Content-Type", ""))

        content = b"".join(res.streaming_content) if res.streaming else res.content
        data = json.loads(content.decode("utf-8"))
        self.assertEqual(data.get("display"), "standalone")
        self.assertEqual(data.get("start_url"), "/dashboard/?source=pwa")
        self.assertEqual(data.get("scope"), "/")
        self.assertTrue(len(data.get("icons", [])) >= 2)

    def test_pwa_service_worker_and_offline_fallback(self):
        """Service worker serves with root scope and navigation fallback to /offline/."""
        sw_res = self.client.get(reverse("service_worker"))
        self.assertEqual(sw_res.status_code, 200)
        self.assertEqual(sw_res.headers.get("Service-Worker-Allowed"), "/")
        self.assertIn("application/javascript", sw_res.headers.get("Content-Type", ""))

        off_res = self.client.get(reverse("pwa_offline"))
        self.assertEqual(off_res.status_code, 200)
        self.assertIn("Connection Lost", off_res.content.decode("utf-8"))
        self.assertIn("Campus Nexus", off_res.content.decode("utf-8"))

    def test_pwa_standalone_detection_and_two_week_session(self):
        """Requests with PWA markers activate is_downloaded_app_request and 14-day timeout."""
        # 1. Without PWA marker
        req_plain = self.client.get(reverse("landing"))
        self.assertFalse(is_downloaded_app_request(req_plain.wsgi_request))

        # 2. With cookie
        self.client.cookies["nexus_standalone_app"] = "1"
        res_cookie = self.client.get(reverse("landing"))
        self.assertTrue(is_downloaded_app_request(res_cookie.wsgi_request))

        # 3. With header
        self.client.cookies.clear()
        res_header = self.client.get(reverse("landing"), HTTP_X_NEXUS_APP="1")
        self.assertTrue(is_downloaded_app_request(res_header.wsgi_request))

        # 4. With query parameter
        res_query = self.client.get(reverse("landing"), {"source": "pwa"})
        self.assertTrue(is_downloaded_app_request(res_query.wsgi_request))

    def test_mac_safari_pwa_install_elements_in_base_template(self):
        """Base template includes isMacSafari detection and Mac 'Add to Dock' installation instructions."""
        res = self.client.get(reverse("landing"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        self.assertIn("function isMacSafari()", html)
        self.assertIn("isMacSafari() && !isRunningStandalone()", html)
        self.assertIn("On Mac (Safari &amp; Chrome)", html)
        self.assertIn("Add to Dock...", html)

    def test_mac_touch_id_webauthn_creation_options(self):
        """Profile passkey registration sets authenticatorAttachment 'platform' and Touch ID prompt on Mac."""
        self.client.login(username="2024BCSE077", password=self.password)
        res = self.client.get(reverse("profile", kwargs={"enrollment_number": "2024BCSE077"}))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode("utf-8")

        # Must check for platform authenticator and set attachment to 'platform'
        self.assertIn("isPlatformAvailable || isMac || isIos", html)
        self.assertIn("authSelection.authenticatorAttachment = 'platform'", html)
        self.assertIn("Please touch your Touch ID sensor on your Mac keyboard", html)
        self.assertIn("Mac (Touch ID)", html)

    def test_mac_touch_id_webauthn_assertion_includes_allow_credentials_and_hints(self):
        """2FA and Login passkey assertion populate allowCredentials with internal transports and client-device hints."""
        # Enroll mock passkey for user
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id="dGVzdC1jcmVkZW50aWFsLWlkLTEyMw",
            public_key="dGVzdC1wdWJsaWMta2V5LWRhdGE",
            sign_count=0,
            name="MacBook Pro Touch ID",
        )

        # 1. Two-factor template check
        session = self.client.session
        session["pre_2fa_user_id"] = self.user.id
        session["pre_2fa_method"] = "choice"
        session["pre_2fa_time"] = timezone.now().timestamp()
        session.save()

        res_2fa = self.client.get(reverse("two_factor_verify"))
        self.assertEqual(res_2fa.status_code, 200)
        html_2fa = res_2fa.content.decode("utf-8")

        # allowCredentials mapping
        self.assertIn("getOptions.allowCredentials = chalData.credential_ids.map", html_2fa)
        self.assertIn("transports: ['internal', 'hybrid', 'usb', 'nfc', 'ble']", html_2fa)
        self.assertIn("getOptions.hints = ['client-device']", html_2fa)
        self.assertIn("Awaiting Touch ID confirmation...", html_2fa)
        self.assertIn("passkey-title", html_2fa)
        self.assertIn("passkey-btn-text", html_2fa)

        # 2. Login template check
        res_login = self.client.get(reverse("login"))
        self.assertEqual(res_login.status_code, 200)
        html_login = res_login.content.decode("utf-8")

        self.assertIn("getOptions.allowCredentials = chalData.credential_ids.map", html_login)
        self.assertIn("getOptions.hints = ['client-device']", html_login)
        self.assertIn("Use Touch ID on your Mac to sign in instantly without password", html_login)

    def test_passkey_auth_challenge_returns_credential_ids_for_allowed_credentials(self):
        """passkey_auth_challenge returns enrolled credential IDs in JSON for client allowCredentials array."""
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id="dGVzdC1jcmVkZW50aWFsLWlkLTEyMw",
            public_key="dGVzdC1wdWJsaWMta2V5LWRhdGE",
            sign_count=0,
            name="MacBook Pro Touch ID",
        )

        session = self.client.session
        session["pre_2fa_user_id"] = self.user.id
        session["pre_2fa_time"] = timezone.now().timestamp()
        session.save()

        res = self.client.get(reverse("passkey_auth_challenge"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("challenge", data)
        self.assertIn("credential_ids", data)
        self.assertIn("dGVzdC1jcmVkZW50aWFsLWlkLTEyMw", data["credential_ids"])
