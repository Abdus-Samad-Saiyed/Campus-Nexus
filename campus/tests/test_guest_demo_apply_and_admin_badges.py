from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from accounts.models import AccountApplication
from accounts.demo import ensure_guest_demo_user

User = get_user_model()


class GuestDemoApplyAndAdminBadgesTests(TestCase):
    databases = {'default', 'guest_demo'}

    def setUp(self):
        # Create superuser
        self.admin_user = User.objects.create_superuser(
            enrollment_number="2024BCSE001",
            username="2024BCSE001",
            first_name="Admin",
            last_name="Samad",
            email="admin@nitsri.ac.in",
            password="AdminPassword123!",
            verified=True,
        )

        # Create regular student
        self.student = User.objects.create_user(
            enrollment_number="2024BCSE002",
            username="2024BCSE002",
            first_name="Raza",
            last_name="Khan",
            email="raza@nitsri.ac.in",
            password="StudentPassword123!",
            verified=True,
        )

        ensure_guest_demo_user("default")
        ensure_guest_demo_user("guest_demo")

    def test_admin_pending_applications_counter_in_context_and_navbar(self):
        # Initially 0 pending applications
        client = Client()
        client.force_login(self.admin_user)
        res = client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["pending_applications_count"], 0)

        # Create a pending application
        app = AccountApplication.objects.create(
            enrollment_number="2024BCSE099",
            full_name="New Applicant",
            email="new@nitsri.ac.in",
            phone_number="9876543210",
            gender="M",
            password="HashedPassword123",
            application_number="TESTAPP1",
            approved=False,
            phone_verified=True,
        )

        # Now admin sees counter == 1 in navbar and dropdown
        res2 = client.get(reverse("dashboard"))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.context["pending_applications_count"], 1)
        self.assertContains(res2, 'title="Pending Student Applications Queue (1 waiting review)"')
        self.assertContains(res2, 'Pending student IDs')
        self.assertContains(res2, 'Pending Applications')

        # Regular non-staff student does not receive pending_applications_count > 0 or admin queue pill
        client.force_login(self.student)
        res_student = client.get(reverse("dashboard"))
        self.assertEqual(res_student.status_code, 200)
        self.assertEqual(res_student.context["pending_applications_count"], 0)
        self.assertNotContains(res_student, 'Pending Student Applications Queue')

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_guest_demo_user_prompted_and_not_locked_to_guest_demo(self):
        client = Client()
        client.get(reverse("enter_guest_demo"))

        res = client.get(reverse("apply_account"))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context["is_guest_demo"])

        # Must display prompt banner
        self.assertContains(res, "You are currently exploring Campus Nexus in <strong>Guest Demo Mode</strong>")
        self.assertContains(res, "Quick-Bypass Account (Skip Verification For Now)")
        self.assertContains(res, "Verified Permanent Account (Physical Verification)")

        # Enrollment field must NOT be locked or set to GUEST_DEMO
        self.assertNotContains(res, 'value="GUEST_DEMO" readonly disabled')
        self.assertNotContains(res, "Bound to your active temporary account (GUEST_DEMO)")

    def test_guest_demo_enrollment_rejected_in_application_form(self):
        client = Client()
        client.get(reverse("enter_guest_demo"))

        # Attempt to submit an application using GUEST_DEMO
        post_data = {
            "registration_mode": "permanent",
            "enrollment_number": "GUEST_DEMO",
            "full_name": "Demo Impostor",
            "email": "impostor@nitsri.ac.in",
            "phone_number": "9876543210",
            "gender": "M",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "phone_otp": "123456",
        }
        res = client.post(reverse("apply_account"), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Guest demo accounts cannot apply for permanent status")

        # Verify no AccountApplication was created for GUEST_DEMO
        self.assertFalse(AccountApplication.objects.filter(enrollment_number="GUEST_DEMO").exists())

    def test_model_level_validation_blocks_guest_demo_application(self):
        app = AccountApplication(
            enrollment_number="GUEST_DEMO",
            full_name="Direct Model Try",
            email="direct@campus.internal",
            phone_number="0000000000",
            gender="M",
            password="HashedPassword123",
            application_number="DIRECTDEMO",
            phone_verified=True,
            approved=False,
        )
        with self.assertRaises(ValueError):
            app.save()

    def test_approve_application_safeguard_blocks_guest_demo(self):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO accounts_accountapplication "
                "(enrollment_number, full_name, email, phone_number, gender, password, application_number, phone_verified, approved, created_at) "
                "VALUES ('GUEST_DEMO', 'Corrupt Demo', 'c@c.com', '1234567890', 'M', 'pw', 'CORRUPT1', TRUE, FALSE, CURRENT_TIMESTAMP)"
            )

        app_id = AccountApplication.objects.get(enrollment_number="GUEST_DEMO").id

        client = Client()
        client.force_login(self.admin_user)

        # Elevate session grace
        session = client.session
        from django.utils import timezone
        session['security_otp_verified_until'] = (timezone.now() + timezone.timedelta(minutes=10)).isoformat()
        session.save()

        res = client.post(reverse("approve_application", args=[app_id]))
        self.assertRedirects(res, reverse("application_list"))

        # GUEST_DEMO application was purged and GUEST_DEMO user was NOT verified or altered
        self.assertFalse(AccountApplication.objects.filter(enrollment_number="GUEST_DEMO").exists())
        guest_user = User.objects.get(enrollment_number="GUEST_DEMO")
        self.assertFalse(guest_user.verified)
        self.assertEqual(guest_user.first_name, "Guest")
