import json
from datetime import date
from django.test import Client, TestCase
from psycopg.types.range import Range

from accounts.models import User
from accommodate.models import (
    AccommodationGroup,
    AccommodationJoinApplication,
    AccommodationMembership,
    AccommodationReputationSnapshot,
    AdmissionMode,
    ApplicationStatus,
    MembershipStatus,
)
from accommodate.services.group_service import create_group


class AccommodateApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="2023BCSE001",
            email="owner@nitk.ac.in",
            enrollment_number="2023BCSE001",
            first_name="Alice",
            last_name="Owner",
        )
        self.applicant = User.objects.create_user(
            username="2023BCSE002",
            email="applicant@nitk.ac.in",
            enrollment_number="2023BCSE002",
            first_name="Bob",
            last_name="Applicant",
        )
        self.stay_period = Range(date(2027, 6, 1), date(2027, 7, 31), bounds='[]')
        self.group = create_group(
            creator=self.owner,
            title="Bangalore API Group",
            destination_city="Bengaluru",
            location_within_city="Indiranagar",
            creator_planned_stay=self.stay_period,
            member_limit=3,
            admission_mode=AdmissionMode.MANUAL,
        )

    def test_api_cities(self):
        response = self.client.get("/api/v1/accommodate/cities/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("cities", data["data"])
        self.assertTrue(any(c["city"] == "Bengaluru" for c in data["data"]["cities"]))

    def test_api_popular_stays(self):
        response = self.client.get("/api/v1/accommodate/stays/popular/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("stays", data["data"])

    def test_api_groups_search(self):
        response = self.client.get("/api/v1/accommodate/groups/?city=Bengaluru")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["total_count"], 1)
        self.assertEqual(data["data"]["groups"][0]["title"], "Bangalore API Group")

    def test_api_group_create(self):
        self.client.force_login(self.applicant)
        payload = {
            "title": "Pune Summer Flat",
            "destination_city": "Pune",
            "location_within_city": "Kalyani Nagar",
            "start_date": "2027-08-01",
            "end_date": "2027-09-30",
            "member_limit": 2,
            "admission_mode": "AUTO",
            "estimated_cost_amount": 13000,
            "estimated_cost_basis": "PER_PERSON_MONTH",
        }
        response = self.client.post(
            "/api/v1/accommodate/groups/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["group"]["title"], "Pune Summer Flat")

    def test_api_group_detail(self):
        response = self.client.get(f"/api/v1/accommodate/groups/{self.group.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["group"]["id"], str(self.group.id))
        self.assertEqual(len(data["data"]["group"]["occupants"]), 1)

    def test_api_group_patch(self):
        self.client.force_login(self.owner)
        patch_payload = {
            "title": "Bangalore API Group Updated",
            "member_limit": 5,
        }
        response = self.client.patch(
            f"/api/v1/accommodate/groups/{self.group.id}/",
            data=json.dumps(patch_payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["data"]["group"]["title"], "Bangalore API Group Updated")
        self.assertEqual(data["data"]["group"]["member_limit"], 5)

    def test_api_apply_and_inbox_and_approve(self):
        # 1. Bob applies
        self.client.force_login(self.applicant)
        apply_payload = {
            "start_date": "2027-06-01",
            "end_date": "2027-07-31",
            "message": "Hi Alice, would love to join!",
        }
        app_resp = self.client.post(
            f"/api/v1/accommodate/groups/{self.group.id}/apply/",
            data=json.dumps(apply_payload),
            content_type="application/json",
        )
        self.assertEqual(app_resp.status_code, 201)
        app_id = app_resp.json()["data"]["application"]["id"]

        # 2. Owner checks inbox
        self.client.force_login(self.owner)
        inbox_resp = self.client.get(f"/api/v1/accommodate/groups/{self.group.id}/inbox/")
        self.assertEqual(inbox_resp.status_code, 200)
        inbox_data = inbox_resp.json()["data"]
        self.assertEqual(len(inbox_data["pending_applications"]), 1)

        # 3. Owner approves application
        approve_resp = self.client.post(
            f"/api/v1/accommodate/applications/{app_id}/approve/",
            content_type="application/json",
        )
        self.assertEqual(approve_resp.status_code, 200)
        app = AccommodationJoinApplication.objects.get(id=app_id)
        self.assertEqual(app.status, ApplicationStatus.APPROVED)
        self.assertIsNotNone(app.membership)
        self.assertEqual(app.membership.status, MembershipStatus.ACTIVE)

    def test_api_reputation(self):
        AccommodationReputationSnapshot.objects.create(
            user=self.owner,
            composite_score=94.50,
            review_count=4,
            group_count=2,
            reliability_average=4.8,
            communication_average=4.6,
            cooperation_average=4.9,
            respect_average=4.7,
        )

        response = self.client.get(f"/api/v1/accommodate/reputation/{self.owner.enrollment_number}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["reputation"]["composite_score"], 94.50)
        self.assertEqual(data["data"]["reputation"]["review_count"], 4)
