import json
from datetime import datetime as dt_cls, timedelta
from django.core.cache import cache
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, PasskeyCredential, PhoneOTP
from accounts.two_factor import create_phone_otp, can_resend_otp
from accounts.utils import is_passkey_step_up_active
from campus.tests.test_stage1_passkeys_cryptographic import generate_test_keypair, make_registration_response


class PasskeyGraceAndVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.verified_user = User.objects.create_user(
            username='2024BCSE100',
            enrollment_number='2024BCSE100',
            email='user100@campus.edu',
            phone_number='9876543210',
            password='TestPassword@123',
            verified=True,
            is_active=True,
        )
        self.unverified_user = User.objects.create_user(
            username='2024BCSE101',
            enrollment_number='2024BCSE101',
            email='user101@campus.edu',
            phone_number='9876543211',
            password='TestPassword@123',
            verified=False,
            is_active=True,
        )
        self.private_key, _, self.pubkey_b64 = generate_test_keypair()

    def tearDown(self):
        cache.clear()

    def test_new_account_creation_early_launch_sets_30m_grace_window(self):
        """New account creation under Early Launch sets 30-minute grace window in session."""
        with self.settings(EARLY_LAUNCH_MODE=True):
            resp = self.client.post(
                reverse('apply_account') + '?mode=early',
                {
                    'enrollment_number': '2024BCSE200',
                    'full_name': 'New Student',
                    'email': 'new200@campus.edu',
                    'phone_number': '9876543220',
                    'password': 'StrongPassword@123',
                    'confirm_password': 'StrongPassword@123',
                    'gender': 'M',
                    'self_declared_truth': 'on',
                    'designated_admin': '',
                },
                follow=True,
            )
            self.assertEqual(resp.status_code, 200)
            created_user = User.objects.filter(enrollment_number='2024BCSE200').first()
            self.assertIsNotNone(created_user)

            # Check session values
            session = self.client.session
            self.assertIn('security_otp_verified_until', session)
            self.assertIn('auth_timestamp', session)

            sec_dt = dt_cls.fromisoformat(session['security_otp_verified_until'])
            diff_minutes = (sec_dt - timezone.now()).total_seconds() / 60
            self.assertGreater(diff_minutes, 25)
            self.assertLessEqual(diff_minutes, 31)

            # Visiting settings page shows grace active without demanding OTP
            settings_resp = self.client.get(reverse('account_settings_section', args=['security']))
            self.assertEqual(settings_resp.status_code, 200)
            self.assertTrue(settings_resp.context.get('grace_active'))

    def test_unverified_account_allowed_passkey_challenge(self):
        """Unverified accounts with active reauthentication can successfully obtain passkey registration challenge."""
        self.client.force_login(self.unverified_user)
        # Give step-up session
        session = self.client.session
        session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=30)).isoformat()
        session.save()

        res = self.client.get(reverse('passkey_reg_challenge'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('challenge', data)
        self.assertIn('exclude_credentials', data)

    def test_verified_account_allowed_passkey_challenge(self):
        """Verified accounts with active grace period successfully obtain passkey registration challenge."""
        self.client.force_login(self.verified_user)
        session = self.client.session
        session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=30)).isoformat()
        session.save()

        res = self.client.get(reverse('passkey_reg_challenge'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('challenge', data)
        self.assertIn('exclude_credentials', data)

    def test_passkey_delete_allows_single_passkey_deletion(self):
        """Deleting the only passkey succeeds and does not artificially block with anti-lockout error."""
        cred = PasskeyCredential.objects.create(
            user=self.verified_user,
            credential_id='test_cred_single',
            public_key='test_pubkey',
            name='Only Passkey',
        )
        self.assertEqual(PasskeyCredential.objects.filter(user=self.verified_user).count(), 1)

        self.client.force_login(self.verified_user)
        session = self.client.session
        session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=30)).isoformat()
        session.save()

        res = self.client.post(reverse('passkey_delete', args=[cred.id]))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('success'))
        self.assertEqual(PasskeyCredential.objects.filter(user=self.verified_user).count(), 0)

    def test_verify_security_otp_clears_ratelimit_cache(self):
        """Successful security OTP verification clears rate limit cache keys for send_security_otp."""
        self.client.force_login(self.verified_user)
        otp, code = create_phone_otp(user=self.verified_user, purpose='SECURITY_CHANGE')

        # Simulate triggered rate limit cache on client IP 127.0.0.1
        cache.set('ratelimit:send_security_otp:127.0.0.1', 5, timeout=60)
        self.assertEqual(cache.get('ratelimit:send_security_otp:127.0.0.1'), 5)

        res = self.client.post(
            reverse('verify_security_otp'),
            data=json.dumps({'otp_code': code}),
            content_type='application/json',
            REMOTE_ADDR='127.0.0.1',
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('success'))

        # Cache key should be deleted/reset
        self.assertIsNone(cache.get('ratelimit:send_security_otp:127.0.0.1'))

        # Session should have 30 minute grace window
        session = self.client.session
        sec_dt = dt_cls.fromisoformat(session['security_otp_verified_until'])
        diff_minutes = (sec_dt - timezone.now()).total_seconds() / 60
        self.assertGreater(diff_minutes, 25)

    def test_can_resend_otp_allows_immediate_resend_after_otp_used(self):
        """If previous OTP was used, can_resend_otp returns True immediately without wait cooldown."""
        otp, code = create_phone_otp(user=self.verified_user, purpose='SECURITY_CHANGE')
        otp.is_used = True
        otp.save()

        can_resend, wait_sec = can_resend_otp(user=self.verified_user, purpose='SECURITY_CHANGE')
        self.assertTrue(can_resend)
        self.assertEqual(wait_sec, 0)

    def test_google_bound_passkey_naming(self):
        """Passkey registered on Chrome or Android labels bound Google account explicitly."""
        self.client.force_login(self.verified_user)
        session = self.client.session
        session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=30)).isoformat()
        session.save()

        chal_res = self.client.get(reverse('passkey_reg_challenge'))
        self.assertEqual(chal_res.status_code, 200)
        challenge = chal_res.json()['challenge']

        reg_payload, _, _ = make_registration_response(
            challenge_b64=challenge,
            credential_id=b'google_bound_cred_1',
            origin='http://testserver',
            rp_id='testserver',
            private_key=self.private_key,
        )
        reg_payload['name'] = 'Passkey — 11/20/2026'

        # Test Windows Hello takes precedence on Windows machines
        windows_chrome_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        res = self.client.post(
            reverse('passkey_reg_verify'),
            data=json.dumps(reg_payload),
            content_type='application/json',
            HTTP_USER_AGENT=windows_chrome_ua,
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('success'))

        cred = PasskeyCredential.objects.filter(user=self.verified_user).first()
        self.assertIsNotNone(cred)
        self.assertIn('Windows Hello', cred.name)
        self.assertIn('Windows PC (Chrome)', cred.name)
        self.assertEqual(cred.device_icon, 'bi-windows text-info')

        # Test Google Password Manager on Android
        PasskeyCredential.objects.all().delete()
        chal_res2 = self.client.post(reverse('passkey_reg_challenge'))
        challenge2 = chal_res2.json()['challenge']
        reg_payload2, _, _ = make_registration_response(
            challenge_b64=challenge2,
            credential_id=b'google_bound_cred_android',
            origin='http://testserver',
            rp_id='testserver',
            private_key=self.private_key,
        )
        android_ua = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        res2 = self.client.post(
            reverse('passkey_reg_verify'),
            data=json.dumps(reg_payload2),
            content_type='application/json',
            HTTP_USER_AGENT=android_ua,
        )
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json().get('success'))
        cred_android = PasskeyCredential.objects.filter(user=self.verified_user).first()
        self.assertIsNotNone(cred_android)
        self.assertIn('Google Password Manager', cred_android.name)
        self.assertIn('Android (Chrome)', cred_android.name)
