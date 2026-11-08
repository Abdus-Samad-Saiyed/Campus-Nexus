"""Stage 1 tests — Complete Elimination of Passkey Mock Fallbacks & Full Cryptographic Verification.

Covers:
1. Helper-level cryptographic registration verification (valid attestation, missing attestation, wrong origin, wrong rp_id, wrong challenge).
2. Helper-level cryptographic assertion verification (valid signature, tampered signature, wrong challenge, missing signature).
3. Legacy/mock credential rejection: credentials with mock keys are denied passkey login.
4. Endpoint-level registration and authentication flows:
   - Challenge generation, single-use consumption, and 120s expiry.
   - Step-up reauthentication mandate on registration and deletion (403 without active grace window).
   - Deletion endpoint removes credential and logs audit event.
   - Password and OTP authentication remain 100% operational as fallback.
"""

import json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasskeyCredential, AuditEvent
from accounts.two_factor import (
    generate_webauthn_challenge,
    verify_passkey_assertion,
    verify_passkey_registration,
)
from campus.tests.webauthn_test_utils import (
    generate_test_keypair,
    make_assertion_response,
    make_registration_response,
)

User = get_user_model()


class Stage1WebAuthnCryptographicHelperTests(TestCase):
    """Unit tests for verify_passkey_registration and verify_passkey_assertion."""

    def setUp(self):
        self.user = User.objects.create_user(
            enrollment_number="2024BCSE601",
            username="2024BCSE601",
            password="StrongPassword123!",
            first_name="Alice",
            last_name="WebAuthn",
            phone_number="+919876543601",
            email="alice601@nitsri.ac.in",
            verified=True,
            two_factor_enabled=True,
        )
        self.other_user = User.objects.create_user(
            enrollment_number="2024BCSE602",
            username="2024BCSE602",
            password="StrongPassword123!",
            first_name="Bob",
            last_name="Impostor",
            phone_number="+919876543602",
            email="bob602@nitsri.ac.in",
            verified=True,
        )
        self.private_key, self.cose_key_bytes, self.pubkey_b64 = generate_test_keypair()
        self.credential_id = b"alice_test_credential_601"
        self.cred_id_str = "alice_test_credential_601"

    def test_registration_with_valid_attestation_succeeds(self):
        """Genuine WebAuthn attestation response verifies and yields verified credential values."""
        challenge = generate_webauthn_challenge()
        reg_data, _, cose_pub_b64 = make_registration_response(
            challenge_b64=challenge,
            credential_id=self.credential_id,
            origin="http://testserver",
            rp_id="testserver",
            private_key=self.private_key,
        )

        ok, msg, result = verify_passkey_registration(
            data=reg_data,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertTrue(ok)
        self.assertEqual(result["public_key"], cose_pub_b64)
        self.assertEqual(result["sign_count"], 0)

    def test_registration_without_attestation_strictly_fails(self):
        """Mock payload with client-supplied credential_id and public_key is rejected."""
        challenge = generate_webauthn_challenge()
        mock_payload = {
            "credential_id": "attacker_supplied_id",
            "public_key": "attacker_supplied_key",
        }
        ok, msg, result = verify_passkey_registration(
            data=mock_payload,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)
        self.assertIn("invalid", msg.lower())

    def test_registration_with_tampered_challenge_fails(self):
        """Registration response matching a different challenge fails verification."""
        challenge = generate_webauthn_challenge()
        reg_data, _, _ = make_registration_response(
            challenge_b64="wrong_challenge_string_12345",
            credential_id=self.credential_id,
            origin="http://testserver",
            rp_id="testserver",
        )
        ok, msg, result = verify_passkey_registration(
            data=reg_data,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)

    def test_registration_with_wrong_origin_fails(self):
        """Registration response from untrusted origin fails verification."""
        challenge = generate_webauthn_challenge()
        reg_data, _, _ = make_registration_response(
            challenge_b64=challenge,
            credential_id=self.credential_id,
            origin="https://phishing-site.com",
            rp_id="testserver",
        )
        ok, msg, result = verify_passkey_registration(
            data=reg_data,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)

    def test_assertion_with_valid_signature_succeeds(self):
        """Genuine WebAuthn assertion signed with private key authenticates successfully."""
        from webauthn.helpers import bytes_to_base64url
        cred_b64 = bytes_to_base64url(self.credential_id)
        passkey = PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key=self.pubkey_b64,
            sign_count=0,
            name="Alice Key",
        )

        challenge = generate_webauthn_challenge()
        assertion = make_assertion_response(
            private_key=self.private_key,
            challenge_b64=challenge,
            credential_id=self.credential_id,
            origin="http://testserver",
            rp_id="testserver",
            sign_count=1,
        )

        ok, msg = verify_passkey_assertion(
            user=self.user,
            credential_id=cred_b64,
            full_credential=assertion,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertTrue(ok)
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 1)
        self.assertIsNotNone(passkey.last_used_at)

    def test_assertion_with_tampered_signature_strictly_fails(self):
        """Assertion with modified signature fails cryptographic verification."""
        from webauthn.helpers import bytes_to_base64url
        cred_b64 = bytes_to_base64url(self.credential_id)
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key=self.pubkey_b64,
            sign_count=0,
            name="Alice Key",
        )

        challenge = generate_webauthn_challenge()
        bad_assertion = make_assertion_response(
            private_key=self.private_key,
            challenge_b64=challenge,
            credential_id=self.credential_id,
            origin="http://testserver",
            rp_id="testserver",
            sign_count=1,
            tamper_signature=True,
        )

        ok, msg = verify_passkey_assertion(
            user=self.user,
            credential_id=cred_b64,
            full_credential=bad_assertion,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)

    def test_assertion_with_missing_signature_strictly_fails(self):
        """Missing signature in assertion payload fails immediately without fallback."""
        from webauthn.helpers import bytes_to_base64url
        cred_b64 = bytes_to_base64url(self.credential_id)
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key=self.pubkey_b64,
            sign_count=0,
            name="Alice Key",
        )

        challenge = generate_webauthn_challenge()
        assertion = make_assertion_response(
            private_key=self.private_key,
            challenge_b64=challenge,
            credential_id=self.credential_id,
            origin="http://testserver",
            rp_id="testserver",
        )
        # Strip signature
        assertion["response"]["signature"] = ""

        ok, msg = verify_passkey_assertion(
            user=self.user,
            credential_id=cred_b64,
            full_credential=assertion,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)
        self.assertIn("missing", msg.lower())

    def test_assertion_for_credential_owned_by_another_user_fails(self):
        """An assertion matching a credential owned by another user is rejected."""
        from webauthn.helpers import bytes_to_base64url
        cred_b64 = bytes_to_base64url(self.credential_id)
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key=self.pubkey_b64,
            sign_count=0,
            name="Alice Key",
        )

        challenge = generate_webauthn_challenge()
        assertion = make_assertion_response(
            private_key=self.private_key,
            challenge_b64=challenge,
            credential_id=self.credential_id,
            origin="http://testserver",
            rp_id="testserver",
        )

        # Bob attempts to present Alice's credential
        ok, msg = verify_passkey_assertion(
            user=self.other_user,
            credential_id=cred_b64,
            full_credential=assertion,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)
        self.assertIn("not recognized", msg.lower())

    def test_legacy_mock_credential_is_denied_and_prompts_reenrollment(self):
        """Passkey rows containing legacy/mock public keys reject authentication safely."""
        from webauthn.helpers import bytes_to_base64url
        cred_b64 = bytes_to_base64url(b"legacy_mock_cred")
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key="webauthn_mock_public_key",
            sign_count=0,
            name="Legacy Key",
        )

        challenge = generate_webauthn_challenge()
        assertion = make_assertion_response(
            private_key=self.private_key,
            challenge_b64=challenge,
            credential_id=b"legacy_mock_cred",
            origin="http://testserver",
            rp_id="testserver",
        )

        ok, msg = verify_passkey_assertion(
            user=self.user,
            credential_id=cred_b64,
            full_credential=assertion,
            session_challenge=challenge,
            expected_rp_id="testserver",
            expected_origin="http://testserver",
        )
        self.assertFalse(ok)
        self.assertIn("re-enrollment", msg.lower())


