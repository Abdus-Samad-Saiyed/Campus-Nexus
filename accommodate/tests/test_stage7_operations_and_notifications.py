from datetime import date
from django.test import Client, TestCase
from django.urls import reverse
from psycopg.types.range import Range

from accounts.models import AuditEvent, User
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationReviewInvitation,
    AdmissionMode,
    ApplicationStatus,
    InvitationStatus,
)
from accommodate.services.group_service import create_group
from accommodate.services.application_service import submit_application


class AccommodateOperationsAndNotificationsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username="admin01",
            email="admin@nitk.ac.in",
            enrollment_number="2020BCSE999",
            password="adminpassword123",
            verified=True,
        )
        self.owner = User.objects.create_user(
            username="2023BCSE001",
            email="owner@nitk.ac.in",
            enrollment_number="2023BCSE001",
            first_name="Alice",
            last_name="Owner",
            verified=True,
        )
        self.applicant = User.objects.create_user(
            username="2023BCSE002",
            email="applicant@nitk.ac.in",
            enrollment_number="2023BCSE002",
            first_name="Bob",
            last_name="Applicant",
            verified=True,
        )

        self.stay_period = Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[]')
        self.group = create_group(
            creator=self.owner,
            title="Bangalore Ops Group",
            destination_city="Bengaluru",
            location_within_city="Indiranagar",
            creator_planned_stay=self.stay_period,
            member_limit=3,
            admission_mode=AdmissionMode.MANUAL,
        )

    def test_api_recent_notifications_includes_stay_application(self):
        # Bob applies
        submit_application(
            group_id=self.group.id,
            applicant=self.applicant,
            requested_stay=self.stay_period,
            message="Looking forward to staying together!",
        )

        # Alice checks notifications
        self.client.force_login(self.owner)
        response = self.client.get(reverse('api_recent_notifications'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        notifs = data["data"]["notifications"]

        app_notifs = [n for n in notifs if n.get("category") == "accommodate"]
        self.assertTrue(len(app_notifs) >= 1)
        self.assertIn("Stay Application", app_notifs[0]["badge"])
        self.assertIn("Bob", app_notifs[0]["content"])

    def test_operational_dashboard_includes_accommodate_metrics(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('operational_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_accommodate_groups", response.context)
        self.assertEqual(response.context["total_accommodate_groups"], 1)
        self.assertEqual(response.context["total_accommodate_occupants"], 1)

    def test_export_accommodate_groups_csv(self):
        self.client.force_login(self.admin_user)
        # Set session freshness
        session = self.client.session
        session['last_step_up_time'] = 9999999999
        session.save()

        response = self.client.get(reverse('export_accommodate_groups_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('campus_nexus_accommodate_groups_export.csv', response['Content-Disposition'])

        csv_content = response.content.decode('utf-8')
        self.assertIn("Group ID", csv_content)
        self.assertIn("Bangalore Ops Group", csv_content)
        self.assertIn("Bengaluru", csv_content)

        # Check audit event logged
        audit = AuditEvent.objects.filter(event_type="EXPORT_GENERATED").first()
        self.assertIsNotNone(audit)
        self.assertIn("accommodation groups", audit.description)
