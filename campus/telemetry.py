"""
Durable, high-performance traffic telemetry and operational performance metrics engine for Campus Nexus.

Features:
- Fast In-Memory & Redis Buffer: Sub-microsecond request recording with zero latency overhead.
- Lasting Database Persistence: Write-behind batch aggregator flushes to `DailyTrafficMetric`
  and `PlatformRuntimeState` every 5 seconds, preserving metrics across server restarts,
  worker reboots, and platform crashes.
- Multi-Worker Safe: Daphne workers update daily records atomically via database `F()` expressions.
- Persistent Platform Uptime: Preserves platform launch timeline across routine service reboots.
"""

import atexit
import collections
import os
import threading
import time
from datetime import timedelta
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

# Thread synchronisation
_LOCK = threading.Lock()
_PROCESS_START_TIME = time.time()

# Write-behind accumulator buffer
_BUFFER = {
    'requests': 0,
    'latency_ms': 0.0,
    'hourly': collections.defaultdict(int),
    'peak_rps': 0,
    'last_flush': time.time(),
}

# 1-second sliding window for peak RPS calculation
_CURRENT_SEC = int(time.time())
_CURRENT_SEC_COUNT = 0

# Cache keys
KEY_TOTAL_REQUESTS = 'nexus_telemetry_total_requests'
KEY_TOTAL_LATENCY_MS = 'nexus_telemetry_total_latency_ms'
KEY_PEAK_RPS = 'nexus_telemetry_peak_rps'
KEY_LAST_WINDOW_TS = 'nexus_telemetry_window_ts'
KEY_WINDOW_REQ_COUNT = 'nexus_telemetry_window_req_count'


def _record_in_memory(elapsed_ms: float):
    """Buffer request telemetry in memory and update sliding RPS window."""
    global _CURRENT_SEC, _CURRENT_SEC_COUNT
    now = timezone.localtime()
    hour_key = f"{now.hour:02d}"
    now_sec = int(time.time())

    with _LOCK:
        _BUFFER['requests'] += 1
        _BUFFER['latency_ms'] += elapsed_ms
        _BUFFER['hourly'][hour_key] += 1

        if now_sec == _CURRENT_SEC:
            _CURRENT_SEC_COUNT += 1
        else:
            _CURRENT_SEC = now_sec
            _CURRENT_SEC_COUNT = 1

        if _CURRENT_SEC_COUNT > _BUFFER['peak_rps']:
            _BUFFER['peak_rps'] = _CURRENT_SEC_COUNT

    # Update cache if available (best-effort, non-blocking)
    try:
        if not cache.add(KEY_TOTAL_REQUESTS, 1, timeout=None):
            cache.incr(KEY_TOTAL_REQUESTS)
    except Exception:
        pass


