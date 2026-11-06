"""Django management command to run the Multi-Worker ASGI Daphne Cluster.

Spawns multiple Daphne worker processes across CPU cores and proxies traffic on port 8000,
multiplying concurrent throughput by 4x-6x.

Usage:
    python manage.py run_cluster [--workers 4] [--port 8000] [--early-launch]
"""

import asyncio
import os
import signal
import sys
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run Campus Nexus Multi-Worker ASGI Cluster with Daphne"

    def add_arguments(self, parser):
        cpu_count = os.cpu_count() or 4
        default_workers = min(8, max(4, cpu_count // 2))

        parser.add_argument(
            "-w",
            "--workers",
            type=int,
            default=default_workers,
            dest="workers",
            help=f"Number of Daphne worker processes (default: {default_workers}, detected {cpu_count} CPU threads)",
        )
        parser.add_argument(
            "-p",
            "--port",
            type=int,
            default=8000,
            dest="port",
            help="Public proxy port (default: 8000)",
        )
        parser.add_argument(
            "-b",
            "--bind",
            type=str,
            default="0.0.0.0",
            dest="bind",
            help="Public proxy bind address (default: 0.0.0.0)",
        )
        parser.add_argument(
            "--base-port",
            type=int,
            default=8001,
            dest="base_port",
            help="Base port for Daphne worker processes (default: 8001)",
        )
        parser.add_argument(
            "--early-launch",
            action="store_true",
            default=False,
            dest="early_launch",
            help="Enable Early Launch Mode across all workers",
        )

    def handle(self, *args, **options):
        # Import MultiWorkerCluster from scripts
        from scripts.run_multiworker_cluster import MultiWorkerCluster

        settings_mod = os.environ.get("DJANGO_SETTINGS_MODULE", "campus.settings.dev")
        early_launch = options.get("early_launch") or getattr(settings, "EARLY_LAUNCH_MODE", False)

        cluster = MultiWorkerCluster(
            num_workers=options["workers"],
            bind_host=options["bind"],
            proxy_port=options["port"],
            worker_base_port=options["base_port"],
            early_launch=early_launch,
            settings_module=settings_mod,
        )

        def handle_signal(sig, frame):
            cluster.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_signal)
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, handle_signal)

        try:
            asyncio.run(cluster.run())
        except KeyboardInterrupt:
            cluster.shutdown()
