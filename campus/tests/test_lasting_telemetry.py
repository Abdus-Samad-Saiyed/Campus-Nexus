"""Tests for Lasting, Persistent Telemetry in Campus Nexus.

Verifies:
1. Request recording and write-behind flush to DailyTrafficMetric and PlatformRuntimeState.
2. Server crash / process restart simulation: metrics survive process reboots and rehydrate correctly.
3. Multi-worker concurrent flush safety via atomic database operations.
4. Uptime continuity across server restarts.
"""

import time
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import AuditEvent, DailyTrafficMetric, PlatformRuntimeState
from campus.telemetry import (
    _record_in_memory,
    flush_telemetry_to_db,
    get_traffic_telemetry,
    _BUFFER,
    _LOCK,
)

User = get_user_model()


class LastingTelemetryTests(TestCase):
    def setUp(self):
        # Clear buffer
        with _LOCK:
            _BUFFER['requests'] = 0
            _BUFFER['latency_ms'] = 0.0
            _BUFFER['hourly'].clear()
            _BUFFER['peak_rps'] = 0

    def test_record_and_flush_to_database(self):
        """Simulate incoming requests and verify write-behind flush into DailyTrafficMetric."""
        today = timezone.localdate()

        # Simulate 5 requests with 10ms latency each
        for _ in range(5):
            _record_in_memory(10.0)

        with _LOCK:
            self.assertEqual(_BUFFER['requests'], 5)
            self.assertEqual(_BUFFER['latency_ms'], 50.0)

        # Flush to DB
        flush_telemetry_to_db()

        # Buffer should be drained
        with _LOCK:
            self.assertEqual(_BUFFER['requests'], 0)

        # Database record should exist
        metric = DailyTrafficMetric.objects.get(date=today)
        self.assertEqual(metric.total_requests, 5)
        self.assertEqual(metric.total_latency_ms, 50.0)
        self.assertEqual(metric.avg_latency_ms, 10.0)
        self.assertGreater(metric.peak_rps, 0)

        # Verify heartbeat and initial launch state created
        self.assertTrue(PlatformRuntimeState.objects.filter(key='platform_heartbeat').exists())
        self.assertTrue(PlatformRuntimeState.objects.filter(key='platform_initial_launch').exists())

    def test_telemetry_survives_server_restart_and_process_reboot(self):
        """Simulate a server crash / process restart.
        
        Verify that when a new process starts with empty memory buffers,
        get_traffic_telemetry() reads the lasting metrics from the database.
        """
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        five_days_ago = today - timedelta(days=5)

        # Pre-seed historical days in the database (simulating past days)
        DailyTrafficMetric.objects.create(
            date=five_days_ago,
            total_requests=100,
            total_latency_ms=2500.0,
            peak_rps=25,
            hourly_requests={'12': 50, '13': 50},
        )
        DailyTrafficMetric.objects.create(
            date=yesterday,
            total_requests=200,
            total_latency_ms=4000.0,
            peak_rps=30,
            hourly_requests={'10': 100, '11': 100},
        )
        DailyTrafficMetric.objects.create(
            date=today,
            total_requests=50,
            total_latency_ms=1000.0,
            peak_rps=15,
            hourly_requests={f"{timezone.localtime().hour:02d}": 50},
        )

        # Set persistent launch time 2 hours ago
        two_hours_ago = timezone.now() - timedelta(hours=2, minutes=15)
        PlatformRuntimeState.objects.create(
            key='platform_initial_launch',
            timestamp=two_hours_ago,
            value_text='Platform Launch',
            value_int=int(two_hours_ago.timestamp()),
        )

        # SIMULATE SERVER RESTART: Memory buffer is completely empty (0)
        with _LOCK:
            _BUFFER['requests'] = 0
            _BUFFER['latency_ms'] = 0.0
            _BUFFER['hourly'].clear()
            _BUFFER['peak_rps'] = 0

        # Query operational dashboard telemetry
        telemetry = get_traffic_telemetry()

        # Verify historical numbers are preserved
        self.assertEqual(telemetry['requests_today'], 50)
        self.assertEqual(telemetry['requests_last_hour'], 50)
        self.assertEqual(telemetry['requests_7d'], 350)  # 100 + 200 + 50
        self.assertEqual(telemetry['requests_30d'], 350)
        self.assertEqual(telemetry['total_requests'], 350)
        self.assertEqual(telemetry['peak_rps'], 30)  # Peak across 30 days
        self.assertAlmostEqual(telemetry['avg_latency_ms'], 20.0, delta=0.5)  # 1000ms / 50reqs

        # Verify persistent uptime was NOT reset to 0h 0m
        self.assertIn("2h 15m", telemetry['uptime_display'])
        self.assertGreaterEqual(telemetry['uptime_seconds'], 7200)

    def test_multi_worker_atomic_accumulation(self):
        """Simulate two workers flushing deltas into the same day."""
        today = timezone.localdate()

        # Worker 1 flushes 20 requests
        with _LOCK:
            _BUFFER['requests'] = 20
            _BUFFER['latency_ms'] = 400.0
            _BUFFER['hourly']['14'] = 20
            _BUFFER['peak_rps'] = 12
        flush_telemetry_to_db()

        # Worker 2 flushes 30 requests
        with _LOCK:
            _BUFFER['requests'] = 30
            _BUFFER['latency_ms'] = 600.0
            _BUFFER['hourly']['14'] = 30
            _BUFFER['peak_rps'] = 18
        flush_telemetry_to_db()

        metric = DailyTrafficMetric.objects.get(date=today)
        self.assertEqual(metric.total_requests, 50)
        self.assertEqual(metric.total_latency_ms, 1000.0)
        self.assertEqual(metric.hourly_requests.get('14'), 50)
        self.assertEqual(metric.peak_rps, 18)

    def test_middleware_request_integration(self):
        """Test HTTP request flow through TrafficTelemetryMiddleware."""
        client = Client()
        response = client.get('/about/')
        self.assertEqual(response.status_code, 200)

        # Buffer should have recorded the request
        with _LOCK:
            buffered = _BUFFER['requests']
        self.assertGreaterEqual(buffered, 1)

        # Flushed to DB
        telemetry = get_traffic_telemetry()
        self.assertGreaterEqual(telemetry['requests_today'], 1)
        self.assertGreaterEqual(telemetry['total_requests'], 1)
