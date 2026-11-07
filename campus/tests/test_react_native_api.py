"""Tests for React Native Mobile Frontend Support: REST APIs, CORS, and WebSocket Token Auth."""

import io
from PIL import Image
from asgiref.sync import async_to_sync
from django.test import TransactionTestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from campus.api.channels_middleware import get_user_from_token

User = get_user_model()


class ReactNativeApiTest(TransactionTestCase):
    """Verify endpoints and authentication used by React Native Android frontends."""

    def setUp(self):
        self.client = Client()
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(
            enrollment_number="2024BCSE101",
            username="2024BCSE101",
            first_name="Mobile",
            last_name="Tester",
            email="2024bcse101@nitsri.ac.in",
            phone_number="+919876543210",
            gender="M",
            verified=True,
            password=self.password,
        )

    @override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:8081"])
    def test_cors_headers_present_on_api_requests(self):
        """Preflight OPTIONS request should receive valid CORS headers."""
        response = self.client.options(
            "/api/v1/",
            HTTP_ORIGIN="http://localhost:8081",  # Metro bundler default port
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertIn("access-control-allow-origin", response.headers)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:8081")

    def test_mobile_login_returns_bearer_token(self):
        """api_login returns Bearer token session key for React Native storage."""
        response = self.client.post(
            "/api/v1/auth/login/",
            data={
                "enrollment_number": "2024BCSE101",
                "password": self.password,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("token", data["data"])
        self.assertEqual(data["data"]["token_type"], "Bearer")

        token = data["data"]["token"]

        # Access /api/v1/me/ using Bearer token in Authorization header
        me_resp = self.client.get(
            "/api/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(me_resp.status_code, 200)
        me_data = me_resp.json()
        self.assertEqual(me_data["data"]["user"]["enrollment_number"], "2024BCSE101")

    def test_mobile_update_profile_patch(self):
        """PATCH /api/v1/me/ updates bio and phone number for authenticated mobile user."""
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            data={"enrollment_number": "2024BCSE101", "password": self.password},
            content_type="application/json",
        )
        token = login_resp.json()["data"]["token"]

        patch_resp = self.client.patch(
            "/api/v1/me/",
            data={"bio": "React Native Android developer", "phone_number": "+919999988888"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(patch_resp.status_code, 200)

        self.user.refresh_from_db()
        self.assertEqual(self.user.bio, "React Native Android developer")
        self.assertEqual(self.user.phone_number, "9999988888")

    def test_mobile_photo_upload(self):
        """POST /api/v1/me/photo/ uploads profile image from mobile multipart payload."""
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            data={"enrollment_number": "2024BCSE101", "password": self.password},
            content_type="application/json",
        )
        token = login_resp.json()["data"]["token"]

        # Create a small valid test PNG image in-memory
        image_file = io.BytesIO()
        image = Image.new("RGB", (100, 100), color="blue")
        image.save(image_file, "PNG")
        image_file.seek(0)
        image_file.name = "avatar.png"

        upload_resp = self.client.post(
            "/api/v1/me/photo/",
            data={"profile_photo": image_file},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(upload_resp.status_code, 200)
        upload_data = upload_resp.json()
        self.assertEqual(upload_data["status"], "success")
        self.assertTrue(upload_data["data"]["profile_photo_url"].startswith("/media/profiles/"))

    def test_websocket_token_auth_resolution(self):
        """Token query string resolves authenticated user in Channels middleware."""
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            data={"enrollment_number": "2024BCSE101", "password": self.password},
            content_type="application/json",
        )
        token = login_resp.json()["data"]["token"]

        resolved_user = async_to_sync(get_user_from_token)(token)
        self.assertEqual(resolved_user.id, self.user.id)
        self.assertEqual(resolved_user.enrollment_number, "2024BCSE101")