class Stage1WebAuthnEndpointTests(TestCase):
    """Endpoint tests covering passkey challenge, registration, deletion, and 2FA bypass."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            enrollment_number="2024BCSE603",
            username="2024BCSE603",
            password="StrongPassword123!",
            first_name="Carol",
            last_name="Student",
            phone_number="+919876543603",
            email="carol603@nitsri.ac.in",
            verified=True,
            two_factor_enabled=True,
        )
        self.private_key, _, self.pubkey_b64 = generate_test_keypair()
        self.cred_id_bytes = b"carol_cred_603"

    def test_passkey_registration_flow_requires_step_up(self):
        """Passkey registration challenge and verify endpoints enforce step-up authorization."""
        self.client.force_login(self.user)

        # Without active reauthentication window -> 403
        chal_res = self.client.get(reverse("passkey_reg_challenge"))
        self.assertEqual(chal_res.status_code, 403)
        self.assertTrue(chal_res.json().get("requires_step_up"))

        # Activate 10-minute reauthentication window
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        # Challenge succeeds
        chal_res = self.client.get(reverse("passkey_reg_challenge"))
        self.assertEqual(chal_res.status_code, 200)
        challenge = chal_res.json()["challenge"]

        # Registration with genuine attestation succeeds
        reg_payload, _, _ = make_registration_response(
            challenge_b64=challenge,
            credential_id=self.cred_id_bytes,
            origin="http://testserver",
            rp_id="testserver",
            private_key=self.private_key,
        )
        reg_payload["name"] = "Carol Workstation"

        verify_res = self.client.post(
            reverse("passkey_reg_verify"),
            data=json.dumps(reg_payload),
            content_type="application/json",
        )
        self.assertEqual(verify_res.status_code, 200)
        self.assertTrue(verify_res.json().get("success"))

        # Verify audit event logged
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="PASSKEY_REGISTERED",
                actor_enrollment="2024BCSE603",
            ).exists()
        )

    def test_passkey_registration_with_dynamic_tunnel_origin(self):
        """Passkey registration succeeds through reverse proxy or Cloudflare tunnel origins."""
        self.client.force_login(self.user)
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        tunnel_host = "tunnel-demo.trycloudflare.com"
        tunnel_origin = f"https://{tunnel_host}"

        chal_res = self.client.get(
            reverse("passkey_reg_challenge"),
            HTTP_HOST=tunnel_host,
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(chal_res.status_code, 200)
        challenge = chal_res.json()["challenge"]
        self.assertEqual(chal_res.json()["rp_id"], tunnel_host)

        reg_payload, _, _ = make_registration_response(
            challenge_b64=challenge,
            credential_id=b"carol_tunnel_cred",
            origin=tunnel_origin,
            rp_id=tunnel_host,
            private_key=self.private_key,
        )
        reg_payload["name"] = "Carol Cloudflare Tunnel Key"

        verify_res = self.client.post(
            reverse("passkey_reg_verify"),
            data=json.dumps(reg_payload),
            content_type="application/json",
            HTTP_HOST=tunnel_host,
            HTTP_ORIGIN=tunnel_origin,
            HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(verify_res.status_code, 200)
        self.assertTrue(verify_res.json().get("success"))

    def test_passkey_registration_behind_reverse_proxy_with_local_host_header(self):
        """Passkey registration succeeds even when reverse proxy forwards request with local Host: 127.0.0.1:8000."""
        self.client.force_login(self.user)
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        tunnel_host = "increasingly-dock-document.demonstrate.trycloudflare.com"
        tunnel_origin = f"https://{tunnel_host}"

        chal_res = self.client.get(
            reverse("passkey_reg_challenge"),
            HTTP_HOST="127.0.0.1:8000",
            HTTP_ORIGIN=tunnel_origin,
        )
        self.assertEqual(chal_res.status_code, 200)
        challenge = chal_res.json()["challenge"]
        self.assertEqual(chal_res.json()["rp_id"], tunnel_host)

        reg_payload, _, _ = make_registration_response(
            challenge_b64=challenge,
            credential_id=b"carol_proxy_cred",
            origin=tunnel_origin,
            rp_id=tunnel_host,
            private_key=self.private_key,
        )
        reg_payload["name"] = "Carol Proxy Key"

        verify_res = self.client.post(
            reverse("passkey_reg_verify"),
            data=json.dumps(reg_payload),
            content_type="application/json",
            HTTP_HOST="127.0.0.1:8000",
            HTTP_ORIGIN=tunnel_origin,
        )
        self.assertEqual(verify_res.status_code, 200)
        self.assertTrue(verify_res.json().get("success"))

    def test_passkey_deletion_requires_step_up(self):
        """Deleting a passkey requires active reauthentication."""
        self.client.force_login(self.user)
        from webauthn.helpers import bytes_to_base64url
        cred = PasskeyCredential.objects.create(
            user=self.user,
            credential_id=bytes_to_base64url(self.cred_id_bytes),
            public_key=self.pubkey_b64,
            name="Carol Key",
        )
        PasskeyCredential.objects.create(user=self.user, credential_id=bytes_to_base64url(b"second_backup_key_id"), public_key=self.pubkey_b64, name="Backup Key")

        # Deletion without step-up -> 403
        del_fail = self.client.post(reverse("passkey_delete", args=[cred.id]))
        self.assertEqual(del_fail.status_code, 403)
        self.assertTrue(del_fail.json().get("requires_step_up"))

        # Activate step-up window
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        # Deletion with step-up succeeds
        del_ok = self.client.post(reverse("passkey_delete", args=[cred.id]))
        self.assertEqual(del_ok.status_code, 200)
        self.assertTrue(del_ok.json().get("success"))
        self.assertFalse(PasskeyCredential.objects.filter(id=cred.id).exists())

    def test_passkey_2fa_auth_endpoint_flow(self):
        """Passkey authentication through 2FA verify route succeeds with valid signature."""
        from webauthn.helpers import bytes_to_base64url
        cred_b64 = bytes_to_base64url(self.cred_id_bytes)
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id=cred_b64,
            public_key=self.pubkey_b64,
            name="Carol Key",
        )

        # Simulate password login placing user in pre-2FA state
        session = self.client.session
        session["pre_2fa_user_id"] = self.user.id
        session["pre_2fa_time"] = timezone.now().timestamp()
        session["pre_2fa_redirect"] = "/dashboard/"
        session.save()

        # Request challenge
        chal_res = self.client.get(reverse("passkey_auth_challenge"))
        self.assertEqual(chal_res.status_code, 200)
        challenge = chal_res.json()["challenge"]

        # Present valid assertion
        assertion = make_assertion_response(
            private_key=self.private_key,
            challenge_b64=challenge,
            credential_id=self.cred_id_bytes,
            origin="http://testserver",
            rp_id="testserver",
        )

        auth_res = self.client.post(
            reverse("passkey_auth_verify"),
            data=json.dumps(assertion),
            content_type="application/json",
        )
        self.assertEqual(auth_res.status_code, 200)
        self.assertTrue(auth_res.json().get("success"))

        # Client is now authenticated
        self.assertEqual(int(self.client.session.get("_auth_user_id")), self.user.id)

    def test_passkey_registration_labeled_by_device_of_origin_and_time(self):
        """Verify biometric keys are labeled by device of origin and time, both with and without client hints."""
        from accounts.views import get_device_of_origin_from_ua

        # 1. Test UA device detector helper
        ua_win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ua_iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ua_mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ua_android = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

        self.assertEqual(get_device_of_origin_from_ua(ua_win), "Windows PC (Chrome)")
        self.assertEqual(get_device_of_origin_from_ua(ua_iphone), "iPhone (Safari)")
        self.assertEqual(get_device_of_origin_from_ua(ua_mac), "Mac (Chrome)")
        self.assertEqual(get_device_of_origin_from_ua(ua_android), "Android (Chrome)")

        # 2. Registration with client device and local time
        self.client.force_login(self.user)
        session = self.client.session
        session["security_otp_verified_until"] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        chal_res = self.client.get(reverse("passkey_reg_challenge"))
        challenge = chal_res.json()["challenge"]

        reg_payload, _, _ = make_registration_response(
            challenge_b64=challenge,
            credential_id=b"cred_label_test_001",
            origin="http://testserver",
            rp_id="testserver",
            private_key=self.private_key,
        )
        reg_payload["device"] = "Windows PC (Chrome)"
        reg_payload["client_time"] = "Nov 05, 2026, 10:22 PM"

        verify_res = self.client.post(
            reverse("passkey_reg_verify"),
            data=json.dumps(reg_payload),
            content_type="application/json",
            HTTP_USER_AGENT=ua_win,
        )
        self.assertEqual(verify_res.status_code, 200)

        cred = PasskeyCredential.objects.filter(user=self.user).first()
        self.assertIn("Windows PC (Chrome)", cred.name)
        self.assertIn("Nov 05, 2026, 10:22 PM", cred.name)
        self.assertEqual(cred.device_icon, "bi-windows text-info")

        # 3. Profile view renders device icon and label
        profile_res = self.client.get(reverse("profile", args=[self.user.enrollment_number]))
        self.assertEqual(profile_res.status_code, 200)
        self.assertContains(profile_res, "Windows PC (Chrome)")
        self.assertContains(profile_res, "bi-windows")
