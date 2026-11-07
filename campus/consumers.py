"""WebSocket consumers for real-time live Operations and Applications dashboards."""

import json
import logging
import zoneinfo
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)
IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")


class OperationsConsumer(AsyncWebsocketConsumer):
    """Live telemetry and operational audit stream for /operations/."""

    GROUP_NAME = "staff_operations"

    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated or not (self.user.is_staff or self.user.is_superuser):
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()

        # Send initial snapshot
        snapshot = await self._get_telemetry_snapshot()
        await self.send(text_data=json.dumps({
            "type": "telemetry_snapshot",
            "data": snapshot,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, "channel_layer") and self.channel_layer:
            await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            payload = json.loads(text_data)
            action = payload.get("action")
            if action in ("ping", "refresh_telemetry"):
                snapshot = await self._get_telemetry_snapshot()
                await self.send(text_data=json.dumps({
                    "type": "telemetry_snapshot",
                    "data": snapshot,
                }))
        except Exception as e:
            logger.debug("OperationsConsumer receive error: %s", e)

    async def audit_event_broadcast(self, event):
        """Receive newly logged AuditEvent and push to dashboard table."""
        await self.send(text_data=json.dumps({
            "type": "audit_event",
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "event_type": event.get("event_type"),
            "actor": event.get("actor"),
            "details": event.get("details"),
        }))

    async def operations_telemetry(self, event):
        """Receive periodic or triggered telemetry push."""
        await self.send(text_data=json.dumps({
            "type": "telemetry_update",
            "data": event.get("data", {}),
        }))

    @sync_to_async
    def _get_telemetry_snapshot(self):
        import time, os, threading, platform
        from django.db import connection
        from django.contrib.sessions.models import Session
        from accounts.models import User, AccountApplication, AuditEvent
        from coordinate.models import RideRequest
        from campus.telemetry import get_traffic_telemetry

        db_latency_ms = 0.0
        try:
            t0 = time.perf_counter()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception:
            db_latency_ms = -1.0

        now = timezone.now()
        now_ist = timezone.localtime(now, IST_TZ)

        # Active unique sessions count
        active_sessions = Session.objects.filter(expire_date__gt=now)
        active_sessions_count = active_sessions.count()

        active_user_ids = set()
        for s in active_sessions[:200]:
            try:
                data = s.get_decoded()
                uid = data.get("_auth_user_id")
                if uid:
                    active_user_ids.add(str(uid))
            except Exception:
                pass

        traffic = get_traffic_telemetry()

        return {
            "server_time": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
            "db_engine": connection.vendor.upper(),
            "db_latency_ms": db_latency_ms,
            "cpu_count": os.cpu_count() or 1,
            "active_threads": threading.active_count(),
            "python_version": platform.python_version(),
            "requests_today": traffic.get("requests_today", 0),
            "avg_daily_requests": traffic.get("avg_daily_requests", 0),
            "signins_today": traffic.get("signins_today", 0),
            "peak_rps": traffic.get("peak_rps", 0),
            "uptime_display": traffic.get("uptime_display", "0m"),
            "active_sessions_count": active_sessions_count,
            "unique_active_users_count": len(active_user_ids),
            "total_users": User.objects.count(),
            "verified_users": User.objects.filter(verified=True).count(),
            "pending_applications": AccountApplication.objects.filter(approved=False).count(),
            "total_rides": RideRequest.objects.count(),
            "active_rides": RideRequest.objects.filter(status__in=["OPEN", "FULL"]).count(),
        }


class ApplicationsConsumer(AsyncWebsocketConsumer):
    """Live application submission and approval stream for /applications/."""

    GROUP_NAME = "staff_applications"

    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated or not (self.user.is_staff or self.user.is_superuser):
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "channel_layer") and self.channel_layer:
            await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    async def application_created(self, event):
        """Receive newly filed student application and push to table."""
        await self.send(text_data=json.dumps({
            "type": "application_created",
            "id": event.get("id"),
            "application_number": event.get("application_number"),
            "enrollment_number": event.get("enrollment_number"),
            "full_name": event.get("full_name"),
            "email": event.get("email", ""),
            "phone_number": event.get("phone_number"),
            "gender": event.get("gender"),
            "is_temp_upgrade": event.get("is_temp_upgrade", False),
            "created_at": event.get("created_at"),
        }))

    async def application_status_changed(self, event):
        """Receive application approval/rejection and push update/removal."""
        await self.send(text_data=json.dumps({
            "type": "application_status_changed",
            "id": event.get("id"),
            "status": event.get("status"),
            "actor": event.get("actor", ""),
        }))
