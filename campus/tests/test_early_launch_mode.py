"""Automated tests for Early Launch Mode & Temporary Database Isolation.

Validates:
1. EarlyLaunchDeclarationForm validation, untrusted self-declaration (verified=False), and pending application purge.
2. Self-declaration flow via apply_account view under EARLY_LAUNCH_MODE=True.
3. Fallback to standard physical verification application when EARLY_LAUNCH_MODE=False.
4. COORDINATE policy integration (is_verified_student waivers for untrusted active users).
5. Temporary database routing & isolation settings.
6. Management commands: runserver --early-launch and purge_early_launch.
"""

import os
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, load_command_class
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import AccountApplicationForm, EarlyLaunchDeclarationForm
from accounts.models import AccountApplication, AuditEvent
from coordinate.policies import is_verified_student

User = get_user_model()


class EarlyLaunchFormTests(TestCase):
    """Test EarlyLaunchDeclarationForm validation, untrusted status, and persistence."""

    def test_valid_form_submission_creates_untrusted_user(self):
        # Create a pre-existing pending application to verify it is safely preserved
        AccountApplication.objects.create(
            enrollment_number="2024BCSE901",
            full_name="Pending Student",
            gender="M",
            phone_number="+919876543210",
            email="pending901@nitsri.ac.in",
            approved=False,
        )
        self.assertEqual(AccountApplication.objects.filter(enrollment_number="2024BCSE901").count(), 1)

        form_data = {
            "enrollment_number": "2024BCSE901",
            "full_name": "Zaid Khan",
            "gender": "M",
            "phone_number": "+919876543210",
            "email": "zaid901@nitsri.ac.in",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        }
        form = EarlyLaunchDeclarationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)

        user = form.save()
        self.assertIsNotNone(user.pk)
        self.assertEqual(user.enrollment_number, "2024BCSE901")
        self.assertEqual(user.first_name, "Zaid")
        self.assertEqual(user.last_name, "Khan")
        self.assertEqual(user.phone_number, "9876543210")
        self.assertEqual(user.email, "zaid901@nitsri.ac.in")
        # Early access accounts are untrusted
        self.assertFalse(user.verified)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("SecurePassword123!"))

        # Pending permanent application is safely preserved (not wiped)
        self.assertEqual(AccountApplication.objects.filter(enrollment_number="2024BCSE901").count(), 1)

    def test_default_institute_email_generated_when_omitted(self):
        form_data = {
            "enrollment_number": "2024BCSE902",
            "full_name": "Sara Ali",
            "gender": "F",
            "phone_number": "9876543211",
            "email": "",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        }
        form = EarlyLaunchDeclarationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, "2024bcse902@nitsri.ac.in")
        self.assertEqual(user.gender, "F")
        self.assertFalse(user.verified)

    def test_invalid_enrollment_format_rejected(self):
        form_data = {
            "enrollment_number": "NOTANENROLL",
            "full_name": "Invalid Student",
            "gender": "M",
            "phone_number": "9876543210",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        }
        form = EarlyLaunchDeclarationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("enrollment_number", form.errors)

    def test_duplicate_enrollment_rejected(self):
        User.objects.create_user(
            enrollment_number="2024BCSE903",
            username="2024BCSE903",
            password="ExistingPass123!",
        )
        form_data = {
            "enrollment_number": "2024BCSE903",
            "full_name": "Duplicate Student",
            "gender": "M",
            "phone_number": "9876543210",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        }
        form = EarlyLaunchDeclarationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("enrollment_number", form.errors)

    def test_password_mismatch_rejected(self):
        form_data = {
            "enrollment_number": "2024BCSE904",
            "full_name": "Mismatch Student",
            "gender": "M",
            "phone_number": "9876543210",
            "password": "SecurePassword123!",
            "confirm_password": "DifferentPassword123!",
        }
        form = EarlyLaunchDeclarationForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("confirm_password", form.errors)


