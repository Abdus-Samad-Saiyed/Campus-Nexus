"""Unit and integration tests for Web Push notifications, 500 error page cursor,
and ASGI client logger resilience.
"""

import json
from unittest.mock import patch
from django.test import TransactionTestCase, RequestFactory
from django.urls import reverse
from django.template.loader import render_to_string
from django.conf import settings
from accounts.models import User, WebPushSubscription, Friendship
from accounts.notifications import send_user_notification, dispatch_web_push
from accounts.client_logger import ClientLoggingASGIWrapper


class WebPushAndClusterResilienceTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='2024BCSE999',
            enrollment_number='2024BCSE999',
            email='test_push@nitsri.ac.in',
            password='TestPassword123!',
            verified=True,
        )
        self.friend = User.objects.create_user(
            username='2024BCSE888',
            enrollment_number='2024BCSE888',
            email='test_friend@nitsri.ac.in',
            password='TestPassword123!',
            verified=True,
        )
        Friendship.objects.create(user1=self.user, user2=self.friend)
        self.factory = RequestFactory()

    def test_vapid_public_key_endpoint(self):
        url = reverse('api_webpush_vapid_key')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertTrue(len(data.get('publicKey', '')) > 20)

    def test_webpush_subscribe_and_unsubscribe_lifecycle(self):
        self.client.force_login(self.user)
        subscribe_url = reverse('api_webpush_subscribe')
        payload = {
            'endpoint': 'https://fcm.googleapis.com/fcm/send/device_token_xyz',
            'keys': {
                'p256dh': 'test_p256dh_key_data',
                'auth': 'test_auth_key_data',
            }
        }
        resp = self.client.post(
            subscribe_url,
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get('status'), 'success')

        sub = WebPushSubscription.objects.filter(endpoint=payload['endpoint']).first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.p256dh, 'test_p256dh_key_data')
        self.assertEqual(sub.auth, 'test_auth_key_data')

        # Test unsubscribe
        unsubscribe_url = reverse('api_webpush_unsubscribe')
        unsub_resp = self.client.post(
            unsubscribe_url,
            data=json.dumps({'endpoint': payload['endpoint']}),
            content_type='application/json',
        )
        self.assertEqual(unsub_resp.status_code, 200)
        self.assertEqual(unsub_resp.json().get('status'), 'success')
        self.assertFalse(WebPushSubscription.objects.filter(endpoint=payload['endpoint']).exists())

    def test_webpush_subscribe_csrf_exempt(self):
        """CSRF exemption on webpush_subscribe_view allows POST even with enforce_csrf_checks=True."""
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.user)
        subscribe_url = reverse('api_webpush_subscribe')
        payload = {
            'endpoint': 'https://fcm.googleapis.com/fcm/send/csrf_exempt_token',
            'keys': {
                'p256dh': 'test_p256dh_csrf',
                'auth': 'test_auth_csrf',
            }
        }
        resp = client.post(
            subscribe_url,
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get('status'), 'success')

    def test_direct_chat_post_invokes_dispatch_web_push(self):
        """HTTP/AJAX POST in direct_chat_view calls dispatch_web_push to notify recipient device."""
        self.client.force_login(self.user)
        url = reverse('direct_chat', args=[self.friend.enrollment_number])

        with patch('accounts.notifications.dispatch_web_push') as mock_push:
            resp = self.client.post(url, {'content': 'Hello over HTTP push!'}, HTTP_ACCEPT='application/json')
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(mock_push.called)
            target_user_id, push_payload = mock_push.call_args[0]
            self.assertEqual(target_user_id, self.friend.pk)
            self.assertIn('Hello over HTTP push!', push_payload['body'])

    def test_dispatch_web_push_invokes_pywebpush(self):
        WebPushSubscription.objects.create(
            user=self.user,
            endpoint='https://fcm.googleapis.com/fcm/send/test_dispatch_token',
            p256dh='test_p256dh',
            auth='test_auth',
        )
        with patch('pywebpush.webpush') as mock_webpush:
            send_user_notification(
                user_id=self.user.id,
                title='Test Alert',
                content='Test content for background push',
                url='/dashboard/',
            )
            # Give daemon thread brief moment to invoke webpush
            import time
            time.sleep(0.15)
            self.assertTrue(mock_webpush.called)
            args, kwargs = mock_webpush.call_args
            sub_info = kwargs.get('subscription_info')
            self.assertEqual(sub_info['endpoint'], 'https://fcm.googleapis.com/fcm/send/test_dispatch_token')
            data = json.loads(kwargs.get('data'))
            self.assertEqual(data.get('title'), 'Test Alert')

    def test_base_template_has_notification_socket_and_exports(self):
        """base.html contains function connectNotifSocket and window exports."""
        with open('templates/base.html', 'r', encoding='utf-8') as f:
            html = f.read()
        self.assertIn('function connectNotifSocket()', html)
        self.assertIn('window.registerBackgroundPushSubscription = registerBackgroundPushSubscription', html)
        self.assertIn('window.unsubscribeBackgroundPush = unsubscribeBackgroundPush', html)
        self.assertIn('window.isNexusNotificationsEnabled = function()', html)
        self.assertIn('requireInteraction: true', html)

    def test_sw_uses_require_interaction_and_absolute_urls(self):
        """sw.js sets requireInteraction: true and resolves URLs with self.location.origin."""
        with open('static/sw.js', 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('requireInteraction: true', content)
        self.assertIn('self.location.origin', content)

    def test_dispatch_web_push_safe_from_async_context(self):
        """dispatch_web_push must not throw SynchronousOnlyOperation when called inside an asyncio event loop."""
        import asyncio
        WebPushSubscription.objects.create(
            user=self.user,
            endpoint='https://fcm.googleapis.com/fcm/send/async_test_token',
            p256dh='async_p256dh',
            auth='async_auth',
        )

        with patch('pywebpush.webpush') as mock_webpush:
            async def run_in_async():
                t = dispatch_web_push(self.user.id, {'title': 'Async Test', 'body': 'Works!'})
                if t:
                    if hasattr(t, 'result'):
                        t.result(timeout=2.0)
                    elif hasattr(t, 'join'):
                        t.join(timeout=2.0)

            # Must execute without SynchronousOnlyOperation error
            asyncio.run(run_in_async())
            self.assertTrue(mock_webpush.called)

    def test_500_error_page_restores_native_cursor(self):
        html = render_to_string('500.html')
        self.assertIn('cursor: default !important', html)
        self.assertIn('cursor: pointer !important', html)
        self.assertIn('no-custom-cursor', html)
        self.assertIn('error-page', html)

    def test_client_logger_safe_when_scope_ident_present_without_meta(self):
        # Must not raise UnboundLocalError when real_client is None and scope_ident is present
        try:
            ClientLoggingASGIWrapper._log_ws_event(
                action_name='CONNECT',
                path='/ws/chat/direct/test/',
                client_key='127.0.0.1:9999',
                real_client=None,
                status_type='connected',
                scope={'client_identity': {'enrollment': '2024BCSE999', 'name': 'Tester'}},
            )
            success = True
        except Exception:
            success = False
        self.assertTrue(success, "ClientLoggingASGIWrapper._log_ws_event crashed on uninitialized meta")
