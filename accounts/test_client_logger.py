"""Tests for client request identity console logging (receival timestamp, enrollment number, name, and IP address)
and protected profile media authorization.
"""

import os
import re
import tempfile
from unittest.mock import MagicMock, patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from accounts.client_logger import (
    _CLIENT_REGISTRY,
    _CONNECTION_IDENTITY_CACHE,
    _REGISTRY_LOCK,
    _SESSION_IDENTITY_CACHE,
    _WEBSOCKET_REGISTRY,
    register_client_identity,
    ClientLoggingASGIWrapper,
    pop_client_request,
    resolve_client_display,
    seed_client_request,
    update_or_record_client_request,
)
from accounts.management.commands.runserver import Command
from accounts.middleware import ClientIdentityLoggingMiddleware
from campus.urls import serve_protected_media

User = get_user_model()


class ClientLoggerRegistryTests(TestCase):
    """Test registry functions for client metadata tracking."""

    def test_resolve_client_display_local(self):
        """Local loopback addresses retain client key with port."""
        res = resolve_client_display("127.0.0.1:65521", "127.0.0.1")
        self.assertEqual(res, "127.0.0.1:65521")

    def test_resolve_client_display_proxy_with_port(self):
        """Proxy/Cloudflare real IP replaces loopback host while preserving port."""
        res = resolve_client_display("127.0.0.1:37548", "203.0.113.195")
        self.assertEqual(res, "203.0.113.195:37548")

    def test_resolve_client_display_none_or_missing(self):
        """Handles missing client key gracefully."""
        self.assertEqual(resolve_client_display(None, "198.51.100.1"), "198.51.100.1")
        self.assertEqual(resolve_client_display(None, None), "127.0.0.1")

    def test_seed_and_pop_client_request_defaults(self):
        """Seeded request returns defaults N.A. for enrollment and name."""
        client_key = "192.168.1.10:44111"
        seed_client_request(client_key, real_client=client_key)
        item = pop_client_request(client_key)
        self.assertEqual(item["enrollment"], "N.A.")
        self.assertEqual(item["name"], "N.A.")
        self.assertEqual(item["real_client"], client_key)
        self.assertIn("received_at", item)

    def test_update_client_request(self):
        """Updating seeded request sets authenticated user metadata."""
        client_key = "192.168.1.10:44112"
        seed_client_request(client_key)
        update_or_record_client_request(
            client_key,
            enrollment="2024BCSE065",
            name="Abdus Samad Saiyed",
            real_client="203.0.113.50:44112",
        )
        item = pop_client_request(client_key)
        self.assertEqual(item["enrollment"], "2024BCSE065")
        self.assertEqual(item["name"], "Abdus Samad Saiyed")
        self.assertEqual(item["real_client"], "203.0.113.50:44112")

    def test_pop_nonexistent_returns_defaults(self):
        """Popping a client key with no records returns safe defaults."""
        item = pop_client_request("10.0.0.99:9999")
        self.assertEqual(item["enrollment"], "N.A.")
        self.assertEqual(item["name"], "N.A.")
        self.assertEqual(item["real_client"], "10.0.0.99:9999")
        self.assertIn("received_at", item)


