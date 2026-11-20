"""Test Suite for About page, Contributors page, Feedback/Bug reporting,
and In-Ride Misconduct Reporting with Institute Escalation and Chat Logs.
"""

import datetime
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditEvent, PlatformFeedback
from coordinate.models import (
    RideIncidentReport,
    RideMessage,
    RideParticipant,
    RideRequest,
)

User = get_user_model()


class AboutContributorsFeedbackTests(TestCase):
    """Verify About, Contributors, and Platform Feedback / Bug Reporting."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="2024BCSE002",
            enrollment_number="2024BCSE002",
            first_name="Raza",
            last_name="Khan",
            gender="M",
            phone_number="9999999902",
            password="TestPassword123!",
            is_active=True,
            verified=True,
        )

    def test_about_page_rendering(self):
        """About page renders openly with mission, tech stack, and invitation."""
        res = self.client.get(reverse("about"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Campus Nexus")
        self.assertContains(res, "NIT Srinagar")
        self.assertContains(res, "Have an Idea or Utility")
        self.assertContains(res, "Architecture Stack")

    def test_contributors_page_rendering(self):
        """Contributors page renders with lead creator, repo, LinkedIn, and open source guide."""
        res = self.client.get(reverse("contributors"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Abdus Samad Saiyed")
        self.assertContains(res, "Lead Creator & Architect")
        self.assertContains(res, "Project Repository")
        self.assertContains(res, "LinkedIn")
        self.assertContains(res, "How to Contribute")

    def test_feedback_page_get(self):
        """Feedback / bug report form renders correctly."""
        res = self.client.get(reverse("feedback"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Submission Category")
        self.assertContains(res, "Bug Report")

    def test_feedback_submission_authenticated(self):
        """Authenticated user submits a bug report; verifies model and audit log."""
        self.client.force_login(self.user)
        res = self.client.post(reverse("feedback"), {
            "feedback_type": "BUG",
            "title": "Date picker layout glitch",
            "description": "On mobile screen width 360px, date strip wraps unexpectedly.",
            "submitter_name": "Raza Khan",
            "submitter_contact": "9999999902",
        })
        self.assertEqual(res.status_code, 302)

        fb = PlatformFeedback.objects.filter(title="Date picker layout glitch").first()
        self.assertIsNotNone(fb)
        self.assertEqual(fb.user, self.user)
        self.assertEqual(fb.feedback_type, "BUG")
        self.assertEqual(fb.status, "OPEN")

        # Audit event created
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="FEEDBACK_SUBMITTED",
                actor_enrollment="2024BCSE002",
            ).exists()
        )

    def test_feedback_submission_guest(self):
        """Guest user submits feedback; verifies model creation with Guest actor."""
        res = self.client.post(reverse("report_bug"), {
            "feedback_type": "PROPOSAL",
            "title": "Hostel Print Queue Module",
            "description": "Digitize print orders directly to campus stationery shops.",
            "submitter_name": "Prospective Student",
            "submitter_contact": "guest@nitsri.ac.in",
        })
        self.assertEqual(res.status_code, 302)

        fb = PlatformFeedback.objects.filter(title="Hostel Print Queue Module").first()
        self.assertIsNotNone(fb)
        self.assertIsNone(fb.user)
        self.assertEqual(fb.feedback_type, "PROPOSAL")

    def test_feedback_submit_and_report_bug_pages_render_directly(self):
        """Dedicated /feedback/submit/ and /report-bug/ routes render the post form with the expected default category."""
        res_submit = self.client.get(reverse("feedback_submit"))
        self.assertEqual(res_submit.status_code, 200)
        self.assertContains(res_submit, 'id="feedbackForm"')
        self.assertContains(res_submit, 'value="GENERAL" selected')
        self.assertContains(res_submit, reverse("feedback"))

        res_proposal = self.client.get(reverse("feedback_submit") + "?type=PROPOSAL")
        self.assertEqual(res_proposal.status_code, 200)
        self.assertContains(res_proposal, 'value="PROPOSAL" selected')

        res_bug = self.client.get(reverse("report_bug"))
        self.assertEqual(res_bug.status_code, 200)
        self.assertContains(res_bug, 'id="feedbackForm"')
        self.assertContains(res_bug, 'value="BUG" selected')
        self.assertContains(res_bug, "Report a Bug")

    def test_feedback_submit_post_creates_entry_and_redirects(self):
        """POST to /feedback/submit/ creates PlatformFeedback, logs audit event, and redirects to tracker."""
        self.client.force_login(self.user)
        res = self.client.post(reverse("feedback_submit"), {
            "feedback_type": "GENERAL",
            "title": "Navbar anchor clearance feedback",
            "description": "Anchors now land cleanly below the sticky navbar on mobile.",
            "submitter_name": "Raza Khan",
            "submitter_contact": "2024BCSE002",
        })
        self.assertRedirects(res, reverse("feedback"))

        fb = PlatformFeedback.objects.filter(title="Navbar anchor clearance feedback").first()
        self.assertIsNotNone(fb)
        self.assertEqual(fb.user, self.user)
        self.assertEqual(fb.feedback_type, "GENERAL")

    def test_tracker_and_info_pages_link_to_direct_post_options(self):
        """Feedback tracker, landing, about, contact, and footer link directly to post bug/feedback routes."""
        tracker_res = self.client.get(reverse("feedback"))
        self.assertEqual(tracker_res.status_code, 200)
        self.assertContains(tracker_res, f'href="{reverse("report_bug")}"')
        self.assertContains(tracker_res, f'href="{reverse("feedback_submit")}?type=GENERAL"')

        for url_name in ("landing", "about", "contact"):
            res = self.client.get(reverse(url_name))
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, reverse("feedback_submit"))
            self.assertContains(res, reverse("report_bug"))

    def test_bug_report_attributed_to_other_student_when_details_provided(self):
        """When user A reports a bug for user B, it attributes to B and sets reported_by to A."""
        student_b = User.objects.create_user(
            username="2024BCSE074",
            enrollment_number="2024BCSE074",
            first_name="Gaurav",
            last_name="Mishra",
            gender="M",
            phone_number="9876543299",
            password="OtherPassword123!",
            is_active=True,
            verified=True,
        )

        self.client.force_login(self.user)
        res = self.client.post(reverse("report_bug"), {
            "feedback_type": "BUG",
            "title": "Passkey registration biometric timeout",
            "description": "On Windows Hello enrollment, prompt fails with WebAuthn timeout.",
            "submitter_name": "Gaurav Mishra",
            "submitter_contact": "2024BCSE074",
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        fb = PlatformFeedback.objects.filter(title="Passkey registration biometric timeout").first()
        self.assertIsNotNone(fb)
        self.assertEqual(fb.user, student_b)
        self.assertEqual(fb.reported_by, self.user)
        self.assertEqual(fb.submitter_name, "Gaurav Mishra")
        self.assertEqual(fb.submitter_contact, "2024BCSE074")

        # Submitter A viewing their own reports should NOT see this bug
        res_a = self.client.get(reverse("feedback") + "?view=mine")
        self.assertEqual(res_a.status_code, 200)
        self.assertNotIn("Passkey registration biometric timeout", res_a.content.decode("utf-8"))

        # Student B viewing their own reports SHOULD see this bug
        self.client.force_login(student_b)
        res_b = self.client.get(reverse("feedback") + "?view=mine")
        self.assertEqual(res_b.status_code, 200)
        self.assertIn("Passkey registration biometric timeout", res_b.content.decode("utf-8"))

    def test_bug_report_vetoes_mismatched_name_to_name_attached_to_enrollment(self):
        """If submitter enters mismatched name for an enrollment, the name is vetoed to the attached student name."""
        student_b = User.objects.create_user(
            username="2024BCSE070",
            enrollment_number="2024BCSE070",
            first_name="Ritesh",
            last_name="Raj",
            gender="M",
            phone_number="9876543298",
            password="OtherPassword123!",
            is_active=True,
            verified=True,
        )

        self.client.force_login(self.user)
        res = self.client.post(reverse("report_bug"), {
            "feedback_type": "BUG",
            "title": "Announcements badge not updating",
            "description": "Unread counter stays at 1 after dismissing notice.",
            "submitter_name": "Ritesh",  # Partial / informal name
            "submitter_contact": "2024bcse070",  # Lowercase enrollment
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        fb = PlatformFeedback.objects.filter(title="Announcements badge not updating").first()
        self.assertIsNotNone(fb)
        self.assertEqual(fb.user, student_b)
        self.assertEqual(fb.reported_by, self.user)
        # Name is vetoed to full official name attached to enrollment
        self.assertEqual(fb.submitter_name, "Ritesh Raj")
        self.assertEqual(fb.submitter_contact, "2024BCSE070")

    def test_bug_report_against_nonexistent_enrollment_rejected(self):
        """During bug reports, you cannot submit against a person whose enrollment does not exist."""
        self.client.force_login(self.user)
        res = self.client.post(reverse("report_bug"), {
            "feedback_type": "BUG",
            "title": "Broken link in footer",
            "description": "Footer link 404s.",
            "submitter_name": "Unknown Student",
            "submitter_contact": "2024BCSE999",  # Does NOT exist
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        # Bug should NOT be created
        fb = PlatformFeedback.objects.filter(title="Broken link in footer").first()
        self.assertIsNone(fb)

        # Error message rendered
        content = res.content.decode("utf-8")
        self.assertIn("does not exist in the student registry", content)



class RideMisconductReportingTests(TestCase):
    """Verify in-ride misconduct reporting, chat log snapshot, and institute escalation."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()
        self.host = User.objects.create_user(
            username="2024BCSE001",
            enrollment_number="2024BCSE001",
            first_name="Abdus",
            gender="M",
            phone_number="9999999901",
            password="HostPassword123!",
            is_active=True,
            verified=True,
            is_superuser=True,
        )
        self.passenger = User.objects.create_user(
            username="2024BCSE002",
            enrollment_number="2024BCSE002",
            first_name="Raza",
            gender="M",
            phone_number="9999999902",
            password="PassPassword123!",
            is_active=True,
            verified=True,
        )
        self.outsider = User.objects.create_user(
            username="2024BCSE003",
            enrollment_number="2024BCSE003",
            first_name="Outsider",
            gender="M",
            phone_number="9999999903",
            password="OutPassword123!",
            is_active=True,
            verified=True,
        )

        now = timezone.now()
        self.ride = RideRequest.objects.create(
            creator=self.host,
            route="LAL_CHOWK",
            departure_time=now + datetime.timedelta(days=1),
            total_spots=4,
            visibility="MIXED",
            status="OPEN",
        )
        RideParticipant.objects.create(ride=self.ride, user=self.host)
        RideParticipant.objects.create(ride=self.ride, user=self.passenger)

        # Seed in-ride chat messages
        RideMessage.objects.create(
            ride=self.ride,
            author=self.host,
            content="Meet near the main campus library gate at 09:30 AM.",
        )
        RideMessage.objects.create(
            ride=self.ride,
            author=self.passenger,
            content="Understood, I will be there on time with exact change.",
        )

    def test_report_incident_with_chat_log_escalation(self):
        """Passenger reports host for misbehavior and attaches chat log transcript."""
        self.client.force_login(self.passenger)

        res = self.client.post(reverse("report_incident", args=[self.ride.id]), {
            "reported_user": self.host.enrollment_number,
            "category": "INAPPROPRIATE_LANGUAGE",
            "statement": "Host demanded extra unagreed fare and used inappropriate language.",
            "include_chat_log": "on",
        })
        self.assertEqual(res.status_code, 302)

        # Verify RideIncidentReport created
        inc = RideIncidentReport.objects.filter(ride=self.ride, reporter=self.passenger).first()
        self.assertIsNotNone(inc)
        self.assertTrue(inc.reference_id.startswith("ESC-"))
        self.assertEqual(inc.reported_user, self.host)
        self.assertEqual(inc.category, "INAPPROPRIATE_LANGUAGE")
        self.assertTrue(inc.include_chat_log)
        self.assertTrue(inc.escalated_to_institute)
        self.assertEqual(inc.status, "ESCALATED")

        # Verify chat transcript was captured as evidence
        self.assertIn("main campus library gate", inc.chat_transcript)
        self.assertIn("exact change", inc.chat_transcript)
        self.assertIn("2024BCSE001", inc.chat_transcript)
        self.assertIn("2024BCSE002", inc.chat_transcript)

        # Verify AuditEvent was logged
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="INCIDENT_ESCALATED",
                actor_enrollment="2024BCSE002",
                target_id=inc.reference_id,
            ).exists()
        )

    def test_report_incident_non_participant_forbidden(self):
        """Non-participant in the ride is denied from reporting (403)."""
        self.client.force_login(self.outsider)
        res = self.client.post(reverse("report_incident", args=[self.ride.id]), {
            "reported_user": self.host.enrollment_number,
            "category": "HARASSMENT",
            "statement": "False report attempt by non-passenger.",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(RideIncidentReport.objects.count(), 0)

    def test_operational_dashboard_displays_charts_and_escalations(self):
        """Operational dashboard renders telemetry, chart JSON data, and proctorial queue."""
        # File an incident first
        inc = RideIncidentReport.objects.create(
            reference_id="ESC-2026-TEST99",
            ride=self.ride,
            reporter=self.passenger,
            reported_user=self.host,
            category="HARASSMENT",
            statement="Test statement for proctorial dossier review.",
            include_chat_log=True,
            chat_transcript="[2026-10-14 09:30:00] Test Chat",
            escalated_to_institute=True,
        )

        self.client.force_login(self.host)  # host is superuser
        res = self.client.get(reverse("operational_dashboard"))
        self.assertEqual(res.status_code, 200)

        # Check telemetry in context
        self.assertIn("server_telemetry", res.context)
        self.assertIn("cpu_count", res.context["server_telemetry"])
        self.assertIn("db_latency_ms", res.context["server_telemetry"])

        # Check Chart.js JSON datasets in context
        self.assertIn("days_labels_json", res.context)
        self.assertIn("user_growth_json", res.context)
        self.assertIn("ride_creation_json", res.context)
        self.assertIn("route_labels_json", res.context)
        self.assertIn("hourly_labels_json", res.context)

        # Check Escalation queue in template
        self.assertContains(res, "ESC-2026-TEST99")
        self.assertContains(res, "Institute Escalation Queue")
        self.assertContains(res, "growthActivityChart")
        self.assertContains(res, "rideStatusChart")