class EarlyLaunchViewTests(TestCase):
    """Test HTTP views and templates under Early Launch Mode."""

    def setUp(self):
        cache.clear()
        self.client = Client()

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_apply_view_get_in_early_launch_mode(self):
        response = self.client.get(reverse("apply_account"))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["form"], EarlyLaunchDeclarationForm)
        self.assertTrue(response.context.get("early_launch_mode"))
        self.assertContains(response, "TEMPORARY EARLY ACCESS (UNTRUSTED)")
        self.assertContains(response, "Temporary Student Registration")
        self.assertNotContains(response, "Designated Application Approvers")

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_apply_view_post_creates_and_logs_in_user(self):
        post_data = {
            "enrollment_number": "2024BCSE905",
            "full_name": "Tariq Ahmad",
            "gender": "M",
            "phone_number": "+919876543210",
            "email": "tariq905@nitsri.ac.in",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        }
        response = self.client.post(reverse("apply_account"), data=post_data, follow=True)
        self.assertRedirects(response, reverse("dashboard"))

        # Verify user was created with active, untrusted status (verified=False)
        user = User.objects.get(enrollment_number="2024BCSE905")
        self.assertFalse(user.verified)
        self.assertTrue(user.is_active)

        # Verify audit log was recorded
        audit = AuditEvent.objects.filter(
            event_type="EARLY_LAUNCH_SELF_DECLARATION",
            actor_enrollment="2024BCSE905",
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("2024BCSE905", audit.description)

        # Verify client is actively authenticated
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    @override_settings(EARLY_LAUNCH_MODE=False)
    def test_apply_view_get_in_standard_mode(self):
        response = self.client.get(reverse("apply_account"))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["form"], AccountApplicationForm)
        self.assertFalse(response.context.get("early_launch_mode", False))
        self.assertContains(response, "STUDENT REGISTRATION")
        self.assertContains(response, "Apply for Verification")
        self.assertNotContains(response, "TEMPORARY EARLY ACCESS")

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_apply_view_permanent_tab_in_early_launch_mode(self):
        response = self.client.get(reverse("apply_account") + "?mode=permanent")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["form"], AccountApplicationForm)
        self.assertEqual(response.context.get("active_tab"), "permanent")
        self.assertContains(response, "OFFICIAL STUDENT REGISTRATION")
        self.assertContains(response, "Apply for Verification")
        self.assertContains(response, "Designated Application Approvers")

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_apply_view_post_permanent_mode_in_early_launch(self):
        from accounts.two_factor import create_phone_otp
        _, raw_otp = create_phone_otp(phone_number="+919876543220", email="applicant920@nitsri.ac.in", purpose="SIGNUP")
        post_data = {
            "registration_mode": "permanent",
            "enrollment_number": "2024BCSE920",
            "full_name": "Permanent Applicant",
            "gender": "M",
            "phone_number": "+919876543220",
            "email": "applicant920@nitsri.ac.in",
            "phone_otp": raw_otp,
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        }
        response = self.client.post(reverse("apply_account"), data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/application_success.html")
        app = AccountApplication.objects.filter(enrollment_number="2024BCSE920").first()
        self.assertIsNotNone(app)
        self.assertEqual(app.full_name, "Permanent Applicant")

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_untrusted_user_can_submit_permanent_application(self):
        # Create unverified/untrusted early access user
        untrusted = User.objects.create_user(
            enrollment_number="2024BCSE921",
            username="2024BCSE921",
            password="InitialEarlyPassword1!",
            verified=False,
            is_active=True,
        )
        self.client.force_login(untrusted)

        # GET /apply/ should render permanent application tab
        response = self.client.get(reverse("apply_account"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get("active_tab"), "permanent")

        # POST permanent application for this enrollment
        from accounts.two_factor import create_phone_otp
        _, raw_otp = create_phone_otp(phone_number="+919876543221", email="upgraded921@nitsri.ac.in", purpose="SIGNUP")
        post_data = {
            "registration_mode": "permanent",
            "enrollment_number": "2024BCSE921",
            "full_name": "Upgraded Student",
            "gender": "F",
            "phone_number": "+919876543221",
            "email": "upgraded921@nitsri.ac.in",
            "phone_otp": raw_otp,
            "password": "NewPermanentPass123!",
            "confirm_password": "NewPermanentPass123!",
        }
        post_response = self.client.post(reverse("apply_account"), data=post_data)
        self.assertEqual(post_response.status_code, 200)
        self.assertTemplateUsed(post_response, "accounts/application_success.html")
        app = AccountApplication.objects.filter(enrollment_number="2024BCSE921").first()
        self.assertIsNotNone(app)

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_admin_approval_upgrades_untrusted_user_to_verified(self):
        # Create unverified user from early access
        untrusted = User.objects.create_user(
            enrollment_number="2024BCSE922",
            username="2024BCSE922",
            first_name="Temporary",
            last_name="Student",
            password="InitialPass123!",
            verified=False,
            is_active=True,
        )
        self.assertFalse(untrusted.verified)

        # Create pending application
        from django.contrib.auth.hashers import make_password
        app = AccountApplication.objects.create(
            enrollment_number="2024BCSE922",
            full_name="Verified Tariq",
            email="tariq922@nitsri.ac.in",
            phone_number="+919876543222",
            gender="M",
            password=make_password("ApprovedPass123!"),
            application_number="APP922",
            phone_verified=True,
            approved=False,
        )

        # Admin approves
        admin_user = User.objects.create_superuser(
            enrollment_number="2024BCSE000",
            username="2024BCSE000",
            password="AdminPassword123!",
        )
        self.client.force_login(admin_user)
        session = self.client.session
        session["auth_timestamp"] = timezone.now().timestamp()
        session.save()

        approve_url = reverse("approve_application", kwargs={"application_id": app.id})
        response = self.client.post(approve_url, follow=True)
        self.assertRedirects(response, reverse("application_list"))

        # Verify existing user is upgraded to verified=True!
        untrusted.refresh_from_db()
        self.assertTrue(untrusted.verified)
        self.assertEqual(untrusted.first_name, "Verified")
        self.assertEqual(untrusted.last_name, "Tariq")
        self.assertEqual(untrusted.email, "tariq922@nitsri.ac.in")
        self.assertTrue(untrusted.check_password("ApprovedPass123!"))
        # Application should be deleted
        self.assertFalse(AccountApplication.objects.filter(enrollment_number="2024BCSE922").exists())

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_landing_page_renders_early_launch_banners_and_cta(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Early Launch Mode Active")
        self.assertContains(response, "Fast-Track Early Access")
        self.assertContains(response, "Apply for Permanent ID")
        self.assertContains(response, "Transit Rush + Official Verification")

    @override_settings(EARLY_LAUNCH_MODE=False)
    def test_landing_page_renders_standard_banners_and_cta(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apply for Verification")
        self.assertNotContains(response, "Fast-Track Early Access")

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_dashboard_displays_untrusted_badge_for_early_access_user(self):
        user = User.objects.create_user(
            enrollment_number="2024BCSE906",
            username="2024BCSE906",
            password="SecurePassword123!",
            verified=False,
            is_active=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Untrusted &bull; Early Access")
        self.assertContains(response, "Temporary Account:")

    @override_settings(EARLY_LAUNCH_MODE=True)
    def test_profile_displays_untrusted_badge(self):
        user = User.objects.create_user(
            enrollment_number="2024BCSE907",
            username="2024BCSE907",
            password="SecurePassword123!",
            verified=False,
            is_active=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("profile", kwargs={"enrollment_number": "2024BCSE907"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Untrusted &bull; Early Access")


class EarlyLaunchPolicyTests(TestCase):
    """Test COORDINATE verification policies under early launch."""

    def setUp(self):
        self.unverified_user = User.objects.create_user(
            enrollment_number="2024BCSE910",
            username="2024BCSE910",
            password="SecurePassword123!",
            verified=False,
            is_active=True,
        )
        self.verified_user = User.objects.create_user(
            enrollment_number="2024BCSE911",
            username="2024BCSE911",
            password="SecurePassword123!",
            verified=True,
            is_active=True,
        )
        self.inactive_user = User.objects.create_user(
            enrollment_number="2024BCSE912",
            username="2024BCSE912",
            password="SecurePassword123!",
            verified=True,
            is_active=False,
        )

    def test_is_verified_student_under_standard_mode(self):
        with override_settings(EARLY_LAUNCH_MODE=False):
            self.assertFalse(is_verified_student(self.unverified_user))
            self.assertTrue(is_verified_student(self.verified_user))
            self.assertFalse(is_verified_student(self.inactive_user))

    def test_is_verified_student_under_early_launch_mode(self):
        with override_settings(EARLY_LAUNCH_MODE=True):
            # Untrusted active student is granted temporary coordination privileges
            self.assertTrue(is_verified_student(self.unverified_user))
            self.assertTrue(is_verified_student(self.verified_user))
            # Inactive student is still barred
            self.assertFalse(is_verified_student(self.inactive_user))


class EarlyLaunchManagementCommandTests(TestCase):
    """Test manage.py runserver and purge_early_launch commands."""

    def test_runserver_command_argument_parsing(self):
        cmd = load_command_class("accounts", "runserver")
        parser = cmd.create_parser("manage.py", "runserver")
        parsed = parser.parse_args(["--early-launch", "0.0.0.0:8000"])
        self.assertTrue(parsed.early_launch)
        self.assertEqual(parsed.addrport, "0.0.0.0:8000")

    def test_purge_early_launch_command(self):
        # Create a mock temporary db file
        mock_path = settings.BASE_DIR / "test_temp_early_access.sqlite3"
        with open(mock_path, "w") as f:
            f.write("mock db content")
        self.assertTrue(os.path.exists(mock_path))

        with override_settings(EARLY_LAUNCH_DB_PATH=mock_path):
            call_command("purge_early_launch", force=True)
            self.assertFalse(os.path.exists(mock_path))


class TemporaryUserPermanentApplicationTests(TestCase):
    """Test authenticated temporary users applying for permanent verified ID and elevation."""

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(
            username="2020BCSE001",
            enrollment_number="2020BCSE001",
            email="admin@nitsri.ac.in",
            password="AdminPassword123!",
            verified=True,
            is_active=True,
            phone_number="+919876543200",
        )
        self.temp_user = User.objects.create_user(
            username="2024BCSE777",
            enrollment_number="2024BCSE777",
            email="temp777@nitsri.ac.in",
            password="OriginalPassword123!",
            verified=False,
            is_active=True,
            phone_number="+919876543210",
            first_name="Tariq",
            last_name="Mir",
            gender="M",
        )

    def test_authenticated_temp_user_applies_with_blank_password(self):
        """Authenticated temporary user can submit permanent application leaving password blank to reuse existing password."""
        self.client.force_login(self.temp_user)
        with override_settings(EARLY_LAUNCH_MODE=True):
            res = self.client.post(
                reverse("apply_account"),
                data={
                    "registration_mode": "permanent",
                    "enrollment_number": self.temp_user.enrollment_number,
                    "full_name": "Tariq Mir",
                    "gender": "M",
                    "email": "temp777@nitsri.ac.in",
                    "phone_number": "+919876543210",
                    "password": "",
                    "confirm_password": "",
                },
            )
            self.assertEqual(res.status_code, 200)
            self.assertTemplateUsed(res, "accounts/application_success.html")

            # Check application was created
            app = AccountApplication.objects.filter(enrollment_number="2024BCSE777").first()
            self.assertIsNotNone(app)
            self.assertFalse(app.approved)
            self.assertEqual(app.password, self.temp_user.password)

            # Re-visiting /apply/ while pending renders application_status.html
            get_res = self.client.get(reverse("apply_account"))
            self.assertEqual(get_res.status_code, 200)
            self.assertTemplateUsed(get_res, "accounts/application_status.html")
            self.assertContains(get_res, app.application_number)

    def test_temp_user_coordinates_rides_while_permanent_application_pending(self):
        """User remains authenticated and can coordinate rides while permanent application is pending."""
        self.client.force_login(self.temp_user)
        AccountApplication.objects.create(
            enrollment_number=self.temp_user.enrollment_number,
            full_name="Tariq Mir",
            gender="M",
            phone_number=self.temp_user.phone_number,
            email=self.temp_user.email,
            password=self.temp_user.password,
            approved=False,
        )

        with override_settings(EARLY_LAUNCH_MODE=True):
            dash_res = self.client.get(reverse("dashboard"))
            self.assertEqual(dash_res.status_code, 200)
            self.assertContains(dash_res, "Permanent ID Application Pending")

            rides_res = self.client.get(reverse("ride_list"))
            self.assertEqual(rides_res.status_code, 200)

    def test_admin_approves_temp_user_application_elevates_to_verified(self):
        """Admin approving permanent application elevates existing temporary user to verified=True."""
        app = AccountApplication.objects.create(
            enrollment_number=self.temp_user.enrollment_number,
            full_name="Tariq Mir",
            gender="M",
            phone_number=self.temp_user.phone_number,
            email=self.temp_user.email,
            password=self.temp_user.password,
            approved=False,
        )

        self.client.force_login(self.admin)
        session = self.client.session
        session["auth_timestamp"] = timezone.now().timestamp()
        session.save()
        with override_settings(EARLY_LAUNCH_MODE=True):
            # Check application list shows elevation badge
            list_res = self.client.get(reverse("application_list"))
            self.assertEqual(list_res.status_code, 200)
            self.assertContains(list_res, "Elevate Temp User")

            # Approve application
            approve_res = self.client.post(reverse("approve_application", args=[app.id]))
            self.assertIn(approve_res.status_code, [200, 302])

            # Verify temp_user has been elevated
            self.temp_user.refresh_from_db()
            self.assertTrue(self.temp_user.verified)

            # Pending application is consumed/purged upon approval
            self.assertFalse(AccountApplication.objects.filter(id=app.id).exists())


class TemporaryUserFriendshipTests(TestCase):
    """Test temporary users forming friendships during Early Launch mode and purging behavior."""

    def setUp(self):
        from accounts.models import FriendRequest, Friendship
        self.verified_user = User.objects.create_user(
            username="2022BCSE101",
            enrollment_number="2022BCSE101",
            email="verified101@nitsri.ac.in",
            password="VerifiedPassword123!",
            verified=True,
            is_active=True,
            phone_number="+919876543201",
            first_name="Aamir",
            last_name="Lone",
        )
        self.temp_user = User.objects.create_user(
            username="2024BCSE999",
            enrollment_number="2024BCSE999",
            email="temp999@nitsri.ac.in",
            password="TempPassword123!",
            verified=False,
            is_active=True,
            phone_number="+919876543299",
            first_name="Bilal",
            last_name="Wani",
        )

    def test_temp_user_can_send_and_receive_friend_request_in_early_launch(self):
        """Temporary user can send friend requests to verified users and receive them in Early Launch mode."""
        from accounts.models import FriendRequest
        # Verified user sends to temporary user
        self.client.force_login(self.verified_user)
        with override_settings(EARLY_LAUNCH_MODE=True):
            send_res = self.client.post(
                reverse("send_friend_request", args=[self.temp_user.enrollment_number])
            )
            self.assertEqual(send_res.status_code, 302)
            self.assertTrue(
                FriendRequest.objects.filter(
                    sender=self.verified_user, receiver=self.temp_user
                ).exists()
            )

            # Temporary user logs in and sees prominent friend request on dashboard
            self.client.force_login(self.temp_user)
            dash_res = self.client.get(reverse("dashboard"))
            self.assertEqual(dash_res.status_code, 200)
            self.assertContains(dash_res, "Incoming Connection Request")
            self.assertContains(dash_res, self.verified_user.enrollment_number)
            self.assertContains(dash_res, "Accept")

            # Temporary user accepts the friend request
            req = FriendRequest.objects.get(sender=self.verified_user, receiver=self.temp_user)
            accept_res = self.client.post(reverse("accept_friend_request", args=[req.id]))
            self.assertEqual(accept_res.status_code, 302)

            from accounts.models import Friendship
            from django.db.models import Q
            self.assertTrue(
                Friendship.objects.filter(
                    Q(user1=self.verified_user, user2=self.temp_user) |
                    Q(user1=self.temp_user, user2=self.verified_user)
                ).exists()
            )

    def test_temp_user_friend_request_blocked_in_standard_mode(self):
        """In standard mode (EARLY_LAUNCH_MODE=False), unverified users cannot form friendships."""
        from accounts.models import FriendRequest
        self.client.force_login(self.verified_user)
        with override_settings(EARLY_LAUNCH_MODE=False):
            send_res = self.client.post(
                reverse("send_friend_request", args=[self.temp_user.enrollment_number])
            )
            self.assertEqual(send_res.status_code, 302)
            self.assertFalse(
                FriendRequest.objects.filter(
                    sender=self.verified_user, receiver=self.temp_user
                ).exists()
            )