class ClientIdentityLoggingMiddlewareTests(TestCase):
    """Test middleware identity extraction for both anonymous and authenticated sessions."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="2024BCSE065",
            enrollment_number="2024BCSE065",
            first_name="Abdus Samad",
            last_name="Saiyed",
            email="2024bcse065@nitsri.ac.in",
            password="TestPassword123!",
            is_active=True,
            verified=True,
        )

    def test_unauthenticated_request_records_na(self):
        """Unauthenticated requests record N.A. for enrollment and name."""
        request = self.factory.get("/")
        request.user = AnonymousUser()
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["REMOTE_PORT"] = 23986

        middleware = ClientIdentityLoggingMiddleware(lambda req: MagicMock(status_code=200))
        middleware(request)

        item = pop_client_request("127.0.0.1:23986")
        self.assertEqual(item["enrollment"], "N.A.")
        self.assertEqual(item["name"], "N.A.")
        self.assertEqual(item["real_client"], "127.0.0.1:23986")

    def test_authenticated_request_records_user_info(self):
        """Authenticated requests record user's enrollment number and full name."""
        request = self.factory.get("/rides/")
        request.user = self.user
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["REMOTE_PORT"] = 65521

        middleware = ClientIdentityLoggingMiddleware(lambda req: MagicMock(status_code=200))
        middleware(request)

        item = pop_client_request("127.0.0.1:65521")
        self.assertEqual(item["enrollment"], "2024BCSE065")
        self.assertEqual(item["name"], "Abdus Samad Saiyed")
        self.assertEqual(item["real_client"], "127.0.0.1:65521")

    def test_cloudflare_header_extracts_real_ip(self):
        """CF-Connecting-IP header sets real client IP on recorded request."""
        request = self.factory.get("/profile/2024BCSE065/")
        request.user = self.user
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["REMOTE_PORT"] = 37548
        request.META["HTTP_CF_CONNECTING_IP"] = "103.21.244.2"

        middleware = ClientIdentityLoggingMiddleware(lambda req: MagicMock(status_code=200))
        middleware(request)

        item = pop_client_request("127.0.0.1:37548")
        self.assertEqual(item["enrollment"], "2024BCSE065")
        self.assertEqual(item["name"], "Abdus Samad Saiyed")
        self.assertEqual(item["real_client"], "103.21.244.2:37548")

    def test_bearer_token_resolves_authenticated_user(self):
        """Bearer token in Authorization header resolves authenticated user for API calls."""
        session = SessionStore()
        session["_auth_user_id"] = str(self.user.pk)
        session.create()

        request = self.factory.get("/api/v1/rides/")
        request.user = AnonymousUser()
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["REMOTE_PORT"] = 55123
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {session.session_key}"

        middleware = ClientIdentityLoggingMiddleware(lambda req: MagicMock(status_code=200))
        middleware(request)

        item = pop_client_request("127.0.0.1:55123")
        self.assertEqual(item["enrollment"], "2024BCSE065")
        self.assertEqual(item["name"], "Abdus Samad Saiyed")