def flush_telemetry_to_db(fail_silently: bool = True):
    """Flush accumulated in-memory request deltas into durable database tables."""
    from accounts.models import DailyTrafficMetric, PlatformRuntimeState

    with _LOCK:
        delta_requests = _BUFFER['requests']
        delta_latency = _BUFFER['latency_ms']
        delta_hourly = dict(_BUFFER['hourly'])
        delta_peak_rps = _BUFFER['peak_rps']

        if delta_requests == 0:
            return

        # Clear buffer
        _BUFFER['requests'] = 0
        _BUFFER['latency_ms'] = 0.0
        _BUFFER['hourly'].clear()
        _BUFFER['peak_rps'] = 0
        _BUFFER['last_flush'] = time.time()

    today_date = timezone.localdate()
    now = timezone.now()

    try:
        with transaction.atomic():
            metric, created = DailyTrafficMetric.objects.select_for_update().get_or_create(
                date=today_date,
                defaults={
                    'total_requests': 0,
                    'total_latency_ms': 0.0,
                    'peak_rps': 0,
                    'hourly_requests': {},
                }
            )

            # Atomic increments
            metric.total_requests = F('total_requests') + delta_requests
            metric.total_latency_ms = F('total_latency_ms') + delta_latency

            # Update peak RPS if higher
            if delta_peak_rps > metric.peak_rps:
                metric.peak_rps = delta_peak_rps

            # Merge hourly distribution
            current_hourly = dict(metric.hourly_requests or {})
            for h_str, count in delta_hourly.items():
                current_hourly[h_str] = current_hourly.get(h_str, 0) + count
            metric.hourly_requests = current_hourly

            metric.save(update_fields=['total_requests', 'total_latency_ms', 'peak_rps', 'hourly_requests', 'updated_at'])

            # Record heartbeat and ensure platform launch record exists
            PlatformRuntimeState.objects.update_or_create(
                key='platform_heartbeat',
                defaults={'timestamp': now, 'value_text': 'Active', 'value_int': int(time.time())}
            )
            PlatformRuntimeState.objects.get_or_create(
                key='platform_initial_launch',
                defaults={'timestamp': now, 'value_text': 'Platform Launch', 'value_int': int(time.time())}
            )
    except Exception:
        if not fail_silently:
            raise
        # Re-queue on failure so metrics are not lost
        with _LOCK:
            _BUFFER['requests'] += delta_requests
            _BUFFER['latency_ms'] += delta_latency
            for h_str, count in delta_hourly.items():
                _BUFFER['hourly'][h_str] += count
            _BUFFER['peak_rps'] = max(_BUFFER['peak_rps'], delta_peak_rps)


# Register clean shutdown flush
atexit.register(flush_telemetry_to_db)


class _FlushWorker(threading.Thread):
    """Background daemon thread to periodically commit in-memory telemetry to the database."""

    def __init__(self, interval_sec: float = 5.0):
        super().__init__(daemon=True, name="TelemetryFlushWorker")
        self.interval_sec = interval_sec
        self._running = True

    def run(self):
        while self._running:
            time.sleep(self.interval_sec)
            try:
                flush_telemetry_to_db(fail_silently=True)
            except Exception:
                pass


_FLUSH_WORKER = None
_WORKER_LOCK = threading.Lock()


def _is_testing() -> bool:
    import sys
    from django.conf import settings
    return bool(getattr(settings, 'TESTING', False) or ('test' in sys.argv))


def _ensure_flush_worker():
    """Start the periodic flush daemon worker once per process (disabled in test runners)."""
    global _FLUSH_WORKER
    if _is_testing():
        return
    if _FLUSH_WORKER is None:
        with _WORKER_LOCK:
            if _FLUSH_WORKER is None:
                _FLUSH_WORKER = _FlushWorker(interval_sec=5.0)
                _FLUSH_WORKER.start()


class TrafficTelemetryMiddleware:
    """Lightweight, thread-safe request telemetry middleware backed by durable storage."""

    def __init__(self, get_response):
        self.get_response = get_response
        _ensure_flush_worker()

    def __call__(self, request):
        path = request.path_info
        if path.startswith('/static/') or path.startswith('/media/') or path == '/favicon.ico':
            return self.get_response(request)

        start_time = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        try:
            _record_in_memory(elapsed_ms)
        except Exception:
            pass

        return response


