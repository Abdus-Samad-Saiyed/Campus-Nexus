"""Comprehensive Test Suite for Platform Announcements and First-Login Proclamations.

Covers:
1. Announcement audience targeting (ALL, SET, FILTERED, INDIVIDUAL).
2. Deactivated and expired announcement exclusion.
3. First-login proclamation banner delivery on the dashboard.
4. User acknowledgment receipts and suppression of subsequent banners.
5. Administrative management (creation, listing, filtering, active toggle, deletion).
6. Security permissions and audit event logging.
"""

import datetime
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Announcement, AnnouncementReceipt, AuditEvent

User = get_user_model()


class AnnouncementTargetingTests(TestCase):
    """Test model-level audience evaluation and active targeting engine."""

    def setUp(self):
        self.staff_user = User.objects.create_user(
            username="STAFF001",
            enrollment_number="STAFF001",
            first_name="Staff",
            phone_number="9876543201",
            password="TestPassword123!",
            is_staff=True,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2021,
            gender="M",
        )
        self.student_cse_2024_f = User.objects.create_user(
            username="2024BCSE010",
            enrollment_number="2024BCSE010",
            first_name="Fatima",
            phone_number="9876543202",
            password="TestPassword123!",
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="F",
        )
        self.student_ece_2023_m = User.objects.create_user(
            username="2023BECE020",
            enrollment_number="2023BECE020",
            first_name="Tariq",
            phone_number="9876543203",
            password="TestPassword123!",
            is_staff=False,
            verified=True,
            branch="ECE",
            degree="B.Tech",
            joining_year=2023,
            gender="M",
        )
        self.unverified_student = User.objects.create_user(
            username="2024MCIV005",
            enrollment_number="2024MCIV005",
            first_name="Bilal",
            phone_number="9876543204",
            password="TestPassword123!",
            is_staff=False,
            verified=False,
            branch="CIVIL",
            degree="M.Tech",
            joining_year=2024,
            gender="M",
        )

    def test_universal_all_targeting(self):
        """Universal announcement targets all active users."""
        ann = Announcement.objects.create(
            title="Campus Holiday Notice",
            content="Campus closed on Monday.",
            target_type="ALL",
            priority="INFO",
        )
        self.assertTrue(ann.is_user_targeted(self.staff_user))
        self.assertTrue(ann.is_user_targeted(self.student_cse_2024_f))
        self.assertTrue(ann.is_user_targeted(self.student_ece_2023_m))
        self.assertTrue(ann.is_user_targeted(self.unverified_student))

    def test_preset_set_targeting(self):
        """Preset cohort targeting correctly separates students, staff, verified, and unverified."""
        # Students only
        ann_students = Announcement.objects.create(
            title="Student Council Elections",
            content="Voting opens tomorrow.",
            target_type="SET",
            target_set="STUDENTS",
        )
        self.assertFalse(ann_students.is_user_targeted(self.staff_user))
        self.assertTrue(ann_students.is_user_targeted(self.student_cse_2024_f))
        self.assertTrue(ann_students.is_user_targeted(self.unverified_student))

        # Staff only
        ann_staff = Announcement.objects.create(
            title="Faculty Senate Meeting",
            content="Meeting at 4 PM.",
            target_type="SET",
            target_set="STAFF",
        )
        self.assertTrue(ann_staff.is_user_targeted(self.staff_user))
        self.assertFalse(ann_staff.is_user_targeted(self.student_cse_2024_f))

        # Verified students only
        ann_verified = Announcement.objects.create(
            title="Verified Ride Pool Perks",
            content="Priority pooling unlocked.",
            target_type="SET",
            target_set="VERIFIED",
        )
        self.assertTrue(ann_verified.is_user_targeted(self.student_cse_2024_f))
        self.assertFalse(ann_verified.is_user_targeted(self.unverified_student))

        # Unverified students only
        ann_unverified = Announcement.objects.create(
            title="Complete ID Verification",
            content="Please submit student ID for verification.",
            target_type="SET",
            target_set="UNVERIFIED",
        )
        self.assertTrue(ann_unverified.is_user_targeted(self.unverified_student))
        self.assertFalse(ann_unverified.is_user_targeted(self.student_cse_2024_f))
        self.assertFalse(ann_unverified.is_user_targeted(self.staff_user))

    def test_filtered_audience_targeting(self):
        """Filtered targeting by branch, batch year, degree, and gender."""
        # CSE branch only
        ann_cse = Announcement.objects.create(
            title="CSE Hackathon",
            content="Annual Hackathon registration open.",
            target_type="FILTERED",
            target_branches="CSE, IT",
        )
        self.assertTrue(ann_cse.is_user_targeted(self.student_cse_2024_f))
        self.assertFalse(ann_cse.is_user_targeted(self.student_ece_2023_m))

        # Batch 2024 only
        ann_2024 = Announcement.objects.create(
            title="Freshers Orientation",
            content="Orientation session for batch 2024.",
            target_type="FILTERED",
            target_years="2024",
        )
        self.assertTrue(ann_2024.is_user_targeted(self.student_cse_2024_f))
        self.assertTrue(ann_2024.is_user_targeted(self.unverified_student))
        self.assertFalse(ann_2024.is_user_targeted(self.student_ece_2023_m))

        # Female students only
        ann_female = Announcement.objects.create(
            title="Women in Tech Workshop",
            content="Workshop in auditorium.",
            target_type="FILTERED",
            target_gender="F",
        )
        self.assertTrue(ann_female.is_user_targeted(self.student_cse_2024_f))
        self.assertFalse(ann_female.is_user_targeted(self.student_ece_2023_m))

        # M.Tech degree only
        ann_mtech = Announcement.objects.create(
            title="Postgraduate Thesis Submission",
            content="Thesis guidelines.",
            target_type="FILTERED",
            target_degrees="M.Tech",
        )
        self.assertTrue(ann_mtech.is_user_targeted(self.unverified_student))
        self.assertFalse(ann_mtech.is_user_targeted(self.student_cse_2024_f))

    def test_individual_user_targeting(self):
        """Individual user targeting matches only designated user accounts."""
        ann = Announcement.objects.create(
            title="Library Fine Warning",
            content="Overdue book return required.",
            target_type="INDIVIDUAL",
            priority="WARNING",
        )
        ann.target_users.add(self.student_ece_2023_m)

        self.assertTrue(ann.is_user_targeted(self.student_ece_2023_m))
        self.assertFalse(ann.is_user_targeted(self.student_cse_2024_f))
        self.assertFalse(ann.is_user_targeted(self.staff_user))

    def test_inactive_and_expired_announcements(self):
        """Inactive or past-expiration announcements are never targeted."""
        ann_inactive = Announcement.objects.create(
            title="Draft Notice",
            content="Not ready yet.",
            target_type="ALL",
            is_active=False,
        )
        self.assertFalse(ann_inactive.is_user_targeted(self.student_cse_2024_f))

        ann_expired = Announcement.objects.create(
            title="Past Event",
            content="Yesterday event.",
            target_type="ALL",
            expires_at=timezone.now() - datetime.timedelta(hours=1),
        )
        self.assertFalse(ann_expired.is_user_targeted(self.student_cse_2024_f))