class RunserverCommandLogActionTests(TestCase):
    """Test Command.log_action formatting of HTTP request output lines with timestamps."""

    def setUp(self):
        self.cmd = Command()
        with _REGISTRY_LOCK:
            _CLIENT_REGISTRY.clear()
            _CONNECTION_IDENTITY_CACHE.clear()
            _SESSION_IDENTITY_CACHE.clear()
            _WEBSOCKET_REGISTRY.clear()

    def test_log_action_unauthenticated_format_with_timestamp(self):
        """Unauthenticated request logs with server receival timestamp prefix and N.A. user."""
        details = {
            "method": "GET",
            "path": "/",
            "status": 200,
            "time_taken": 0.00,
            "client": "127.0.0.1:23986",
        }
        with self.assertLogs("django.channels.server", level="INFO") as cm:
            self.cmd.log_action("http", "complete", details)
            logged = cm.output[-1]
            # Match timestamp prefix: [DD/Mon/YYYY HH:MM:SS]
            self.assertRegex(logged, r"\[\d{2}/[A-Za-z]{3}/\d{4} \d{2}:\d{2}:\d{2}\] HTTP GET / 200 \[0.00, 127.0.0.1:23986, N.A., N.A.\]")

    def test_log_action_authenticated_format_with_timestamp(self):
        """Authenticated request logs with receival timestamp, enrollment number, and name."""
        client_key = "127.0.0.1:37548"
        update_or_record_client_request(
            client_key,
            enrollment="2024BCSE065",
            name="Abdus Samad Saiyed",
            real_client=client_key,
        )
        details = {
            "method": "POST",
            "path": "/profile/2024BCSE065/",
            "status": 302,
            "time_taken": 0.02,
            "client": client_key,
        }
        with self.assertLogs("django.channels.server", level="INFO") as cm:
            self.cmd.log_action("http", "complete", details)
            logged = cm.output[-1]
            self.assertRegex(logged, r"\[\d{2}/[A-Za-z]{3}/\d{4} \d{2}:\d{2}:\d{2}\] HTTP POST /profile/2024BCSE065/ 302 \[0.02, 127.0.0.1:37548, 2024BCSE065, Abdus Samad Saiyed\]")

    def test_log_action_bad_request_warning(self):
        """400 Bad Request logs via warning level with formatted client details and timestamp."""
        client_key = "127.0.0.1:37548"
        update_or_record_client_request(
            client_key,
            enrollment="2024BCSE065",
            name="Abdus Samad Saiyed",
            real_client=client_key,
        )
        details = {
            "method": "POST",
            "path": "/auth/passkey/register/verify/",
            "status": 400,
            "time_taken": 0.01,
            "client": client_key,
        }
        with self.assertLogs("django.channels.server", level="WARNING") as cm:
            self.cmd.log_action("http", "complete", details)
            logged = cm.output[-1]
            self.assertIn("HTTP POST /auth/passkey/register/verify/ 400 [0.01, 127.0.0.1:37548, 2024BCSE065, Abdus Samad Saiyed]", logged)

    def test_websocket_log_action(self):
        """WebSocket connection logs formatted with timestamp, client, enrollment, and name."""
        client_key = "127.0.0.1:44444"
        update_or_record_client_request(
            client_key,
            enrollment="2024BCSE065",
            name="Abdus Samad Saiyed",
            real_client=client_key,
        )
        details = {
            "path": "/ws/ride/1/",
            "client": client_key,
        }
        with self.assertLogs("django.channels.server", level="INFO") as cm:
            self.cmd.log_action("websocket", "connected", details)
            logged = cm.output[-1]
            self.assertRegex(logged, r"\[\d{2}/[A-Za-z]{3}/\d{4} \d{2}:\d{2}:\d{2}\] WebSocket CONNECT /ws/ride/1/ \[127.0.0.1:44444, 2024BCSE065, Abdus Samad Saiyed\]")

    def test_log_action_in_production_debug_false(self):
        """Under DEBUG=False, django.channels.server outputs the exact enriched completion string."""
        with override_settings(DEBUG=False):
            client_key = "14.139.61.131:19440"
            update_or_record_client_request(
                client_key,
                enrollment="2024BCSE065",
                name="Abdus Samad Saiyed",
                real_client=client_key,
            )
            details = {
                "method": "GET",
                "path": "/media/profiles/f0294f7153af4070a8ba234777339724.jpg",
                "status": 200,
                "time_taken": 0.01,
                "client": client_key,
            }
            with self.assertLogs("django.channels.server", level="INFO") as cm:
                self.cmd.log_action("http", "complete", details)
                logged = cm.output[-1]
                self.assertIn("HTTP GET /media/profiles/f0294f7153af4070a8ba234777339724.jpg 200 [0.01, 14.139.61.131:19440, 2024BCSE065, Abdus Samad Saiyed]", logged)

    def test_guest_demo_user_logs_as_na(self):
        """Guest demo user requests are masked as N.A. to preserve student privacy."""
        factory = RequestFactory()
        request = factory.get("/coordinate/")
        session = SessionStore()
        session["is_guest_demo"] = True
        session.create()
        request.session = session

        from accounts.demo import ensure_guest_demo_user
        demo_user = ensure_guest_demo_user("default")
        request.user = demo_user
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["REMOTE_PORT"] = 49152

        middleware = ClientIdentityLoggingMiddleware(lambda req: MagicMock(status_code=200))
        middleware(request)

        item = pop_client_request("127.0.0.1:49152")
        self.assertEqual(item["enrollment"], "N.A.")
        self.assertEqual(item["name"], "N.A.")


