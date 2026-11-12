"""Tests for Progressive Web App (PWA) manifest, service worker headers,
offline fallback page, and installability elements."""

import json
import os
from django.conf import settings
from django.test import TestCase, Client
from django.urls import reverse


class PwaInstallabilityTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_manifest_endpoint_and_structure(self):
        """Verify /manifest.json returns HTTP 200, application/manifest+json, and valid schema."""
        response = self.client.get('/manifest.json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue('application/manifest+json' in response.headers.get('Content-Type', ''))
        cache_ctrl = response.headers.get('Cache-Control', '')
        self.assertIn('no-cache', cache_ctrl)
        self.assertIn('must-revalidate', cache_ctrl)

        content = b"".join(response.streaming_content) if response.streaming else response.content
        data = json.loads(content.decode('utf-8'))

        # Standard PWA requirements
        self.assertEqual(data.get('name'), 'Campus Nexus — University Digital Ecosystem')
        self.assertEqual(data.get('short_name'), 'Campus Nexus')
        self.assertEqual(data.get('display'), 'standalone')
        self.assertEqual(data.get('start_url'), '/dashboard/?source=pwa')
        self.assertEqual(data.get('background_color'), '#050B08')
        self.assertEqual(data.get('theme_color'), '#050B08')

        # Icon validation
        icons = data.get('icons', [])
        sizes = [icon.get('sizes') for icon in icons]
        self.assertIn('192x192', sizes)
        self.assertIn('512x512', sizes)

        purposes = [icon.get('purpose') for icon in icons]
        self.assertIn('maskable', purposes)

        # Shortcuts validation
        shortcuts = data.get('shortcuts', [])
        self.assertGreaterEqual(len(shortcuts), 1)

    def test_pwa_icon_files_exist_on_disk(self):
        """Ensure all required PWA icon assets exist in static/img/pwa/."""
        expected_icons = [
            'icon-192x192.png',
            'icon-512x512.png',
            'icon-maskable-192x192.png',
            'icon-maskable-512x512.png',
        ]
        pwa_dir = os.path.join(settings.BASE_DIR, 'static', 'img', 'pwa')
        for icon_name in expected_icons:
            icon_path = os.path.join(pwa_dir, icon_name)
            self.assertTrue(os.path.isfile(icon_path), f"Missing PWA icon: {icon_path}")

    def test_offline_fallback_page(self):
        """Verify /offline/ returns HTTP 200 and user-friendly offline instructions."""
        response = self.client.get(reverse('pwa_offline'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn("Offline", content)
        self.assertIn("Retry Connection", content)

    def test_service_worker_headers_and_scope(self):
        """Verify /sw.js serves with root Service-Worker-Allowed scope and no-cache."""
        response = self.client.get(reverse('service_worker'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('Service-Worker-Allowed'), '/')
        self.assertIn('application/javascript', response.headers.get('Content-Type', ''))

        content = b"".join(response.streaming_content) if response.streaming else response.content
        sw_text = content.decode('utf-8')
        self.assertIn('campus-nexus-v2', sw_text)
        self.assertIn('/offline/', sw_text)
        self.assertIn('navigate', sw_text)
        self.assertIn("url.pathname === '/manifest.json'", sw_text)
        self.assertIn("url.pathname.startsWith('/static/')", sw_text)

    def test_base_template_pwa_tags_and_controls(self):
        """Verify base template renders manifest link, install buttons, and iOS modal."""
        response = self.client.get(reverse('about'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Head tags
        self.assertIn('<link rel="manifest" href="/manifest.json">', content)
        self.assertIn('name="apple-mobile-web-app-capable"', content)
        self.assertIn('name="theme-color"', content)
        self.assertIn('content="#050B08"', content)
        self.assertIn('viewport-fit=cover', content)

        # Buttons and Modals
        self.assertIn('id="btn-pwa-install"', content)
        self.assertIn('id="btn-pwa-install-mobile"', content)
        self.assertIn('id="btn-pwa-install-pinned"', content)
        self.assertIn('id="btn-pwa-install-pinned" type="button"', content)
        self.assertIn('pwa-install-pinned-btn', content)
        self.assertIn('window.__nexusDeferredInstallPrompt', content)
        self.assertIn('window.isNexusIosSafari', content)
        self.assertIn('id="pwaIosInstallModal"', content)
        self.assertIn('id="pwaGenericInstallModal"', content)
        self.assertIn('window.triggerNexusPwaInstall', content)
        self.assertIn('window.isNexusRunningStandalone', content)
        self.assertEqual(content.count("serviceWorker.register('/sw.js'"), 1)
        self.assertIn("updateViaCache: 'none'", content)
        self.assertIn('v=3.2.2', content)

    def test_dashboard_post_login_pwa_prompt_elements(self):
        """Dashboard renders #postLoginPwaPrompt with install & dismiss CTAs, feature badges, and standalone check."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            username="2024BCSE088",
            enrollment_number="2024BCSE088",
            first_name="Rohan",
            phone_number="9876543288",
            password="StrongPassword123!",
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="M",
        )
        self.client.login(username="2024BCSE088", password="StrongPassword123!")

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Hero prompt container & IDs
        self.assertIn('id="postLoginPwaPrompt"', content)
        self.assertIn('id="btnPwaPromptInstall"', content)
        self.assertIn('id="btnPwaPromptDismiss"', content)
        self.assertIn('id="pwaPromptDeviceIcon"', content)
        self.assertIn('id="pwaPromptDeviceName"', content)

        # Value propositions
        self.assertIn('2-Week Passkey Sessions', content)
        self.assertIn('1-Touch Biometrics', content)
        self.assertIn('Offline Mode', content)
        self.assertIn('Fullscreen Speed', content)

        # Script functions
        self.assertIn('triggerPwaInstallFromPrompt', content)
        self.assertIn('dismissPostLoginPwaPrompt', content)
        self.assertIn('initPostLoginPwaPrompt', content)

    def test_sign_in_flow_sets_and_pops_just_signed_in_flag(self):
        """Standard browser login sets request.session['just_signed_in'], popped when rendering dashboard."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            username="2024BCSE089",
            enrollment_number="2024BCSE089",
            first_name="Pooja",
            phone_number="9876543289",
            password="StrongPassword123!",
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="F",
        )

        # Perform login via POST
        res = self.client.post(reverse('login'), {
            'username': '2024BCSE089',
            'password': 'StrongPassword123!',
        })
        self.assertEqual(res.status_code, 302)
        self.assertTrue(self.client.session.get('just_signed_in', False))

        # First visit to dashboard: context['just_signed_in'] should be True
        dash_res1 = self.client.get(reverse('dashboard'))
        self.assertEqual(dash_res1.status_code, 200)
        self.assertTrue(dash_res1.context['just_signed_in'])
        dash_content = dash_res1.content.decode('utf-8')
        self.assertIn('Just Signed In', dash_content)

        # Flag in session is now consumed (popped)
        self.assertFalse(self.client.session.get('just_signed_in', False))

        # Second visit to dashboard: context['just_signed_in'] should be False
        dash_res2 = self.client.get(reverse('dashboard'))
        self.assertEqual(dash_res2.status_code, 200)
        self.assertFalse(dash_res2.context['just_signed_in'])
