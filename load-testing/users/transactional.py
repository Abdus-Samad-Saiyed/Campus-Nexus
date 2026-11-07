"""
Campus Nexus — Profile C: Transactional User
Simulates students executing state-changing POST operations (rides, join requests, chat).
"""

import datetime
import random
from locust import HttpUser, between, task
from config import endpoints
from config.settings import WAIT_TIME_MIN, WAIT_TIME_MAX
from users.common import lease_student_credential, authenticate_locust_user, get_active_test_rides, extract_csrf_token


class TransactionalUser(HttpUser):
    """
    Profile C: Transactional User (25% target traffic)
    Simulates students requesting seats, messaging inside rides, creating listings, and forming friendships.
    """
    wait_time = between(WAIT_TIME_MIN + 1.0, WAIT_TIME_MAX + 2.0)

    def on_start(self):
        self.client.headers["X-LoadTest-Bypass"] = "campus-nexus-benchmark-active"
        """Acquire dedicated test account and establish authenticated session."""
        self.credential = lease_student_credential()
        self.enrollment = self.credential["enrollment_number"]
        self.password = self.credential["password"]
        self.authenticated = authenticate_locust_user(self.client, self.enrollment, self.password)

    def _get_csrf_headers(self, referer_path: str = "/coordinate/"):
        token = self.client.cookies.get("csrftoken") or extract_csrf_token(self.client, referer_path)
        return {
            "X-CSRFToken": token or "",
            "Referer": f"{self.client.base_url}{referer_path}",
        }

    @task(5)
    def request_join_ride(self):
        """Submit a request to join an open ride."""
        if not self.authenticated:
            return
        rides = get_active_test_rides()
        if not rides:
            return
        ride_id = random.choice(rides)
        headers = self._get_csrf_headers(f"/coordinate/{ride_id}/")

        self.client.post(
            f"/coordinate/{ride_id}/join/",
            headers=headers,
            data={"csrfmiddlewaretoken": headers["X-CSRFToken"]},
            name="/coordinate/[id]/join/ [POST]",
            allow_redirects=True
        )

    @task(4)
    def send_ride_chat_message(self):
        """Send a real-time message inside a joined ride group chat."""
        if not self.authenticated:
            return
        rides = get_active_test_rides()
        if not rides:
            return
        ride_id = random.choice(rides)
        headers = self._get_csrf_headers(f"/coordinate/{ride_id}/")

        with self.client.post(
            f"/coordinate/{ride_id}/message/",
            data={
                "content": f"[LOADTEST] Coordination check from {self.enrollment}",
                "csrfmiddlewaretoken": headers["X-CSRFToken"]
            },
            headers=headers,
            name="/coordinate/[id]/message/ [POST]",
            allow_redirects=True,
            catch_response=True,
        ) as resp:
            # 200/302 is success; 403 is expected authorization policy if not a confirmed participant
            if resp.status_code in (200, 302, 403):
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(2)
    def create_ride_listing(self):
        """Create a new temporary ride listing."""
        if not self.authenticated:
            return
        headers = self._get_csrf_headers("/coordinate/create/")
        dep_time = (datetime.datetime.now() + datetime.timedelta(days=random.randint(2, 30))).strftime("%Y-%m-%dT%H:%M")

        self.client.post(
            endpoints.CREATE_RIDE,
            data={
                "start_location": "CAMPUS_GATE",
                "end_location": "LAL_CHOWK",
                "departure_time": dep_time,
                "seats_available": 3,
                "comfort_preference": "ANY",
                "route_notes": f"[LOADTEST] Ephemeral benchmark ride from {self.enrollment}",
                "csrfmiddlewaretoken": headers["X-CSRFToken"]
            },
            headers=headers,
            name="/coordinate/create/ [POST]",
            allow_redirects=True
        )

    @task(2)
    def leave_ride_or_cancel(self):
        """Leave a ride or cancel a pending seat request."""
        if not self.authenticated:
            return
        rides = get_active_test_rides()
        if not rides:
            return
        ride_id = random.choice(rides)
        headers = self._get_csrf_headers(f"/coordinate/{ride_id}/")

        self.client.post(
            f"/coordinate/{ride_id}/leave/",
            headers=headers,
            data={"csrfmiddlewaretoken": headers["X-CSRFToken"]},
            name="/coordinate/[id]/leave/ [POST]",
            allow_redirects=True
        )
