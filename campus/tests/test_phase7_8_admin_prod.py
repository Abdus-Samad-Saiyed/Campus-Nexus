"""Comprehensive Test Suite for Phase 7 & Phase 8.

Phase 7:
- Role-protected Operational Dashboard (/operations/)
- Active session metrics distinguishing session count vs distinct active users
- CSV formula-injection sanitization (CWE-1236 protection)
- Role-gated data exports (Rides, Users, Applications) with audit event creation
- Ethical complaint review status tracking

Phase 8:
- System healthcheck endpoint (/health/)
- Production reverse-proxy and security configuration verification
"""

import csv
import datetime
import io
import json
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountApplication, AuditEvent, log_audit_event
from campus.operations import sanitize_csv_cell
from coordinate.models import RideParticipant, RideRequest, RideReview

User = get_user_model()


class Phase7OperationalAdminTests(TestCase):
    """Phase 7 verification: Operational dashboard, session semantics, exports, and formula injection."""

    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            username="2024BCSE001",
            enrollment_number="2024BCSE001",
            first_name="Abdus",
            gender="M",
            phone_number="9999999901",
            password="AdminSecretPassword123!",
            is_active=True,
            verified=True,
        )
        self.regular_user = User.objects.create_user(
            username="2024BCSE002",
            enrollment_number="2024BCSE002",
            first_name="Raza",
            gender="M",
            phone_number="9999999902",
            password="UserSecretPassword123!",
            is_active=True,
            verified=True,
        )

    def test_formula_injection_sanitization(self):
        """Formula characters (=, +, -, @, \\t, \\r) are disarmed with a prepended single quote."""
        dangerous_payloads = [
            ("=cmd|' /C calc'!A0", "'=cmd|' /C calc'!A0"),
            ("+1+1", "'+1+1"),
            ("-2+3", "'-2+3"),
            ("@SUM(1, 2)", "'@SUM(1, 2)"),
            ("\tTAB_INJECT", "'\tTAB_INJECT"),
            ("\rCR_INJECT", "'\rCR_INJECT"),
        ]
        for payload, expected in dangerous_payloads:
            sanitized = sanitize_csv_cell(payload)
            self.assertEqual(sanitized, expected)

        # Benign inputs remain unchanged
        self.assertEqual(sanitize_csv_cell("NIT Srinagar"), "NIT Srinagar")
        self.assertEqual(sanitize_csv_cell(12345), "12345")
        self.assertEqual(sanitize_csv_cell(""), "")
        self.assertEqual(sanitize_csv_cell(None), "")

    def test_operational_dashboard_role_protection(self):
        """Unauthenticated and regular users are denied access to the operations dashboard."""
        # 1. Unauthenticated -> 302 to login
        res_anon = self.client.get(reverse("operational_dashboard"))
        self.assertEqual(res_anon.status_code, 302)

        # 2. Regular user -> 302 (rejected by user_passes_test)
        self.client.force_login(self.regular_user)
        res_regular = self.client.get(reverse("operational_dashboard"))
        self.assertEqual(res_regular.status_code, 302)

        # 3. Superuser -> 200 OK
        self.client.force_login(self.superuser)
        res_admin = self.client.get(reverse("operational_dashboard"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Campus Nexus")
        self.assertContains(res_admin, "Operations")
        self.assertIn("verified_users", res_admin.context)
        self.assertIn("active_sessions_count", res_admin.context)
        self.assertIn("unique_active_users_count", res_admin.context)

    def test_session_semantics_distinction(self):
        """Active session count distinguishes total sessions from unique active users."""
        self.client.force_login(self.superuser)

        # Create 2 distinct sessions for regular_user (e.g. mobile + desktop)
        now = timezone.now()
        s1 = Session.objects.create(
            session_key="session_test_key_1",
            session_data=self.client.session.encode({"_auth_user_id": str(self.regular_user.pk)}),
            expire_date=now + datetime.timedelta(days=1),
        )
        s2 = Session.objects.create(
            session_key="session_test_key_2",
            session_data=self.client.session.encode({"_auth_user_id": str(self.regular_user.pk)}),
            expire_date=now + datetime.timedelta(days=1),
        )

        res = self.client.get(reverse("operational_dashboard"))
        self.assertEqual(res.status_code, 200)
        # Session count should reflect multiple sessions, but unique user count dedupes
        self.assertGreaterEqual(res.context["active_sessions_count"], 2)
        self.assertGreaterEqual(res.context["unique_active_users_count"], 1)

    def test_audit_event_logging(self):
        """Audit events are logged immutably without sensitive credentials."""
        evt = log_audit_event(
            event_type="APP_APPROVED",
            actor_enrollment=self.superuser.enrollment_number,
            description="Approved application for test applicant",
            target_id="APP-101",
        )
        self.assertIsNotNone(evt)
        self.assertEqual(evt.event_type, "APP_APPROVED")
        self.assertEqual(evt.actor_enrollment, "2024BCSE001")
        self.assertTrue(AuditEvent.objects.filter(pk=evt.pk).exists())

    def test_export_rides_csv_protected_and_sanitized(self):
        """Rides export is role-protected, sanitized, and creates an audit record."""
        # Create a ride with potentially malicious route/location text
        ride = RideRequest.objects.create(
            creator=self.regular_user,
            route="CUSTOM",
            custom_from_location="=cmd|' /C calc'!A0",
            custom_to_location="Srinagar Airport",
            departure_time=timezone.now() + datetime.timedelta(days=2),
            total_spots=3,
            visibility="MIXED",
            status="OPEN",
        )

        # Regular user denied
        self.client.force_login(self.regular_user)
        res_denied = self.client.get(reverse("export_rides_csv"))
        self.assertEqual(res_denied.status_code, 302)

        # Superuser allowed
        self.client.force_login(self.superuser)
        res = self.client.get(reverse("export_rides_csv"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", res["Content-Disposition"])

        content = res.content.decode("utf-8")
        # Ensure formula character was disarmed with single quote
        self.assertIn("=cmd|' /C calc'!A0", content)
        self.assertIn("'=cmd|' /C calc'!A0", content)

        # Ensure audit log was written
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="EXPORT_GENERATED",
                actor_enrollment=self.superuser.enrollment_number,
            ).exists()
        )

    def test_export_users_csv_omits_sensitive_data(self):
        """Users export strictly excludes passwords and internal keys."""
        self.client.force_login(self.superuser)
        res = self.client.get(reverse("export_users_csv"))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")
        self.assertNotIn("password", content.lower())
        self.assertNotIn("SecretPassword", content)
        self.assertIn("2024BCSE001", content)

    def test_manage_superusers_role_protection(self):
        """Only active superusers can access /operations/superusers/."""
        # Anonymous -> 302
        res_anon = self.client.get(reverse("manage_superusers"))
        self.assertEqual(res_anon.status_code, 302)

        # Regular user -> 302
        self.client.force_login(self.regular_user)
        res_reg = self.client.get(reverse("manage_superusers"))
        self.assertEqual(res_reg.status_code, 302)

        # Staff non-superuser -> 302
        staff_user = User.objects.create_user(
            username="2024BCSE099",
            enrollment_number="2024BCSE099",
            first_name="StaffOnly",
            gender="M",
            phone_number="9999999999",
            password="StaffPassword123!",
            is_staff=True,
            is_superuser=False,
        )
        self.client.force_login(staff_user)
        res_staff = self.client.get(reverse("manage_superusers"))
        self.assertEqual(res_staff.status_code, 302)

        # Superuser -> 200
        self.client.force_login(self.superuser)
        res_admin = self.client.get(reverse("manage_superusers"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Superuser")
        self.assertContains(res_admin, "Access Control")

    def test_promote_user_to_superuser(self):
        """Superuser can promote a verified student to superuser, logging an audit event."""
        self.client.force_login(self.superuser)
        self.assertFalse(self.regular_user.is_superuser)

        res = self.client.post(reverse("manage_superusers"), {
            "action": "promote",
            "enrollment_number": self.regular_user.enrollment_number,
        })
        self.assertEqual(res.status_code, 302)

        self.regular_user.refresh_from_db()
        self.assertTrue(self.regular_user.is_superuser)
        self.assertTrue(self.regular_user.is_staff)

        # Audit event created
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="SUPERUSER_PROMOTED",
                actor_enrollment=self.superuser.enrollment_number,
                target_id=self.regular_user.enrollment_number,
            ).exists()
        )

    def test_revoke_superuser_lockout_protection(self):
        """System prevents revoking the sole active superuser; allows revocation if another exists."""
        self.client.force_login(self.superuser)
        self.assertEqual(User.objects.filter(is_superuser=True, is_active=True).count(), 1)

        # 1. Attempting to revoke the sole superuser is blocked
        res_blocked = self.client.post(reverse("manage_superusers"), {
            "action": "revoke",
            "enrollment_number": self.superuser.enrollment_number,
        })
        self.assertEqual(res_blocked.status_code, 302)
        self.superuser.refresh_from_db()
        self.assertTrue(self.superuser.is_superuser)

        # 2. Promote another user first so 2 superusers exist
        second_super = User.objects.create_superuser(
            username="2024BCSE008",
            enrollment_number="2024BCSE008",
            first_name="SecondAdmin",
            gender="F",
            phone_number="9999999908",
            password="SecondAdminPassword123!",
            is_active=True,
            verified=True,
        )
        self.assertEqual(User.objects.filter(is_superuser=True, is_active=True).count(), 2)

        # 3. Now revoking second_super succeeds
        res_ok = self.client.post(reverse("manage_superusers"), {
            "action": "revoke",
            "enrollment_number": second_super.enrollment_number,
        })
        self.assertEqual(res_ok.status_code, 302)
        second_super.refresh_from_db()
        self.assertFalse(second_super.is_superuser)

        # Audit event created
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="SUPERUSER_REVOKED",
                actor_enrollment=self.superuser.enrollment_number,
                target_id=second_super.enrollment_number,
            ).exists()
        )


    def test_admin_delete_account_blocks_targeting_superuser(self):
        """admin_delete_account strictly forbids targeting other superuser accounts."""
        second_super = User.objects.create_superuser(
            username="2024BCSE099",
            enrollment_number="2024BCSE099",
            first_name="SecondAdmin",
            gender="M",
            phone_number="9999999999",
            password="AdminSecretPassword123!",
            is_active=True,
            verified=True,
        )
        self.client.force_login(self.superuser)
        res = self.client.post(reverse("admin_delete_account", args=[second_super.id]))
        self.assertRedirects(res, reverse("operational_dashboard"))
        second_super.refresh_from_db()
        self.assertTrue(second_super.is_active)
        self.assertFalse(second_super.is_deleted)

    def test_admin_operations_require_post(self):
        """GET requests to mutating operational endpoints return HTTP 405 Method Not Allowed."""
        self.client.force_login(self.superuser)
        res_phone = self.client.get(reverse("admin_update_phone", args=[self.regular_user.id]))
        self.assertEqual(res_phone.status_code, 405)

        res_delete = self.client.get(reverse("admin_delete_account", args=[self.regular_user.id]))
        self.assertEqual(res_delete.status_code, 405)


