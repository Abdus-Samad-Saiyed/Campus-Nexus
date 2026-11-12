"""Unit and integration tests for Account Settings Information Architecture and Destinations."""

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.models import User, Friendship, PasskeyCredential


class AccountSettingsArchitectureTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            enrollment_number="2024BCSE999",
            username="2024BCSE999",
            email="settings_user@campus.edu",
            password="SecurePassword123!",
            first_name="Alice",
            last_name="Tester",
            phone_number="9876543210",
            degree="Bachelors",
            branch="Computer Science",
            roll_number="CS-999",
            joining_year=2024,
            is_active=True,
            verified=True,
        )
        self.client.force_login(self.user)

    def test_settings_requires_login(self):
        """Unauthenticated requests must be redirected to sign in."""
        anon_client = Client()
        res = anon_client.get(reverse('account_settings'))
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login/', res.url)

    def test_settings_overview_landing_page(self):
        """Settings landing page renders all 4 distinct destinations with summary details."""
        res = self.client.get(reverse('account_settings'))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'accounts/settings.html')
        content = res.content.decode('utf-8')

        # Check section titles and descriptions
        self.assertIn("Personal information", content)
        self.assertIn("Privacy & visibility", content)
        self.assertIn("Security & sign-in", content)
        self.assertIn("Notifications", content)

        # Check summaries
        self.assertIn("Update your photo and contact details.", content)
        self.assertIn("Choose what other students can see.", content)
        self.assertIn("Manage personal info", content)
        self.assertIn("Manage privacy", content)
        self.assertIn("Manage security & sign-in", content)
        self.assertIn("Manage notifications", content)

    def test_personal_information_destination_renders_and_updates(self):
        """Personal info destination displays grouped fields and fixed identifiers, saves without security prompt."""
        res = self.client.get(reverse('account_settings_section', kwargs={'section': 'personal'}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        self.assertIn("Fixed Academic Identifiers", content)
        self.assertIn("2024BCSE999", content)
        self.assertIn("999", content)
        self.assertIn("CSE", content)
        self.assertIn("Save changes", content)

        # Security controls must NOT be present in personal info form
        self.assertNotIn('name="current_password"', content)
        self.assertNotIn('name="new_password"', content)
        self.assertNotIn('name="confirm_password"', content)

        # Update bio and display info
        post_res = self.client.post(
            reverse('account_settings_section', kwargs={'section': 'personal'}),
            {
                'settings_action': 'personal',
                'bio': 'Daily commuter via Campus Shuttle route A.',
                'phone_number': '9876543210',
                'email': 'settings_user@campus.edu',
            },
            follow=True
        )
        self.assertEqual(post_res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, 'Daily commuter via Campus Shuttle route A.')

    def test_privacy_and_visibility_destination(self):
        """Privacy destination groups controls by student questions and saves cleanly."""
        res = self.client.get(reverse('account_settings_section', kwargs={'section': 'privacy'}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        self.assertIn("Profile visibility", content)
        self.assertIn("Activity", content)
        self.assertIn("People & contact", content)
        self.assertIn("Hide profile from student search directory", content)
        self.assertIn("Show online status and last-seen activity", content)
        self.assertIn("Show my phone and email to confirmed friends", content)

        # Post privacy changes
        post_res = self.client.post(
            reverse('account_settings_section', kwargs={'section': 'privacy'}),
            {
                'settings_action': 'privacy',
                'hide_from_search': 'on',
                'show_online_status': 'on',
                'show_friends_publicly': 'on',
                'show_contact_on_profile': 'on',
            },
            follow=True
        )
        self.assertEqual(post_res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.hide_from_search)
        self.assertFalse(self.user.hide_online_status)
        self.assertTrue(self.user.show_friends_publicly)
        self.assertTrue(self.user.show_contact_on_profile)

    def test_security_and_signin_destination(self):
        """Security destination renders password, passkeys, 2FA, and requires correct current password."""
        res = self.client.get(reverse('account_settings_section', kwargs={'section': 'security'}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        self.assertIn("Password &amp; recovery", content)
        self.assertIn("Two-step verification (2FA)", content)
        self.assertIn("Passkeys &amp; Biometrics", content)
        self.assertIn("Active platform sessions", content)

        # Password change with wrong current password fails
        post_res = self.client.post(
            reverse('account_settings_section', kwargs={'section': 'security'}),
            {
                'settings_action': 'security',
                'security_action': 'change_password',
                'current_password': 'WrongPassword123!',
                'new_password': 'BrandNewPassword999!',
                'confirm_password': 'BrandNewPassword999!',
            },
            follow=True
        )
        self.assertEqual(post_res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('BrandNewPassword999!'))

        # Password change with correct current password and active grace succeeds
        session = self.client.session
        session['security_otp_verified_until'] = (timezone.now() + timedelta(minutes=10)).isoformat()
        session.save()

        post_res2 = self.client.post(
            reverse('account_settings_section', kwargs={'section': 'security'}),
            {
                'settings_action': 'security',
                'security_action': 'change_password',
                'current_password': 'SecurePassword123!',
                'new_password': 'BrandNewPassword999!',
                'confirm_password': 'BrandNewPassword999!',
            },
            follow=True
        )
        self.assertEqual(post_res2.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPassword999!'))

    def test_notifications_destination(self):
        """Notifications destination displays notification controls and browser permission guidance."""
        res = self.client.get(reverse('account_settings_section', kwargs={'section': 'notifications'}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        self.assertIn("Desktop &amp; Phone Notifications", content)
        self.assertIn("Browser site permissions", content)
        self.assertIn("window.toggleNexusNotifications", content)

    def test_profile_page_links_to_account_settings(self):
        """Profile page has prominent Account Settings link for the authenticated user."""
        res = self.client.get(reverse('profile', kwargs={'enrollment_number': self.user.enrollment_number}))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn(reverse('account_settings'), content)
        self.assertIn("Account Settings", content)
