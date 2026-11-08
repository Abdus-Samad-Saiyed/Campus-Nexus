"""Comprehensive Test Suite for Multi-Platform Simultaneous Sign-In Policy.

Rules under test:
1. Allow simultaneous sign-in across different platforms (e.g. Windows, Android, iOS).
2. One sign in per platform at a time (concurrent login on same platform terminates prior session on that platform).
3. Multiple sign-ins across different platforms allowed up to a maximum of 3.
4. Logging in on a 4th platform evicts the least recently active platform session.
5. Logging out on one platform only revokes that platform's session, leaving other platforms active.
"""

import time
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserPlatformSession
from accounts.utils import detect_platform_from_request

User = get_user_model()


class MultiPlatformSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(
            username="2024BCSE095",
            enrollment_number="2024BCSE095",
            first_name="Farhan",
            phone_number="9876543295",
            password=self.password,
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="M",
        )

    def _create_platform_client(self, platform_name: str, user_agent: str = "") -> Client:
        """Create a Django test Client pre-configured with platform headers."""
        extra = {
            'HTTP_X_PLATFORM': platform_name,
            'HTTP_X_LOADTEST_BYPASS': 'campus-nexus-benchmark-active',
        }
        if user_agent:
            extra['HTTP_USER_AGENT'] = user_agent
        return Client(**extra)

    def test_platform_detection_rules(self):
        """Platform detector correctly normalizes User-Agent, Sec-CH-UA-Platform, and X-Platform headers."""
        class DummyRequest:
            def __init__(self, meta):
                self.META = meta
                self.headers = {}

        # 1. Explicit override
        req = DummyRequest({'HTTP_X_PLATFORM': 'android'})
        self.assertEqual(detect_platform_from_request(req), 'android')

        # 2. Windows UA
        req = DummyRequest({'HTTP_USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'})
        self.assertEqual(detect_platform_from_request(req), 'windows')

        # 3. Android UA
        req = DummyRequest({'HTTP_USER_AGENT': 'Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Chrome/120.0'})
        self.assertEqual(detect_platform_from_request(req), 'android')

        # 4. iOS UA
        req = DummyRequest({'HTTP_USER_AGENT': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari'})
        self.assertEqual(detect_platform_from_request(req), 'ios')

        # 5. macOS UA
        req = DummyRequest({'HTTP_USER_AGENT': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15'})
        self.assertEqual(detect_platform_from_request(req), 'macos')

        # 6. Linux UA
        req = DummyRequest({'HTTP_USER_AGENT': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0)'})
        self.assertEqual(detect_platform_from_request(req), 'linux')

    def test_different_platforms_can_be_signed_in_simultaneously(self):
        """User can be simultaneously signed in on Windows and Android."""
        client_win = self._create_platform_client('windows')
        client_droid = self._create_platform_client('android')

        # Login on Windows
        res_win = client_win.post(reverse('login'), {
            'username': '2024BCSE095',
            'password': self.password,
        })
        self.assertEqual(res_win.status_code, 302)

        # Login on Android
        res_droid = client_droid.post(reverse('login'), {
            'username': '2024BCSE095',
            'password': self.password,
        })
        self.assertEqual(res_droid.status_code, 302)

        # Verify both sessions are registered
        self.assertEqual(UserPlatformSession.objects.filter(user=self.user).count(), 2)

        # Both can access authenticated pages simultaneously
        dash_win = client_win.get(reverse('dashboard'))
        self.assertEqual(dash_win.status_code, 200)

        dash_droid = client_droid.get(reverse('dashboard'))
        self.assertEqual(dash_droid.status_code, 200)

    def test_three_distinct_platforms_allowed_simultaneously(self):
        """User can maintain simultaneous active sessions across 3 distinct platforms (Windows, Android, iOS)."""
        client_win = self._create_platform_client('windows')
        client_droid = self._create_platform_client('android')
        client_ios = self._create_platform_client('ios')

        client_win.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        client_droid.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        client_ios.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        self.assertEqual(UserPlatformSession.objects.filter(user=self.user).count(), 3)

        # All 3 platforms can make authenticated requests simultaneously
        self.assertEqual(client_win.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(client_droid.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(client_ios.get(reverse('dashboard')).status_code, 200)

    def test_same_platform_replaces_prior_session_on_that_platform_only(self):
        """Logging in from a 2nd browser on Windows terminates the 1st Windows session, leaving Android untouched."""
        client_win1 = self._create_platform_client('windows')
        client_droid = self._create_platform_client('android')
        client_win2 = self._create_platform_client('windows')

        # 1. Login on Windows 1 and Android
        client_win1.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        client_droid.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        # 2. Login on Windows 2 (same platform as Windows 1)
        client_win2.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        # 3. Windows 1 must be terminated on subsequent request
        res_win1 = client_win1.get(reverse('dashboard'))
        self.assertRedirects(res_win1, reverse('login') + '?next=' + reverse('dashboard'))

        # 4. Windows 2 is authenticated
        self.assertEqual(client_win2.get(reverse('dashboard')).status_code, 200)

        # 5. Android session is completely unaffected and remains authenticated!
        self.assertEqual(client_droid.get(reverse('dashboard')).status_code, 200)

        # Active platform sessions count should still be 2 (1 Windows + 1 Android)
        self.assertEqual(UserPlatformSession.objects.filter(user=self.user).count(), 2)

    def test_fourth_platform_evicts_oldest_platform_session(self):
        """Logging in on a 4th platform evicts the least recently active platform to enforce max 3."""
        client_p1 = self._create_platform_client('windows')
        client_p2 = self._create_platform_client('android')
        client_p3 = self._create_platform_client('ios')
        client_p4 = self._create_platform_client('linux')

        # 1. Log in to 3 platforms
        client_p1.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        time.sleep(0.01)
        client_p2.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        time.sleep(0.01)
        client_p3.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        # Artificially order last_activity so p1 (windows) is distinctly the oldest
        s1 = UserPlatformSession.objects.get(user=self.user, platform='windows')
        s2 = UserPlatformSession.objects.get(user=self.user, platform='android')
        s3 = UserPlatformSession.objects.get(user=self.user, platform='ios')

        now = timezone.now()
        s1.last_activity = now - timezone.timedelta(minutes=30)
        s1.save(update_fields=['last_activity'])
        s2.last_activity = now - timezone.timedelta(minutes=15)
        s2.save(update_fields=['last_activity'])
        s3.last_activity = now - timezone.timedelta(minutes=5)
        s3.save(update_fields=['last_activity'])

        # 2. Log in on 4th platform (linux)
        client_p4.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        # Total active platforms must not exceed 3
        active_sessions = UserPlatformSession.objects.filter(user=self.user)
        self.assertEqual(active_sessions.count(), 3)

        active_platforms = set(active_sessions.values_list('platform', flat=True))
        self.assertIn('linux', active_platforms)
        self.assertIn('android', active_platforms)
        self.assertIn('ios', active_platforms)
        # Oldest platform (windows) was evicted!
        self.assertNotIn('windows', active_platforms)

        # p1 (windows) request is now rejected
        res_p1 = client_p1.get(reverse('dashboard'))
        self.assertRedirects(res_p1, reverse('login') + '?next=' + reverse('dashboard'))

        # p2, p3, p4 are all active
        self.assertEqual(client_p2.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(client_p3.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(client_p4.get(reverse('dashboard')).status_code, 200)

    def test_single_platform_logout_preserves_other_platforms(self):
        """Logging out on one platform (e.g. Windows) leaves other platform sessions (e.g. Android) active."""
        client_win = self._create_platform_client('windows')
        client_droid = self._create_platform_client('android')

        client_win.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        client_droid.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        # Logout on Windows
        client_win.post(reverse('logout'))

        # Windows session is terminated
        self.assertEqual(UserPlatformSession.objects.filter(user=self.user, platform='windows').count(), 0)

        # Android session is STILL active
        self.assertEqual(UserPlatformSession.objects.filter(user=self.user, platform='android').count(), 1)
        self.assertEqual(client_droid.get(reverse('dashboard')).status_code, 200)

    def test_profile_displays_active_platform_sessions(self):
        """Profile view renders active platform sessions badge and device listings."""
        client_win = self._create_platform_client('windows')
        client_droid = self._create_platform_client('android')

        client_win.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})
        client_droid.post(reverse('login'), {'username': '2024BCSE095', 'password': self.password})

        res = client_win.get(reverse('profile', kwargs={'enrollment_number': '2024BCSE095'}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        self.assertIn('Active Platform Sessions', content)
        self.assertIn('2/3 Active', content)
        self.assertIn('Windows', content)
        self.assertIn('Android', content)
        self.assertIn('Current Device', content)