class AnnouncementDashboardProclamationTests(TestCase):
    """Test first-login proclamation banner delivery, acknowledge flow, and priority feeds."""

    def setUp(self):
        self.client = Client()
        self.password = "TestPass123!"
        self.user = User.objects.create_user(
            username="2024BCSE099",
            enrollment_number="2024BCSE099",
            first_name="Suhail",
            phone_number="9876543299",
            password=self.password,
            is_staff=False,
            verified=True,
            branch="CSE",
            degree="B.Tech",
            joining_year=2024,
            gender="M",
        )
        self.client.login(username="2024BCSE099", password=self.password)

    def test_first_login_proclaiming_banner_and_acknowledgment(self):
        """On first login after an announcement is issued, a proclamation banner loads across the screen; acknowledging it suppresses the banner on future logins."""
        ann = Announcement.objects.create(
            title="Winter Campus Transport Schedule",
            content="All evening campus shuttles will now operate at 5:30 PM due to winter snowfall.",
            priority="CRITICAL",
            target_type="ALL",
            action_url="/coordinate/",
            action_label="View Shuttle Timetable",
        )

        # 1. First visit to dashboard after announcement is issued
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Winter Campus Transport Schedule")
        self.assertContains(res, "firstLoginAnnouncementBanner")
        self.assertContains(res, "URGENT OFFICIAL PROCLAMATION")
        self.assertEqual(res.context['proclaiming_announcement'], ann)
        self.assertIn(ann, res.context['active_announcements'])

        # 2. Acknowledge proclamation via POST endpoint (AJAX)
        ack_res = self.client.post(
            reverse('acknowledge_announcement', kwargs={'announcement_id': ann.id}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(ack_res.status_code, 200)
        self.assertTrue(ack_res.json().get('acknowledged'))

        # Verify receipt is recorded in database
        receipt = AnnouncementReceipt.objects.filter(announcement=ann, user=self.user).first()
        self.assertIsNotNone(receipt)
        self.assertTrue(receipt.dismissed)

        # 3. Next visit / login to dashboard
        res_after = self.client.get(reverse('dashboard'))
        self.assertEqual(res_after.status_code, 200)
        # Proclamation banner is no longer presented across the screen
        self.assertIsNone(res_after.context['proclaiming_announcement'])
        self.assertNotContains(res_after, '<div id="firstLoginAnnouncementBanner"')
        self.assertNotContains(res_after, "URGENT OFFICIAL PROCLAMATION")
        # But announcement remains visible in the active priority announcements feed!
        self.assertIn(ann, res_after.context['active_announcements'])
        self.assertContains(res_after, "Priority Campus Announcements")


class AdminAnnouncementOperationsTests(TestCase):
    """Test administrative announcement management views, permissions, and audit trails."""

    def setUp(self):
        self.client = Client()
        self.password = "SecureAdmin123!"
        self.admin_user = User.objects.create_superuser(
            username="ADMIN001",
            enrollment_number="ADMIN001",
            first_name="Admin",
            phone_number="9999000001",
            password=self.password,
            is_staff=True,
            is_superuser=True,
            verified=True,
        )
        self.student = User.objects.create_user(
            username="2024BCSE111",
            enrollment_number="2024BCSE111",
            first_name="Student",
            phone_number="9999000002",
            password=self.password,
            is_staff=False,
            verified=True,
        )

    def test_access_control(self):
        """Regular students cannot access admin announcement views; staff/superusers can."""
        self.client.login(username="2024BCSE111", password=self.password)
        res_student = self.client.get(reverse('admin_announcements'))
        # user_passes_test redirects unauthorized users to login
        self.assertIn(res_student.status_code, (302, 403))

        self.client.login(username="ADMIN001", password=self.password)
        res_admin = self.client.get(reverse('admin_announcements'))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Platform Announcements")

    def test_admin_create_announcement_all(self):
        """Admin can issue an announcement to all users with audit event logging."""
        self.client.login(username="ADMIN001", password=self.password)
        post_data = {
            'title': 'Campus Power Maintenance',
            'content': 'Main server room backup testing from 2 AM to 4 AM.',
            'priority': 'WARNING',
            'target_type': 'ALL',
            'action_url': '/status/',
            'action_label': 'View System Status',
        }
        res = self.client.post(reverse('admin_create_announcement'), data=post_data)
        self.assertRedirects(res, reverse('admin_announcements'))

        ann = Announcement.objects.filter(title='Campus Power Maintenance').first()
        self.assertIsNotNone(ann)
        self.assertEqual(ann.priority, 'WARNING')
        self.assertEqual(ann.target_type, 'ALL')
        self.assertEqual(ann.created_by, self.admin_user)

        # Audit event verification
        audit = AuditEvent.objects.filter(event_type='ANNOUNCEMENT_CREATED', target_id=str(ann.id)).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor_enrollment, 'ADMIN001')

    def test_admin_create_announcement_individual(self):
        """Admin can issue an announcement targeted to specific enrollment numbers."""
        self.client.login(username="ADMIN001", password=self.password)
        post_data = {
            'title': 'Identity Verification Urgent Notice',
            'content': 'Please verify your ID card at the academic block.',
            'priority': 'CRITICAL',
            'target_type': 'INDIVIDUAL',
            'target_users': f"{self.student.enrollment_number}, 99999999",
        }
        res = self.client.post(reverse('admin_create_announcement'), data=post_data)
        self.assertRedirects(res, reverse('admin_announcements'))

        ann = Announcement.objects.filter(title='Identity Verification Urgent Notice').first()
        self.assertIsNotNone(ann)
        self.assertEqual(ann.target_type, 'INDIVIDUAL')
        self.assertIn(self.student, ann.target_users.all())

    def test_admin_toggle_active_status(self):
        """Admin can toggle an announcement active and inactive."""
        self.client.login(username="ADMIN001", password=self.password)
        ann = Announcement.objects.create(
            title="Toggle Test Notice",
            content="Testing active toggle.",
            is_active=True,
        )
        toggle_url = reverse('admin_toggle_announcement', kwargs={'announcement_id': ann.id})

        # Deactivate
        self.client.post(toggle_url)
        ann.refresh_from_db()
        self.assertFalse(ann.is_active)

        # Reactivate
        self.client.post(toggle_url)
        ann.refresh_from_db()
        self.assertTrue(ann.is_active)

    def test_admin_delete_announcement(self):
        """Admin can delete an announcement with audit trail logging."""
        self.client.login(username="ADMIN001", password=self.password)
        ann = Announcement.objects.create(
            title="Notice to be deleted",
            content="Testing deletion.",
            is_active=True,
        )
        ann_id = ann.id
        del_url = reverse('admin_delete_announcement', kwargs={'announcement_id': ann_id})

        res = self.client.post(del_url)
        self.assertRedirects(res, reverse('admin_announcements'))
        self.assertFalse(Announcement.objects.filter(id=ann_id).exists())

        audit = AuditEvent.objects.filter(event_type='ANNOUNCEMENT_DELETED', target_id=str(ann_id)).first()
        self.assertIsNotNone(audit)
