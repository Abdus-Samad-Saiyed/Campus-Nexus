"""Comprehensive Test Suite for Mobile/Android UI Settings and 2-Week Downloaded App Passkey Sessions.

Covers:
1. Mobile & Android UI controls: palette button in mobile action bar, mobile drawer appearance section,
   #uiSettingsModal with all 8 curated accents and custom hex picker, profile appearance card.
2. Downloaded app 2-week persistent session: session expiry set to 14 days (1,209,600s),
   idle timeout extended to 14 days in IdleSessionTimeoutMiddleware.
3. Passkey-only sign-in on downloaded app: challenge generation for remembered user without password,
   direct passkey assertion login, and account forget endpoint.
"""

import json
import os
import time
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasskeyCredential
from accounts.middleware import IdleSessionTimeoutMiddleware

User = get_user_model()


class MobileAccentAndUISettingsTests(TestCase):
    """Test visibility and rendering of accent colours and UI settings on mobile/Android viewports."""

    def setUp(self):
        self.client = Client()
        self.password = "SecurePassword123!"
        self.user = User.objects.create_user(
            username="2024BCSE050",
            enrollment_number="2024BCSE050",
            first_name="Amina",
            phone_number="9876543250",
            password=self.password,
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="F",
        )
        self.client.login(username="2024BCSE050", password=self.password)

    def test_mobile_header_and_drawer_have_ui_settings_triggers(self):
        """Mobile top quick actions and navigation drawer contain UI settings modal triggers."""
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)

        # 1. Palette button in mobile top bar
        self.assertContains(res, 'data-bs-target="#uiSettingsModal"')
        self.assertContains(res, 'bi-palette2')

        # 2. Appearance & UI Settings in mobile drawer
        self.assertContains(res, 'Appearance &amp; UI Settings')
        self.assertContains(res, 'mobileDrawerAccentName')

    def test_ui_settings_modal_elements_rendered(self):
        """Global UI settings modal contains all 8 curated palettes, hex picker, and watermark controls."""
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)

        # Modal shell
        self.assertContains(res, 'id="uiSettingsModal"')

        # All 8 curated accent swatches
        for accent in ['emerald', 'cyan', 'indigo', 'amber', 'rose', 'purple', 'teal', 'orange']:
            self.assertContains(res, f"setAccentFromModal('{accent}')")

        # Custom hex color picker inputs
        self.assertContains(res, 'id="modal-fx-hex-picker"')
        self.assertContains(res, 'id="modal-fx-hex-input"')

        # Background visual controls
        self.assertContains(res, 'id="modal-fx-watermark-mode"')
        self.assertContains(res, 'id="modal-fx-opacity-slider"')
        self.assertContains(res, 'id="modal-fx-gear-complexity"')

    def test_profile_page_has_appearance_card_for_self(self):
        """User profile page renders the Appearance & Theme card for authenticated student."""
        res = self.client.get(reverse('profile', kwargs={'enrollment_number': self.user.enrollment_number}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Appearance & Theme')
        self.assertContains(res, 'Customize')

    def test_customization_engine_loaded_unconditionally_across_viewports(self):
        """Customization script & styles load on all viewports (no desktop-only innerWidth gating)."""
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode('utf-8')

        # Must load watermark.min.js & watermark.min.css unconditionally (contains setNexusAccent / setNexusCustomHex)
        self.assertIn('data-asset="watermark.js"', html)
        self.assertIn('data-asset="watermark.css"', html)
        self.assertNotIn('if (window.innerWidth >= 992)', html)

        # Pre-paint restoration and mobile PWA edge-to-edge surface styling
        self.assertIn('viewport-fit=cover', html)
        self.assertIn('id="nexus-theme-color-meta" name="theme-color" content="#050B08"', html)
        self.assertIn('--surface-solid', html)
        self.assertIn('window.syncAccentUIElements = syncAccentUIElements', html)
        self.assertIn('modal-fx-hex-picker', html)
        self.assertIn('setCustomHexFromModal(this.value)', html)
        self.assertIn('rawOpacity <= 1 ? Math.round(rawOpacity * 100)', html)

    def test_paired_static_assets_are_in_sync_and_css_prevents_bottom_strip(self):
        """Paired CSS/JS assets stay byte-identical and CSS propagates dynamic --bg cleanly on mobile PWA."""
        static_dir = os.path.join(settings.BASE_DIR, 'static')
        pairs = [
            (os.path.join(static_dir, 'css', 'main.css'), os.path.join(static_dir, 'css', 'main.min.css')),
            (os.path.join(static_dir, 'css', 'watermark.css'), os.path.join(static_dir, 'css', 'watermark.min.css')),
            (os.path.join(static_dir, 'js', 'watermark.js'), os.path.join(static_dir, 'js', 'watermark.min.js')),
        ]
        for src, minified in pairs:
            with open(src, 'rb') as f_src, open(minified, 'rb') as f_min:
                self.assertEqual(f_src.read(), f_min.read(), f"{minified} is out of sync with {src}")

        with open(os.path.join(static_dir, 'css', 'main.css'), 'r', encoding='utf-8') as f:
            main_css = f.read()

        self.assertIn('min-height:100dvh', main_css)
        self.assertIn('background-attachment:scroll !important', main_css)
        self.assertIn('--surface-solid', main_css)
        self.assertIn('overflow-x: clip;', main_css)
        self.assertIn('background-color: var(--bg, #050B08);', main_css)
        self.assertNotIn('background:rgba(14,23,18,0.98)', main_css)
        self.assertNotIn('background: #0b140f !important', main_css)
        self.assertNotIn('background-color: #0c1611 !important', main_css)


class DownloadedAppTwoWeekSessionTests(TestCase):
    """Test 2-week persistent sessions and passkey-only authentication on downloaded app."""

    def setUp(self):
        self.client = Client()
        self.password = "AppSecretPassword123!"
        self.user = User.objects.create_user(
            username="2024BCSE080",
            enrollment_number="2024BCSE080",
            first_name="Zubair",
            phone_number="9876543280",
            password=self.password,
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="M",
        )
        self.two_weeks_seconds = 14 * 86400  # 1,209,600 seconds

    def test_downloaded_app_login_sets_two_week_expiry(self):
        """Login in downloaded app (standalone mode) sets 2-week session age and remembered cookie."""
        self.client.cookies['nexus_standalone_app'] = '1'

        login_res = self.client.post(reverse('login'), {
            'username': '2024BCSE080',
            'password': self.password,
        })
        self.assertRedirects(login_res, reverse('dashboard'))

        # Verify session expiry is set to 2 weeks (14 days)
        session = self.client.session
        self.assertTrue(session.get('is_downloaded_app'))
        self.assertEqual(session.get_expiry_age(), self.two_weeks_seconds)

        # Verify 2-week remembered user cookie
        remembered_cookie = login_res.cookies.get('nexus_app_remembered_user')
        self.assertIsNotNone(remembered_cookie)
        self.assertEqual(remembered_cookie.value, '2024BCSE080')
        self.assertEqual(remembered_cookie['max-age'], self.two_weeks_seconds)

    def test_idle_session_middleware_exempts_app_from_short_timeout(self):
        """IdleSessionTimeoutMiddleware does not expire downloaded app session after 30 minutes."""
        self.client.cookies['nexus_standalone_app'] = '1'
        self.client.post(reverse('login'), {
            'username': '2024BCSE080',
            'password': self.password,
        })

        # Simulate 2 hours of inactivity (far exceeding default 30-minute SESSION_IDLE_TIMEOUT)
        session = self.client.session
        session['last_activity'] = time.time() - 7200  # 2 hours ago
        session.save()

        # Request dashboard - should NOT be logged out because it's a downloaded app with 2-week window
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context['user'].is_authenticated)

    def test_passkey_only_login_for_remembered_app_user(self):
        """In downloaded app with passkey, user can request challenge and log in using ONLY the passkey."""
        # Create a mock registered passkey for Zubair
        credential_id = "mock_app_cred_zubair_123"
        public_key = "mock_public_key_bytes"
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=0,
            name="Zubair Android Phone",
        )

        # 1. On login screen with 2-week cookie, passkey card is visible
        self.client.cookies['nexus_standalone_app'] = '1'
        self.client.cookies['nexus_app_remembered_user'] = '2024BCSE080'

        login_page_res = self.client.get(reverse('login'))
        self.assertEqual(login_page_res.status_code, 200)
        self.assertContains(login_page_res, 'Sign In with Passkey')
        self.assertContains(login_page_res, 'Welcome back, Zubair!')
        self.assertContains(login_page_res, '2-Week Session')

        # 2. User requests passkey challenge directly (without typing password!)
        chal_res = self.client.get(
            reverse('passkey_auth_challenge'),
            {'enrollment_number': '2024BCSE080'},
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(chal_res.status_code, 200)
        chal_data = chal_res.json()
        self.assertIn('challenge', chal_data)
        self.assertIn(credential_id, chal_data['credential_ids'])

        # 3. Verify passkey assertion directly logs the user in with 2-week session
        # Mock client assertion payload
        raw_challenge = chal_data['challenge']
        payload = {
            'credential_id': credential_id,
            'client_data_json': 'mock_client_data_json',
            'response': {
                'authenticatorData': 'mock_authenticator_data',
                'clientDataJSON': 'mock_client_data_json',
                'signature': 'mock_signature',
                'userHandle': '',
            }
        }

        # Patch verify_passkey_assertion to return True
        from unittest.mock import patch
        with patch('accounts.views.verify_passkey_assertion', return_value=(True, "Success")):
            ver_res = self.client.post(
                reverse('passkey_auth_verify'),
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_X_NEXUS_APP='1',
            )
            self.assertEqual(ver_res.status_code, 200)
            self.assertTrue(ver_res.json().get('success'))

        # Verify session is authenticated and has 2-week expiry
        dash_res = self.client.get(reverse('dashboard'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['user'], self.user)
        self.assertEqual(self.client.session.get_expiry_age(), self.two_weeks_seconds)

    def test_forget_app_remembered_user(self):
        """User can switch account by calling forget_app_remembered_user endpoint."""
        self.client.cookies['nexus_app_remembered_user'] = '2024BCSE080'
        res = self.client.post(reverse('forget_app_remembered_user'))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('success'))
        cookie = res.cookies.get('nexus_app_remembered_user')
        self.assertEqual(cookie.value, '')
