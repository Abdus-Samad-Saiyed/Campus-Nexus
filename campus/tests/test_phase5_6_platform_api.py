"""Comprehensive Test Suite for Phase 5 & Phase 6.

Phase 5:
- Campus Nexus umbrella platform shell and service directory
- Configurable service catalogue and server-authoritative route gating
- Clockwork watermark background, pointer-inertness, and system-time angle calculation

Phase 6:
- Versioned REST API foundation (/api/v1/)
- Token (Bearer) & Session authentication
- User profile & stats endpoints
- COORDINATE web/API parity (ride discovery, filtering, creation, join, leave, history)
- Domain invariant enforcement (one active ride, gender comfort gating, capacity limits)
"""

import datetime
import json
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache

from campus.services import (
    SERVICE_CATALOGUE,
    get_active_services,
    get_all_services,
    get_service_by_slug,
)
from coordinate.models import RideParticipant, RideRequest

User = get_user_model()


class Phase5PlatformShellWatermarkTests(TestCase):
    """Phase 5 verification: Platform shell, catalogue, gateway, and watermark."""

    def setUp(self):
        self.client = Client()
        self.male_user = User.objects.create_user(
            username="2024BCSE001",
            enrollment_number="2024BCSE001",
            first_name="Abdus",
            gender="M",
            phone_number="9999999901",
            password="SecurePassword123!",
            is_active=True,
            verified=True,
        )

    def test_service_catalogue_structure(self):
        """Service catalogue contains stable identifiers, badges, and required keys."""
        services = get_all_services()
        self.assertGreaterEqual(len(services), 2)

        ids = [s["id"] for s in services]
        self.assertIn("coordinate", ids)
        self.assertIn("accommodate", ids)

        for s in services:
            self.assertIn("name", s)
            self.assertIn("slug", s)
            self.assertIn("is_active", s)
            self.assertIn("status_badge", s)
            self.assertIn("features", s)

    def test_service_catalogue_active_filter(self):
        """Only live services have is_active=True."""
        active = get_active_services()
        self.assertEqual(len(active), 2)
        active_ids = [s["id"] for s in active]
        self.assertIn("coordinate", active_ids)
        self.assertIn("accommodate", active_ids)

    def test_server_authoritative_gateway_active_service(self):
        """Active service route redirects to service entry point."""
        response = self.client.get(reverse("service_gateway", kwargs={"slug": "coordinate"}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/coordinate/rides/")

    def test_server_authoritative_gateway_disabled_service(self):
        """Disabled services cannot be bypassed and return HTTP 403 Forbidden."""
        with mock.patch("campus.services.SERVICE_CATALOGUE", [
            {
                "id": "mock_hub",
                "slug": "print-hub",
                "name": "UPCOMING",
                "tagline": "Coming Soon",
                "url": "/services/print-hub/",
                "is_active": False,
                "status_badge": "Upcoming",
                "features": [],
            }
        ]):
            response = self.client.get(reverse("service_gateway", kwargs={"slug": "print-hub"}))
            self.assertEqual(response.status_code, 403)
            self.assertContains(response, "UPCOMING", status_code=403)
            self.assertContains(response, "Coming Soon", status_code=403)

    def test_server_authoritative_gateway_unknown_service(self):
        """Non-existent services return HTTP 404."""
        response = self.client.get(reverse("service_gateway", kwargs={"slug": "quantum-teleport"}))
        self.assertEqual(response.status_code, 404)

    def test_landing_page_presents_campus_nexus_shell(self):
        """Public landing page features Campus Nexus platform and services."""
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CAMPUS")
        self.assertContains(response, "NEXUS")
        self.assertContains(response, "COORDINATE")
        self.assertContains(response, "UPCOMING")
        self.assertContains(response, "COMING SOON")

    def test_dedicated_service_catalogue_view(self):
        """Dedicated catalogue view renders for both public and logged in users."""
        response = self.client.get(reverse("service_catalogue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modular Campus Infrastructure")

        self.client.force_login(self.male_user)
        response_auth = self.client.get(reverse("service_catalogue"))
        self.assertEqual(response_auth.status_code, 200)
        self.assertContains(response_auth, "COORDINATE")

    def test_dashboard_renders_campus_nexus_services(self):
        """User dashboard integrates the Campus Nexus services directory."""
        self.client.force_login(self.male_user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Campus Nexus Services")
        self.assertContains(response, "COORDINATE")

    def test_clockwork_watermark_markup_and_pointer_inertness(self):
        """Base template includes clockwork watermark container with pointer-inert styling."""
        response = self.client.get(reverse("landing"))
        self.assertContains(response, 'id="clockwork-watermark"')
        self.assertContains(response, 'class="clockwork-watermark-container"')
        self.assertContains(response, 'id="clockwork-hour-hand"')
        self.assertContains(response, 'id="clockwork-minute-hand"')
        self.assertContains(response, 'id="clockwork-second-hand"')
        self.assertContains(response, 'watermark.js')
        self.assertContains(response, 'watermark.css')


class Phase6ApiFoundationTests(TestCase):
    """Phase 6 verification: REST API v1, Auth, and COORDINATE Parity."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.male_user = User.objects.create_user(
            username="2024BCSE001",
            enrollment_number="2024BCSE001",
            first_name="Abdus",
            gender="M",
            phone_number="9999999901",
            password="SecurePassword123!",
            is_active=True,
            verified=True,
        )
        self.female_user = User.objects.create_user(
            username="2024BCSE003",
            enrollment_number="2024BCSE003",
            first_name="Aisha",
            gender="F",
            phone_number="9999999903",
            password="SecurePassword123!",
            is_active=True,
            verified=True,
        )

    def _get_auth_token(self, enrollment="2024BCSE001", password="SecurePassword123!", client=None):
        c = client or Client()
        res = c.post(
            reverse("api_login"),
            data=json.dumps({"enrollment_number": enrollment, "password": password}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        return data["data"]["token"]

    def test_api_root_endpoint(self):
        """Root API endpoint returns platform info and version."""
        res = self.client.get(reverse("api_root"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["version"], "v1")

    def test_api_catalogue_endpoint(self):
        """API catalogue returns all services."""
        res = self.client.get(reverse("api_catalogue"))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreaterEqual(len(data["data"]["services"]), 2)

    def test_api_login_success_and_failure(self):
        """Login validates credentials and issues token."""
        # 1. Invalid credentials
        fail_res = self.client.post(
            reverse("api_login"),
            data=json.dumps({"enrollment_number": "2024BCSE001", "password": "WrongPassword"}),
            content_type="application/json",
        )
        self.assertEqual(fail_res.status_code, 401)
        self.assertEqual(fail_res.json()["code"], "INVALID_CREDENTIALS")

        # 2. Missing fields
        missing_res = self.client.post(
            reverse("api_login"),
            data=json.dumps({"enrollment_number": ""}),
            content_type="application/json",
        )
        self.assertEqual(missing_res.status_code, 400)

        # 3. Valid credentials
        token = self._get_auth_token()
        self.assertTrue(len(token) > 10)

    def test_api_me_authenticated_bearer_token(self):
        """Profile endpoint works with Bearer token authentication."""
        token = self._get_auth_token()

        res = self.client.get(
            reverse("api_me"),
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertEqual(data["user"]["enrollment_number"], "2024BCSE001")
        self.assertEqual(data["user"]["branch"], "CSE")
        self.assertIn("on_time_percentage", data["user"])

    def test_api_me_unauthenticated_rejected(self):
        """Unauthenticated profile request is rejected with 401."""
        res = self.client.get(reverse("api_me"))
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["code"], "AUTH_REQUIRED")

    def test_api_ride_creation_and_validation(self):
        """API ride creation enforces departure in future and gender comfort rules."""
        token = self._get_auth_token()
        now = timezone.now()

        # 1. Reject past departure
        past_time = (now - datetime.timedelta(hours=2)).isoformat()
        res_past = self.client.post(
            reverse("api_ride_create"),
            data=json.dumps({
                "origin": "NIT Campus Gate",
                "destination": "Srinagar Airport",
                "departure_time": past_time,
                "total_spots": 3,
                "visibility": "MIXED",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res_past.status_code, 400)
        self.assertEqual(res_past.json()["code"], "PAST_DEPARTURE")

        # 2. Male student cannot create Girls Only ride
        future_time = (now + datetime.timedelta(days=2)).isoformat()
        res_gender = self.client.post(
            reverse("api_ride_create"),
            data=json.dumps({
                "origin": "NIT Campus Gate",
                "destination": "Srinagar Airport",
                "departure_time": future_time,
                "total_spots": 3,
                "visibility": "GIRLS",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res_gender.status_code, 403)
        self.assertEqual(res_gender.json()["code"], "GENDER_MISMATCH")

        # 3. Valid creation
        res_valid = self.client.post(
            reverse("api_ride_create"),
            data=json.dumps({
                "origin": "NIT Campus Gate",
                "destination": "Srinagar Airport",
                "departure_time": future_time,
                "total_spots": 3,
                "visibility": "MIXED",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res_valid.status_code, 201)
        ride_id = res_valid.json()["data"]["ride"]["id"]
        self.assertTrue(RideRequest.objects.filter(pk=ride_id).exists())

        # 4. Attempting to create second active ride is blocked with 409 Conflict
        res_dup = self.client.post(
            reverse("api_ride_create"),
            data=json.dumps({
                "origin": "NIT Campus Gate",
                "destination": "TRC Srinagar",
                "departure_time": future_time,
                "total_spots": 3,
                "visibility": "MIXED",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res_dup.status_code, 409)
        self.assertEqual(res_dup.json()["code"], "ACTIVE_RIDE_EXISTS")

    def test_api_ride_browse_audience_gating(self):
        """Browse endpoint filters rides according to gender comfort gates."""
        now = timezone.now() + datetime.timedelta(days=1)

        # Create 1 Girls Only ride and 1 Mixed ride
        girls_ride = RideRequest.objects.create(
            creator=self.female_user,
            route="CUSTOM",
            custom_from_location="Girls Hostel",
            custom_to_location="Lal Chowk",
            departure_time=now,
            total_spots=3,
            visibility="GIRLS",
            status="OPEN",
        )
        mixed_ride = RideRequest.objects.create(
            creator=self.female_user,
            route="NIT_TO_STATION",
            departure_time=now,
            total_spots=3,
            visibility="MIXED",
            status="OPEN",
        )

        # 1. Unauthenticated client is rejected (auth required)
        anon_client = Client()
        res_anon = anon_client.get(reverse("api_ride_list"))
        self.assertEqual(res_anon.status_code, 401)

        # 2. Male user cannot see GIRLS ride
        male_client = Client()
        male_token = self._get_auth_token(enrollment="2024BCSE001", client=male_client)
        res_male = male_client.get(
            reverse("api_ride_list"),
            HTTP_AUTHORIZATION=f"Bearer {male_token}",
        )
        male_ids = [r["id"] for r in res_male.json()["data"]["rides"]]
        self.assertIn(mixed_ride.id, male_ids)
        self.assertNotIn(girls_ride.id, male_ids)

        # 3. Female user CAN see GIRLS ride
        female_client = Client()
        female_token = self._get_auth_token(enrollment="2024BCSE003", client=female_client)
        res_female = female_client.get(
            reverse("api_ride_list"),
            HTTP_AUTHORIZATION=f"Bearer {female_token}",
        )
        female_ids = [r["id"] for r in res_female.json()["data"]["rides"]]
        self.assertIn(mixed_ride.id, female_ids)
        self.assertIn(girls_ride.id, female_ids)

    def test_api_ride_join_and_leave_cycle(self):
        """API join and leave operations update participation atomically."""
        now = timezone.now() + datetime.timedelta(days=1)
        ride = RideRequest.objects.create(
            creator=self.male_user,
            route="NIT_TO_AIRPORT",
            departure_time=now,
            total_spots=2,
            visibility="MIXED",
            status="OPEN",
        )
        RideParticipant.objects.create(ride=ride, user=self.male_user)

        female_token = self._get_auth_token(enrollment="2024BCSE003")

        # Female user requests to join (202 Accepted, pending host approval)
        res_join = self.client.post(
            reverse("api_ride_join", kwargs={"ride_id": ride.id}),
            HTTP_AUTHORIZATION=f"Bearer {female_token}",
        )
        self.assertEqual(res_join.status_code, 202)
        request_id = res_join.json()["data"]["request_id"]

        # Duplicate join request returns 409
        res_dup_join = self.client.post(
            reverse("api_ride_join", kwargs={"ride_id": ride.id}),
            HTTP_AUTHORIZATION=f"Bearer {female_token}",
        )
        self.assertEqual(res_dup_join.status_code, 409)

        # Host approves the request
        male_token = self._get_auth_token(enrollment="2024BCSE001")
        res_approve = self.client.post(
            reverse("api_ride_approve_request", kwargs={"ride_id": ride.id, "request_id": request_id}),
            HTTP_AUTHORIZATION=f"Bearer {male_token}",
        )
        self.assertEqual(res_approve.status_code, 200)
        ride.refresh_from_db()
        self.assertEqual(ride.status, "FULL")
        self.assertEqual(ride.participants.count(), 2)

        # Female user leaves
        res_leave = self.client.post(
            reverse("api_ride_leave", kwargs={"ride_id": ride.id}),
            HTTP_AUTHORIZATION=f"Bearer {female_token}",
        )
        self.assertEqual(res_leave.status_code, 200)
        ride.refresh_from_db()
        self.assertEqual(ride.status, "OPEN")
        self.assertEqual(ride.participants.count(), 1)

    def test_api_my_rides_endpoint(self):
        """My rides endpoint categorizes active vs historical rides."""
        token = self._get_auth_token(enrollment="2024BCSE001")
        res = self.client.get(
            reverse("api_my_rides"),
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertIn("active_rides", data)
        self.assertIn("past_rides", data)

    def test_ride_list_datetime_range_and_stepper(self):
        """Web ride_list supports date-time range filters and moving stepper strip."""
        self.client.force_login(self.male_user)
        today = timezone.now().date()
        target_date = today + datetime.timedelta(days=4)

        # 1. Accessing with date_from = today + 4 centers stepper window and computes prev/next
        res = self.client.get(reverse("ride_list"), {"date_from": target_date.strftime("%Y-%m-%d")})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["selected_date"], target_date)
        self.assertIsNotNone(res.context["prev_date"])
        self.assertIsNotNone(res.context["next_date"])
        # Check that date buttons are populated and visible (not empty)
        self.assertContains(res, target_date.strftime("%d"))
        self.assertContains(res, "Prev")
        self.assertContains(res, "Next")

        # 2. Date-time range filtering in API
        r1 = RideRequest.objects.create(
            creator=self.male_user,
            route="NIT_TO_AIRPORT",
            departure_time=timezone.now() + datetime.timedelta(days=1, hours=2),
            total_spots=3,
            visibility="MIXED",
            status="OPEN",
        )
        r2 = RideRequest.objects.create(
            creator=self.male_user,
            route="NIT_TO_AIRPORT",
            departure_time=timezone.now() + datetime.timedelta(days=5, hours=2),
            total_spots=3,
            visibility="MIXED",
            status="OPEN",
        )

        token = self._get_auth_token(enrollment="2024BCSE001")
        # Filter range covering only r2
        res_api = self.client.get(
            reverse("api_ride_list"),
            {
                "date_from": (today + datetime.timedelta(days=4)).strftime("%Y-%m-%d"),
                "date_to": (today + datetime.timedelta(days=6)).strftime("%Y-%m-%d"),
            },
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res_api.status_code, 200)
        api_ride_ids = [r["id"] for r in res_api.json()["data"]["rides"]]
        self.assertIn(r2.id, api_ride_ids)
        self.assertNotIn(r1.id, api_ride_ids)

