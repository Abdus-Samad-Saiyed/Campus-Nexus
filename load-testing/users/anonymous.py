"""
Campus Nexus — Profile A: Anonymous Visitor
Simulates a visitor discovering Campus Nexus and browsing publicly accessible pages.
"""

from locust import HttpUser, between, task
from config import endpoints
from config.settings import WAIT_TIME_MIN, WAIT_TIME_MAX


class AnonymousVisitor(HttpUser):
    """
    Profile A: Anonymous Visitor (15% target traffic)
    Simulates students exploring public routes, static assets, and onboarding information.
    """
    wait_time = between(WAIT_TIME_MIN, WAIT_TIME_MAX)

    def on_start(self):
        self.client.headers["X-LoadTest-Bypass"] = "campus-nexus-benchmark-active"

    @task(6)
    def visit_landing(self):
        """Load homepage with primary navigation."""
        self.client.get(endpoints.LANDING, name="/ [Landing]")

    @task(5)
    def browse_public_rides(self):
        """Browse public ride directory without logging in."""
        self.client.get(endpoints.COORDINATE_PUBLIC, name="/coordinate/ [Public Rides]")

    @task(3)
    def visit_login_page(self):
        """Open the login portal."""
        self.client.get(endpoints.LOGIN, name="/login/ [GET]")

    @task(3)
    def view_apply_page(self):
        """View student onboarding application page."""
        self.client.get(endpoints.APPLY, name="/apply/ [GET]")

    @task(2)
    def browse_services(self):
        """Check the service catalogue."""
        self.client.get(endpoints.SERVICES, name="/services/ [GET]")

    @task(2)
    def read_about(self):
        """Read about page."""
        self.client.get(endpoints.ABOUT, name="/about/ [GET]")

    @task(1)
    def view_contributors(self):
        """Browse community contributors."""
        self.client.get(endpoints.CONTRIBUTORS, name="/contributors/ [GET]")

    @task(1)
    def check_health(self):
        """Automated health monitoring probe."""
        self.client.get(endpoints.HEALTHZ, name="/healthz/ [Probe]")
