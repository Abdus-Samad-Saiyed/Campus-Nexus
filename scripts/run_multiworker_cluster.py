#!/usr/bin/env python
"""High-Performance Multi-Worker ASGI Cluster Supervisor & Load Balancer for Campus Nexus.

Utilizes all available CPU cores (e.g., Intel Core 7 240H) by running multiple Daphne ASGI
worker processes behind an asynchronous, zero-overhead reverse proxy listening on port 8000.

Usage:
    python scripts/run_multiworker_cluster.py [options]

Options:
    --workers, -w       Number of Daphne worker processes (default: auto, min(8, cpu_count))
    --port, -p          Public proxy listening port (default: 8000)
    --bind, -b          Public proxy bind address (default: 0.0.0.0)
    --base-port         Base port for worker subprocesses (default: 8001)
    --early-launch      Run all workers in Early Launch Mode (isolated temporary database)
    --settings          Django settings module (default: campus.settings.dev)
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional


class MultiWorkerCluster:
    def __init__(
        self,
        num_workers: int,
        bind_host: str = "0.0.0.0",
        proxy_port: int = 8000,
        worker_base_port: int = 8001,
        early_launch: bool = False,
        settings_module: str = "campus.settings.dev",
    ):
        self.num_workers = num_workers
        self.bind_host = bind_host
        self.proxy_port = proxy_port
        self.worker_base_port = worker_base_port
        self.early_launch = early_launch
        self.settings_module = settings_module

        self.worker_ports: List[int] = [
            self.worker_base_port + i for i in range(self.num_workers)
        ]
        self.workers: Dict[int, subprocess.Popen] = {}
        self.round_robin_counter = 0
        self.shutdown_event = asyncio.Event()
        self.server: Optional[asyncio.Server] = None
        self.redis_proc: Optional[subprocess.Popen] = None

    def _ensure_redis(self):
        """Verify Redis is active; auto-launch local redis-server if available."""
        import socket
        import shutil
        from pathlib import Path

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15)
        try:
            s.connect(('127.0.0.1', 6379))
            s.close()
            print("[MultiWorkerCluster] Redis channel broker active on 127.0.0.1:6379 (WebSockets synchronized across all workers).")
            return
        except Exception:
            pass

        # Locate local redis-server binary
        redis_bin = shutil.which("redis-server") or shutil.which("redis-server.exe")
        if not redis_bin:
            winget_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
            matches = list(winget_dir.glob("**/redis-server.exe"))
            if matches:
                redis_bin = str(matches[0])

        if redis_bin and Path(redis_bin).exists():
            try:
                print(f"[MultiWorkerCluster] Auto-launching local Redis broker ({redis_bin})...")
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                self.redis_proc = subprocess.Popen(
                    [redis_bin, "--port", "6379", "--daemonize", "no", "--save", "", "--appendonly", "no"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                time.sleep(0.5)
                print("[MultiWorkerCluster] Redis broker started successfully on 127.0.0.1:6379.")
            except Exception as e:
                print(f"[MultiWorkerCluster] Warning: Could not auto-launch Redis: {e}")
        else:
            print("[MultiWorkerCluster] Warning: Redis is not running on 127.0.0.1:6379. WebSockets will fallback to in-memory mode.")

    def _get_worker_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["DJANGO_SETTINGS_MODULE"] = self.settings_module
        env.setdefault(
            "REDIS_URL",
            "redis://127.0.0.1:6379/0?protocol=2&socket_timeout=15&socket_connect_timeout=5",
        )
        env.setdefault(
            "DATABASE_URL",
            "postgres://campus_admin:CampusPass123!@127.0.0.1:5432/campus_nexus",
        )
        env.setdefault(
            "GUEST_DEMO_DATABASE_URL",
            "postgres://campus_admin:CampusPass123!@127.0.0.1:5432/campus_guest_demo",
        )
        if self.early_launch:
            env["CAMPUS_EARLY_LAUNCH_MODE"] = "True"
            env["EARLY_LAUNCH_MODE"] = "True"
        return env

    def spawn_worker(self, port: int) -> subprocess.Popen:
        """Spawn a dedicated Daphne ASGI worker process bound to 127.0.0.1:port."""
        python_exe = sys.executable
        cmd = [
            python_exe,
            "-m",
            "daphne",
            "-b",
            "127.0.0.1",
            "-p",
            str(port),
            "--access-log",
            "",
            "--proxy-headers",
            "campus.asgi:application",
        ]

        # Use creationflags on Windows if available to isolate process group
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(
            cmd,
            env=self._get_worker_env(),
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=creationflags,
        )
        self.workers[port] = proc
        return proc

    async def _forward_stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Asynchronously pipe bytes from reader to writer with 64KB chunks."""
        try:
            while not self.shutdown_event.is_set():
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_connection(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ):
        """Proxy incoming TCP connection (HTTP/WebSocket) to next Daphne worker in round-robin."""
        if not self.worker_ports:
            client_writer.close()
            return

        worker_port = self.worker_ports[self.round_robin_counter % len(self.worker_ports)]
        self.round_robin_counter += 1

        try:
            worker_reader, worker_writer = await asyncio.open_connection(
                "127.0.0.1", worker_port
            )
        except Exception:
            # Fallback to alternative worker if selected worker is briefly unresponsive
            fallback_ports = [p for p in self.worker_ports if p != worker_port]
            connected = False
            for fp in fallback_ports:
                try:
                    worker_reader, worker_writer = await asyncio.open_connection("127.0.0.1", fp)
                    connected = True
                    break
                except Exception:
                    continue
            if not connected:
                client_writer.close()
                return

        # Bi-directional stream forwarding
        t1 = asyncio.create_task(self._forward_stream(client_reader, worker_writer))
        t2 = asyncio.create_task(self._forward_stream(worker_reader, client_writer))
        await asyncio.gather(t1, t2, return_exceptions=True)

    async def _monitor_workers(self):
        """Continuously check that worker processes remain healthy; auto-restart if a worker terminates."""
        while not self.shutdown_event.is_set():
            await asyncio.sleep(2.0)
            for port in list(self.worker_ports):
                proc = self.workers.get(port)
                if proc is not None and proc.poll() is not None:
                    if not self.shutdown_event.is_set():
                        print(
                            f"\n[MultiWorkerCluster] WARNING: Daphne worker on port {port} exited with code {proc.returncode}. Restarting worker..."
                        )
                        self.spawn_worker(port)

    def shutdown(self):
        """Gracefully stop all worker processes and proxy server."""
        print("\n[MultiWorkerCluster] Shutting down multi-worker ASGI cluster...")
        self.shutdown_event.set()

        for port, proc in list(self.workers.items()):
            try:
                if proc.poll() is None:
                    if sys.platform == "win32":
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        proc.terminate()
            except Exception:
                pass

        # Give processes a moment to terminate gracefully
        time.sleep(0.5)
        for port, proc in list(self.workers.items()):
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
        self.workers.clear()
        if self.redis_proc is not None:
            try:
                if self.redis_proc.poll() is None:
                    self.redis_proc.terminate()
            except Exception:
                pass
            self.redis_proc = None
        print("[MultiWorkerCluster] All worker processes terminated.")

    async def run(self):
        """Start workers and run load-balancing proxy."""
        print("=" * 70)
        print("  CAMPUS NEXUS — MULTI-WORKER ASGI ARCHITECTURE")
        print("=" * 70)
        print(f"  * Public Listening Address : http://{self.bind_host}:{self.proxy_port}/")
        print(f"  * Total Active ASGI Workers: {self.num_workers} processes")
        print(f"  * Worker Ports             : {', '.join(str(p) for p in self.worker_ports)}")
        print(f"  * Settings Module          : {self.settings_module}")
        print(f"  * Early Launch Mode        : {'ENABLED' if self.early_launch else 'DISABLED'}")
        print("=" * 70)

        # Pre-migrate if in Early Launch Mode
        if self.early_launch:
            print("[MultiWorkerCluster] Running migrations for temporary database before worker startup...")
            try:
                subprocess.run(
                    [
                        sys.executable,
                        "manage.py",
                        "migrate",
                        "--database=default",
                        "--no-input",
                        f"--settings={self.settings_module}",
                    ],
                    env=self._get_worker_env(),
                    check=False,
                )
            except Exception as e:
                print(f"[MultiWorkerCluster] Pre-migration notice: {e}")

        # Ensure Redis is running for multi-worker WebSockets
        self._ensure_redis()

        # Start Daphne workers
        for port in self.worker_ports:
            print(f"[MultiWorkerCluster] Starting Daphne worker on 127.0.0.1:{port}...")
            self.spawn_worker(port)

        # Wait briefly for workers to bind sockets
        await asyncio.sleep(1.0)

        # Start Proxy Server
        self.server = await asyncio.start_server(
            self._handle_connection, self.bind_host, self.proxy_port
        )
        print(
            f"[MultiWorkerCluster] Load-balancing proxy active on {self.bind_host}:{self.proxy_port}."
        )
        print("[MultiWorkerCluster] Ready to accept concurrent requests. Press CTRL+C to quit.\n")

        monitor_task = asyncio.create_task(self._monitor_workers())

        try:
            async with self.server:
                await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            monitor_task.cancel()
            self.shutdown()


def main():
    cpu_count = os.cpu_count() or 4
    default_workers = min(8, max(4, cpu_count // 2))

    parser = argparse.ArgumentParser(
        description="Run Campus Nexus Multi-Worker ASGI Cluster with Daphne",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=default_workers,
        help=f"Number of Daphne worker processes (default: {default_workers}, detected {cpu_count} CPU threads)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=8000,
        help="Public proxy port (default: 8000)",
    )
    parser.add_argument(
        "-b",
        "--bind",
        type=str,
        default="0.0.0.0",
        help="Public proxy bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--base-port",
        type=int,
        default=8001,
        help="Base port for Daphne worker processes (default: 8001)",
    )
    parser.add_argument(
        "--early-launch",
        action="store_true",
        default=False,
        help="Enable Early Launch Mode across all workers",
    )
    parser.add_argument(
        "--settings",
        type=str,
        default="campus.settings.dev",
        help="Django settings module (default: campus.settings.dev)",
    )

    args = parser.parse_args()

    cluster = MultiWorkerCluster(
        num_workers=args.workers,
        bind_host=args.bind,
        proxy_port=args.port,
        worker_base_port=args.base_port,
        early_launch=args.early_launch,
        settings_module=args.settings,
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


if __name__ == "__main__":
    main()