class ClientLoggingASGIWrapperTests(TestCase):
    """Test ASGI application wrapper metadata interception."""

    async def test_asgi_wrapper_seeds_static_metadata(self):
        """ASGI wrapper pre-seeds metadata for static or fast requests."""
        called = False

        async def inner_app(scope, receive, send):
            nonlocal called
            called = True

        wrapper = ClientLoggingASGIWrapper(inner_app)
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 23986),
            "headers": [],
        }
        await wrapper(scope, None, None)
        self.assertTrue(called)

        item = pop_client_request("127.0.0.1:23986")
        self.assertEqual(item["enrollment"], "N.A.")
        self.assertEqual(item["name"], "N.A.")
        self.assertEqual(item["real_client"], "127.0.0.1:23986")
        self.assertIn("received_at", item)

    async def test_asgi_wrapper_seeds_cloudflare_ip(self):
        """ASGI wrapper extracts cf-connecting-ip from raw scope headers."""
        async def inner_app(scope, receive, send):
            pass

        wrapper = ClientLoggingASGIWrapper(inner_app)
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 37548),
            "headers": [
                (b"cf-connecting-ip", b"103.21.244.2"),
            ],
        }
        await wrapper(scope, None, None)

        item = pop_client_request("127.0.0.1:37548")
        self.assertEqual(item["real_client"], "103.21.244.2:37548")


