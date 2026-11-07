"""Extended Daphne runserver management command with --early-launch support
and client identity console logging (receival timestamp, enrollment number, name, and IP address).

Runs server against an isolated temporary database (temp_early_access.sqlite3) when --early-launch is specified.
Enriches Daphne console request logs with client connection details:
  [%(server_time)s] HTTP %(method)s %(path)s %(status)s [%(time_taken).2f, %(client)s, %(enrollment)s, %(name)s]
where unauthenticated requests display N.A. for enrollment and name.
"""

import datetime
import logging
import os
import sys
import time
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from daphne.management.commands.runserver import Command as DaphneRunserverCommand

from accounts.client_logger import (
    ClientLoggingASGIWrapper,
    pop_client_request,
    set_action_logger_active,
    get_request_color,
    colorize,
)


def _configure_channels_logger():
    """Ensure django.channels.server outputs to console regardless of DEBUG or external logging config."""
    srv_logger = logging.getLogger("django.channels.server")
    srv_logger.setLevel(logging.INFO)
    srv_logger.propagate = False
    if not any(isinstance(h, logging.StreamHandler) for h in srv_logger.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        srv_logger.addHandler(h)
    return srv_logger


logger = _configure_channels_logger()


class Command(DaphneRunserverCommand):
    """Runserver command supporting --early-launch and enriched client identity logging."""

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--early-launch',
            action='store_true',
            dest='early_launch',
            default=False,
            help='Run server in Early Launch Mode (temporary isolated database, untrusted self-declarations)',
        )
        parser.add_argument(
            '-w',
            '--workers',
            type=int,
            dest='workers',
            default=1,
            help='Number of concurrent Daphne ASGI worker processes (set > 1 to launch multi-worker cluster)',
        )
        parser.add_argument(
            '--database-url',
            type=str,
            default=None,
            dest='database_url',
            help='Custom Database connection URL',
        )
        parser.add_argument(
            '--redis-url',
            type=str,
            default=None,
            dest='redis_url',
            help='Custom Redis broker connection URL',
        )

    def handle(self, *args, **options):
        workers = options.get('workers', 1)
        if options.get('database_url'):
            os.environ['DATABASE_URL'] = options['database_url']
        if options.get('redis_url'):
            os.environ['REDIS_URL'] = options['redis_url']

        if workers and workers > 1:
            addrport = options.get('addrport') or ''
            addr_val = options.get('addr') or '0.0.0.0'
            port_val = options.get('port') or 8000
            if addrport:
                if ':' in addrport:
                    parts = addrport.split(':', 1)
                    addr_val = parts[0] or addr_val
                    port_val = parts[1] or port_val
                elif str(addrport).isdigit():
                    port_val = addrport
                else:
                    addr_val = addrport
            call_command(
                'run_cluster',
                workers=workers,
                port=int(port_val),
                base_port=int(port_val) + 1,
                bind=str(addr_val),
                early_launch=options.get('early_launch', False),
                database_url=options.get('database_url'),
                redis_url=options.get('redis_url'),
            )
            return

        if options.get('early_launch'):
            os.environ['CAMPUS_EARLY_LAUNCH_MODE'] = 'True'
            settings.EARLY_LAUNCH_MODE = True

            is_worker = os.environ.get('RUN_MAIN') == 'true' or not options.get('use_reloader', True)
            if is_worker:
                self.stdout.write(self.style.WARNING(
                    "\n" + "=" * 70 + "\n"
                    "CAMPUS NEXUS — EARLY LAUNCH MODE ACTIVE (POSTGRESQL PRODUCTION ENGINE)\n"
                    "  * Primary Database: PostgreSQL (campus_nexus)\n"
                    "  * Self-declared accounts are untrusted (verified=False).\n"
                    + "=" * 70 + "\n"
                ))

        _configure_channels_logger()
        set_action_logger_active(True)
        super().handle(*args, **options)

    def get_application(self, options):
        """Wrap Daphne's root ASGI application with ClientLoggingASGIWrapper to pre-seed client metadata."""
        app = super().get_application(options)
        return ClientLoggingASGIWrapper(app)

    def log_action(self, protocol, action, details):
        """Log requests to the console enriched with server receival timestamp, client IP, enrollment number, and name."""
        if protocol == "http" and action == "complete":
            client_key = details.get("client")
            meta = pop_client_request(client_key, path=details.get("path"))

            details["enrollment"] = meta.get("enrollment", "N.A.") or "N.A."
            details["name"] = meta.get("name", "N.A.") or "N.A."
            user_type = meta.get("user_type", "unregistered")
            real_client = meta.get("real_client")
            if real_client:
                details["client"] = real_client
            elif not details.get("client"):
                details["client"] = "127.0.0.1"

            # Determine timestamp at the exact moment the request was received
            time_taken = details.get("time_taken", 0.0) or 0.0
            received_ts = meta.get("received_at") or (time.time() - time_taken)
            import zoneinfo
            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
            details["server_time"] = datetime.datetime.fromtimestamp(received_ts, tz=ist_tz).strftime("%d/%b/%Y %H:%M:%S")

            status = details.get("status", 200)
            path = details.get("path", "")
            method = details.get("method", "GET")
            client = details.get("client", "127.0.0.1")
            enrollment = details["enrollment"]
            name = details["name"]

            msg = f"[{details['server_time']}] HTTP {method} {path} {status} [{time_taken:.2f}, {client}, {enrollment}, {name}]"
            color = get_request_color(status=status, path=path, user_type=user_type)
            colored_msg = colorize(msg, color)

            if status >= 500:
                logger.error(colored_msg)
            elif status >= 400:
                logger.warning(colored_msg)
            else:
                logger.info(colored_msg)

        elif protocol == "websocket" and action in ("connected", "disconnected", "connecting", "rejected"):
            client_key = details.get("client")
            meta = pop_client_request(client_key, path=details.get("path"), is_websocket=True, action=action)

            details["enrollment"] = meta.get("enrollment", "N.A.") or "N.A."
            details["name"] = meta.get("name", "N.A.") or "N.A."
            user_type = meta.get("user_type", "unregistered")
            real_client = meta.get("real_client")
            if real_client:
                details["client"] = real_client
            elif not details.get("client"):
                details["client"] = "127.0.0.1"

            received_ts = meta.get("received_at") or time.time()
            import zoneinfo
            ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
            details["server_time"] = datetime.datetime.fromtimestamp(received_ts, tz=ist_tz).strftime("%d/%b/%Y %H:%M:%S")

            action_name = {
                "connected": "CONNECT",
                "disconnected": "DISCONNECT",
                "connecting": "HANDSHAKING",
                "rejected": "REJECT",
            }.get(action, action.upper())

            msg = f"[{details['server_time']}] WebSocket {action_name} {details.get('path', '')} [{details['client']}, {details['enrollment']}, {details['name']}]"
            color = get_request_color(status=None, path=details.get("path"), user_type=user_type, action=action)
            colored_msg = colorize(msg, color)

            if action in ("rejected", "error"):
                logger.warning(colored_msg)
            else:
                logger.info(colored_msg)
        else:
            super().log_action(protocol, action, details)

    def _sync_superusers_to_temp_db(self):
        """Seed or copy administrative superusers into temporary DB for administrative access."""
        from accounts.models import User
        try:
            if 'permanent' in connections:
                perm_superusers = User.objects.using('permanent').filter(is_superuser=True)
                for su in perm_superusers:
                    if not User.objects.using('default').filter(enrollment_number=su.enrollment_number).exists():
                        User.objects.using('default').create(
                            username=su.username,
                            enrollment_number=su.enrollment_number,
                            password=su.password,
                            email=su.email,
                            first_name=su.first_name,
                            last_name=su.last_name,
                            phone_number=su.phone_number,
                            gender=su.gender,
                            is_superuser=True,
                            is_staff=True,
                            is_active=True,
                            verified=True,
                        )
        except Exception:
            pass
