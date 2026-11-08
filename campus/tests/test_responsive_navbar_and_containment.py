import os
from django.test import TestCase
from django.urls import reverse
from accounts.models import User
from coordinate.models import RideRequest, RideParticipant
from django.utils import timezone
from datetime import timedelta

class ResponsiveNavbarAndContainmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="2024TEST999",
            enrollment_number="2024TEST999",
            phone_number="9876543299",
            password="TestPassword123!",
            verified=True,
            is_active=True
        )

    def test_base_template_navbar_header_row_and_containment(self):
        """Verify templates/base.html has locked navbar-header-row and universal zero-leak containment."""
        res = self.client.get(reverse("landing"))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # 1. Pinned header row & mobile controls pinned right
        self.assertIn("navbar-header-row", content)
        self.assertIn("navbar-mobile-actions", content)
        self.assertIn("d-lg-contents", content)
        self.assertIn("CAMPUS <span class=\"green-accent\">NEXUS</span>", content)

        # 2. Universal zero-leak containment rules
        self.assertIn("UNIVERSAL BOX CONTAINMENT & ZERO-LEAK SYSTEM ARCHITECTURE", content)
        self.assertIn("overflow-wrap: break-word", content)
        self.assertIn("overflow-x: clip", content)
        self.assertIn(".participant-header-row", content)
        self.assertIn(".participant-card", content)

    def test_ride_detail_passenger_containment(self):
        """Verify ride_detail template structures participant cards with responsive wrap & overflow shield."""
        self.client.force_login(self.user)
        ride = RideRequest.objects.create(
            creator=self.user,
            route="NIT_TO_STATION",
            departure_time=timezone.now() + timedelta(days=2),
            total_spots=4,
            status="OPEN",
            visibility="MIXED"
        )
        RideParticipant.objects.create(ride=ride, user=self.user)

        res = self.client.get(reverse("ride_detail", args=[ride.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # Must have participant-header-row with flex-wrap and overflow-hidden
        self.assertIn("participant-header-row", content)
        self.assertIn("participant-card", content)
        self.assertIn("overflow-hidden", content)
        self.assertIn("% on-time", content)