class ProtectedMediaAuthorizationTests(TestCase):
    """Test access control rules for serve_protected_media."""

    def setUp(self):
        self.factory = RequestFactory()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profiles_dir = os.path.join(self.temp_dir.name, 'profiles')
        os.makedirs(self.profiles_dir, exist_ok=True)

        # Create dummy photo on disk
        self.photo_filename = 'f0294f7153af4070a8ba234777339724.jpg'
        self.photo_path = os.path.join(self.profiles_dir, self.photo_filename)
        with open(self.photo_path, 'wb') as f:
            f.write(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00')

        # Create unverified user (e.g. self-registered / early-launch)
        self.unverified_user = User.objects.create_user(
            username="2026BMEC086",
            enrollment_number="2026BMEC086",
            first_name="Mohammad",
            last_name="Rehan",
            email="2026bmec086@nitsri.ac.in",
            password="TestPassword123!",
            is_active=True,
            verified=False,
            profile_photo=f'profiles/{self.photo_filename}',
        )

        # Create other verified user
        self.verified_user = User.objects.create_user(
            username="2024BCSE065",
            enrollment_number="2024BCSE065",
            first_name="Abdus Samad",
            last_name="Saiyed",
            email="2024bcse065@nitsri.ac.in",
            password="TestPassword123!",
            is_active=True,
            verified=True,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unverified_user_can_view_own_profile_photo(self):
        """Unverified student can access and view their own uploaded profile photo."""
        with override_settings(MEDIA_ROOT=self.temp_dir.name):
            request = self.factory.get(f'/media/profiles/{self.photo_filename}')
            request.user = self.unverified_user

            response = serve_protected_media(request, f'profiles/{self.photo_filename}')
            self.assertEqual(response.status_code, 200)
            if hasattr(response, 'close'):
                response.close()

    def test_unverified_user_in_early_launch_can_view_public_profile_photo(self):
        """In Early Launch Mode, untrusted users can view public profile photos."""
        with override_settings(MEDIA_ROOT=self.temp_dir.name, EARLY_LAUNCH_MODE=True):
            request = self.factory.get(f'/media/profiles/{self.photo_filename}')
            request.user = self.verified_user

            response = serve_protected_media(request, f'profiles/{self.photo_filename}')
            self.assertEqual(response.status_code, 200)
            if hasattr(response, 'close'):
                response.close()

    def test_unverified_user_cannot_view_hidden_profile_photo_of_others(self):
        """If owner hid photo from search/public, other unverified users are denied with 403."""
        self.unverified_user.hide_profile_photo = True
        self.unverified_user.save()

        other_unverified = User.objects.create_user(
            username="2026BMEC099",
            enrollment_number="2026BMEC099",
            email="2026bmec099@nitsri.ac.in",
            password="TestPassword123!",
            verified=False,
        )

        with override_settings(MEDIA_ROOT=self.temp_dir.name):
            request = self.factory.get(f'/media/profiles/{self.photo_filename}')
            request.user = other_unverified

            response = serve_protected_media(request, f'profiles/{self.photo_filename}')
            self.assertEqual(response.status_code, 403)
            if hasattr(response, 'close'):
                response.close()

class ConnectionIdentityAndPipelineTests(TestCase):
    """Test connection-level identity persistence and pipelined request disambiguation."""

    def setUp(self):
        self.cmd = Command()

    def test_pipelined_requests_disambiguated_by_path(self):
        """Pipelined requests on same socket correctly match identity and path."""
        client_key = "14.139.61.131:14232"
        seed_client_request(client_key, enrollment="2024BCSE065", name="Abdus Samad Saiyed", path="/coordinate/my-rides/")
        seed_client_request(client_key, enrollment="2024BCSE065", name="Abdus Samad Saiyed", path="/media/profiles/avatar.jpg")

        meta_img = pop_client_request(client_key, path="/media/profiles/avatar.jpg")
        self.assertEqual(meta_img["path"], "/media/profiles/avatar.jpg")
        self.assertEqual(meta_img["enrollment"], "2024BCSE065")

        meta_page = pop_client_request(client_key, path="/coordinate/my-rides/")
        self.assertEqual(meta_page["path"], "/coordinate/my-rides/")
        self.assertEqual(meta_page["enrollment"], "2024BCSE065")

    def test_static_asset_inherits_connection_identity(self):
        """Unauthenticated static asset on an active connection inherits user identity."""
        client_key = "14.139.61.131:14232"
        register_client_identity(client_key=client_key, enrollment="2024BCSE065", name="Abdus Samad Saiyed")

        seed_client_request(client_key, enrollment="N.A.", name="N.A.", path="/static/css/watermark.css")
        meta = pop_client_request(client_key, path="/static/css/watermark.css")
        self.assertEqual(meta["enrollment"], "2024BCSE065")
        self.assertEqual(meta["name"], "Abdus Samad Saiyed")

    def test_websocket_lifecycle_preserves_identity(self):
        """WebSocket handshaking, connect, and disconnect retain user identity across lifecycle."""
        client_key = "14.139.61.131:21582"
        register_client_identity(client_key=client_key, enrollment="2024BCSE065", name="Abdus Samad Saiyed")
        seed_client_request(client_key, enrollment="2024BCSE065", name="Abdus Samad Saiyed", path="/ws/notifications/", scope_type="websocket")

        details_shake = {"path": "/ws/notifications/", "client": client_key}
        details_conn = {"path": "/ws/notifications/", "client": client_key}
        details_disc = {"path": "/ws/notifications/", "client": client_key}

        with self.assertLogs("django.channels.server", level="INFO") as cm:
            self.cmd.log_action("websocket", "connecting", details_shake)
            self.cmd.log_action("websocket", "connected", details_conn)
            self.cmd.log_action("websocket", "disconnected", details_disc)

            self.assertIn("WebSocket HANDSHAKING /ws/notifications/ [14.139.61.131:21582, 2024BCSE065, Abdus Samad Saiyed]", cm.output[0])
            self.assertIn("WebSocket CONNECT /ws/notifications/ [14.139.61.131:21582, 2024BCSE065, Abdus Samad Saiyed]", cm.output[1])
            self.assertIn("WebSocket DISCONNECT /ws/notifications/ [14.139.61.131:21582, 2024BCSE065, Abdus Samad Saiyed]", cm.output[2])