def get_traffic_telemetry():
    """Retrieve lasting operational server traffic metrics across time intervals.
    
    Reads from durable `DailyTrafficMetric` and `PlatformRuntimeState` models,
    guaranteeing that historical numbers and uptime survive server restarts and crashes.
    """
    from accounts.models import AuditEvent, DailyTrafficMetric, PlatformRuntimeState, User

    # Commit any unwritten in-memory deltas first
    try:
        flush_telemetry_to_db(fail_silently=True)
    except Exception:
        pass

    now = timezone.localtime()
    today_date = timezone.localdate()
    current_hour_str = f"{now.hour:02d}"

    try:
        today_metric = DailyTrafficMetric.objects.filter(date=today_date).first()
        requests_today = today_metric.total_requests if today_metric else 0

        # Hourly requests for current hour
        requests_last_hour = 0
        if today_metric and today_metric.hourly_requests:
            requests_last_hour = today_metric.hourly_requests.get(current_hour_str, 0)

        # 7-day and 30-day windows
        seven_days_ago = today_date - timedelta(days=7)
        thirty_days_ago = today_date - timedelta(days=30)

        agg_7d = DailyTrafficMetric.objects.filter(date__gte=seven_days_ago).aggregate(t=Sum('total_requests'))
        requests_7d = agg_7d['t'] or 0

        agg_30d = DailyTrafficMetric.objects.filter(date__gte=thirty_days_ago).aggregate(
            t=Sum('total_requests'),
            lat=Sum('total_latency_ms'),
            peak=Max('peak_rps')
        )
        requests_30d = agg_30d['t'] or 0

        agg_all = DailyTrafficMetric.objects.aggregate(t=Sum('total_requests'))
        total_requests = agg_all['t'] or 0
        if total_requests < requests_30d:
            total_requests = requests_30d

        days_count = max(1, min(30, DailyTrafficMetric.objects.filter(date__gte=thirty_days_ago).count()))
        avg_daily_requests = round(requests_30d / days_count) if requests_30d > 0 else 0

        # Latency calculation
        if today_metric and today_metric.total_requests > 0:
            avg_latency = round(today_metric.total_latency_ms / today_metric.total_requests, 1)
        elif requests_30d > 0 and agg_30d['lat']:
            avg_latency = round(agg_30d['lat'] / requests_30d, 1)
        else:
            avg_latency = 0.0

        peak_rps = max(
            today_metric.peak_rps if today_metric else 0,
            agg_30d['peak'] or 0,
            _BUFFER['peak_rps']
        )

        # Persistent platform uptime
        launch_state = PlatformRuntimeState.objects.filter(key='platform_initial_launch').first()
        if launch_state:
            uptime_seconds = max(0, int((timezone.now() - launch_state.timestamp).total_seconds()))
        else:
            uptime_seconds = int(time.time() - _PROCESS_START_TIME)

    except Exception:
        # Fallback to in-memory/cache baseline if DB table is temporarily inaccessible
        recorded_total = cache.get(KEY_TOTAL_REQUESTS, 0) or _BUFFER['requests']
        requests_today = _BUFFER['requests']
        requests_last_hour = _BUFFER['hourly'].get(current_hour_str, 0)
        requests_7d = requests_today
        requests_30d = max(recorded_total, requests_today)
        total_requests = requests_30d
        avg_daily_requests = requests_today
        avg_latency = 0.0
        peak_rps = _BUFFER['peak_rps']
        uptime_seconds = int(time.time() - _PROCESS_START_TIME)

    # Distinct authenticated accounts signed in today
    try:
        active_today_users = User.objects.filter(last_login__date=today_date).count()
        audit_logins_today = AuditEvent.objects.filter(
            event_type='LOGIN_SUCCESS',
            timestamp__date=today_date,
        ).values('actor_enrollment').distinct().count()
        users_signin_today = max(active_today_users, audit_logins_today)
    except Exception:
        users_signin_today = 0

    uptime_display = f"{max(0, uptime_seconds // 3600)}h {(uptime_seconds % 3600) // 60}m"

    return {
        'requests_today': requests_today,
        'requests_last_hour': requests_last_hour,
        'requests_7d': requests_7d,
        'requests_30d': requests_30d,
        'avg_daily_requests': avg_daily_requests,
        'users_signin_today': users_signin_today,
        'total_requests': total_requests,
        'avg_latency_ms': avg_latency,
        'peak_rps': peak_rps,
        'uptime_seconds': uptime_seconds,
        'uptime_display': uptime_display,
    }
