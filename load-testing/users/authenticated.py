"""
Campus Nexus — Profile B: Authenticated Student
Simulates ordinary verified students viewing dashboards, search, and ride directories.
"""

import random
from locust import HttpUser, between, task
from config import endpoints
from config.settings import WAIT_TIME_MIN, WAIT_TIME_MAX
from users.common import lease_student_credential, authenticate_locust_user, get_active_test_rides


class AuthenticatedStudent(HttpUser):
    """
    Profile B: Authenticated Student (55% target traffic)
    Simulates authenticated campus users browsing rides, directory, and their own dashboard.
    """
    wait_time = between(WAIT_TIME_MIN, WAIT_TIME_MAX + 1.0)

    def on_start(self):
        self.client.headers["X-LoadTest-Bypass"] = "campus-nexus-benchmark-active"
        """Acquire dedicated test account and establish authenticated session."""
        self.credential = lease_student_credential()
        self.enrollment = self.credential["enrollment_number"]
        self.password = self.credential["password"]
        self.authenticated = authenticate_locust_user(self.client, self.enrollment, self.password)

    @task(7)
    def view_dashboard(self):
        """Load main student dashboard."""
        if not self.authenticated:
            return
        self.client.get(endpoints.DASHBOARD, name="/dashboard/ [GET]")

    @task(6)
    def browse_rides(self):
        """Browse active rides with telemetry views."""
        if not self.authenticated:
            return
        view = random.choice(["14", "30", "90"])
        self.client.get(f"{endpoints.RIDE_DIRECTORY}?density_view={view}", name="/coordinate/?density_view= [GET]")

    @task(4)
    def view_ride_detail(self):
        """Inspect a specific ride listing."""
        if not self.authenticated:
            return
        rides = get_active_test_rides()
        if not rides:
            return
        ride_id = random.choice(rides)
        self.client.get(f"/coordinate/{ride_id}/", name="/coordinate/[id]/ [GET]")

    @task(3)
    def view_self_profile(self):
        """View own profile page."""
        if not self.authenticated:
            return
        self.client.get(f"/profile/{self.enrollment}/", name="/profile/[self]/ [GET]")

    @task(3)
    def search_student_directory(self):
        """Search student directory with query."""
        if not self.authenticated:
            return
        query = random.choice(["BCSE", "2024", "2099", "Student"])
        self.client.get(f"/search/?q={query}", name="/search/?q= [GET]")

    @task(2)
    def view_my_rides(self):
        """View student's own active and completed rides."""
        if not self.authenticated:
            return
        self.client.get(endpoints.MY_RIDES, name="/coordinate/my-rides/ [GET]")

    @task(2)
    def check_messages(self):
        """Open direct messaging inbox."""
        if not self.authenticated:
            return
        self.client.get(endpoints.MESSAGES_INBOX, name="/messages/ [GET]")