class Phase8ProductionReadinessTests(TestCase):
    """Phase 8 verification: Health checks, configuration validation."""

    def setUp(self):
        self.client = Client()

    def test_system_health_check_endpoint(self):
        """Health check returns 200 with database status and timestamp."""
        res = self.client.get(reverse("health_check"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["database"], "connected")
        self.assertIn("timestamp", data)
        self.assertIn("version", data)

    def test_health_check_html_rendering(self):
        """GET /health/?format=html renders the dashboard HTML view."""
        response = self.client.get(reverse('health_check') + '?format=html')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response['Content-Type'])
        self.assertContains(response, 'System Health')
        self.assertContains(response, 'ALL SYSTEMS OPERATIONAL')

    def test_health_check_database_failure_returns_503(self):
        """Simulated database connection failure triggers 503 Service Unavailable."""
        from unittest.mock import patch
        with patch('django.db.connection.cursor', side_effect=Exception("Database connection timeout")):
            response = self.client.get(reverse('health_check'), HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 503)
            data = response.json()
            self.assertEqual(data.get('status'), 'unhealthy')
            self.assertEqual(data.get('database'), 'disconnected')
            self.assertIn('error', data)

    def test_production_settings_validation(self):
        """Verify production configuration settings and proxy headers."""
        import importlib
        import os
        import sys
        from unittest import mock

        prod_base_env = {
            "DJANGO_ALLOWED_HOSTS": "campus.nitsri.ac.in",
            "DATABASE_URL": "postgres://user:pass@localhost:5432/campus_db",
            "REDIS_URL": "redis://localhost:6379/0",
            "EMAIL_HOST_PASSWORD": "dummy-password",
            "VAPID_PUBLIC_KEY": "mock_prod_vapid_public_key_1234567890",
            "VAPID_PRIVATE_KEY": "mock_prod_vapid_private_key_1234567890",
        }

        orig_secrets = sys.modules.pop("campus.settings.secrets_local", None)
        try:
            # 1. With dev default key, prod settings must refuse to start
            import campus.settings.base as base_settings
            with mock.patch.dict(os.environ, {**prod_base_env, "DJANGO_SECRET_KEY": "insecure-dev-key-change-me"}):
                importlib.reload(base_settings)
                if "campus.settings.prod" in sys.modules:
                    del sys.modules["campus.settings.prod"]
                with self.assertRaises(ValueError):
                    import campus.settings.prod  # noqa: F401

            # 2. With a strong production secret key, prod settings imports successfully
            with mock.patch.dict(os.environ, {**prod_base_env, "DJANGO_SECRET_KEY": "strong-production-key-for-testing-12345-random-safe"}):
                import campus.settings.base as base_settings
                importlib.reload(base_settings)
                if "campus.settings.prod" in sys.modules:
                    del sys.modules["campus.settings.prod"]
                import campus.settings.prod as prod_settings
                self.assertEqual(prod_settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))
                self.assertTrue(prod_settings.SESSION_COOKIE_SECURE)
                self.assertTrue(prod_settings.CSRF_COOKIE_SECURE)
                self.assertFalse(prod_settings.DEBUG)

                if "campus.settings.prod" in sys.modules:
                    del sys.modules["campus.settings.prod"]
            importlib.reload(base_settings)
        finally:
            if orig_secrets is not None:
                sys.modules["campus.settings.secrets_local"] = orig_secrets
