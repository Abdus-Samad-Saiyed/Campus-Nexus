"""
Campus Nexus — Profile D: Administrative User
Simulates administrative users reviewing operations, student applications, and analytics.
"""

from locust import HttpUser, between, task
from config import endpoints
from config.settings import WAIT_TIME_MIN, WAIT_TIME_MAX
from users.common import get_admin_credential, authenticate_locust_user


class AdministrativeUser(HttpUser):
    """
    Profile D: Administrative User (5% target traffic)
    Simulates high-privilege staff accessing analytical dashboards and operational records.
    """
    wait_time = between(WAIT_TIME_MIN + 2.0, WAIT_TIME_MAX + 4.0)

    def on_start(self):
        self.client.headers["X-LoadTest-Bypass"] = "campus-nexus-benchmark-active"
        """Authenticate with the dedicated admin account."""
        self.credential = get_admin_credential()
        self.enrollment = self.credential["enrollment_number"]
        self.password = self.credential["password"]
        self.authenticated = authenticate_locust_user(self.client, self.enrollment, self.password)

    @task(5)
    def view_operations_dashboard(self):
        """Load operational monitoring desk."""
        if not self.authenticated:
            return
        self.client.get(endpoints.OPERATIONS_DASHBOARD, name="/operations/ [Admin]")

    @task(4)
    def view_analytics(self):
        """View platform telemetry and audit analytics."""
        if not self.authenticated:
            return
        self.client.get(endpoints.ANALYTICS_VIEW, name="/analytics/ [Admin]")

    @task(3)
    def view_applications(self):
        """Review student account verification applications."""
        if not self.authenticated:
            return
        self.client.get(endpoints.APPLICATIONS_REVIEW, name="/applications/ [Admin]")

    @task(1)
    def export_rides(self):
        """Download sanitized rides CSV export."""
        if not self.authenticated:
            return
        self.client.get(endpoints.EXPORT_RIDES_CSV, name="/operations/export/rides/ [CSV]")
