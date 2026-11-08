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
        database_url: Optional[str] = None,
        redis_url: Optional[str] = None,
    ):
        self.num_workers = num_workers
        self.bind_host = bind_host
        self.proxy_port = proxy_port
        self.worker_base_port = worker_base_port
        self.early_launch = early_launch
        self.settings_module = settings_module
        self.database_url = database_url
        self.redis_url = redis_url

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
        import urllib.parse
        from pathlib import Path

        r_url = self.redis_url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        try:
            parsed = urllib.parse.urlparse(r_url)
            redis_host = parsed.hostname or "127.0.0.1"
            redis_port = parsed.port or 6379
        except Exception:
            redis_host = "127.0.0.1"
            redis_port = 6379

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            s.connect((redis_host, redis_port))
            s.close()
            print(f"[MultiWorkerCluster] Redis channel broker active on {redis_host}:{redis_port} (WebSockets synchronized across all workers).")
            return
        except Exception:
            pass

        if redis_host not in ("127.0.0.1", "localhost"):
            print(f"[MultiWorkerCluster] Warning: Remote Redis broker at {redis_host}:{redis_port} not reachable.")
            return

        # Locate local redis-server binary
        redis_bin = shutil.which("redis-server") or shutil.which("redis-server.exe")
        if not redis_bin:
            winget_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
            matches = list(winget_dir.glob("**/redis-server.exe"))
            if matches:
                redis_bin = str(matches[0])

        if redis_bin and Path(redis_bin).exists():
            try:
                print(f"[MultiWorkerCluster] Auto-launching local Redis broker ({redis_bin}) on port {redis_port}...")
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                self.redis_proc = subprocess.Popen(
                    [redis_bin, "--port", str(redis_port), "--daemonize", "no", "--save", "", "--appendonly", "no"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                time.sleep(0.5)
                print(f"[MultiWorkerCluster] Redis broker started successfully on {redis_host}:{redis_port}.")
            except Exception as e:
                print(f"[MultiWorkerCluster] Warning: Could not auto-launch Redis: {e}")
        else:
            print(f"[MultiWorkerCluster] Warning: Redis is not running on {redis_host}:{redis_port}. WebSockets will fallback to in-memory mode.")

    def _get_worker_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["DJANGO_SETTINGS_MODULE"] = self.settings_module
        if self.redis_url:
            env["REDIS_URL"] = self.redis_url
        if self.database_url:
            env["DATABASE_URL"] = self.database_url
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
        os.environ.setdefault("CONN_MAX_AGE", "0")
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
            "-v",
            "0",
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
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.workers[port] = proc
        return proc

    def _is_port_occupied(self, port: int) -> bool:
        """Check if port is currently in use."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _cleanup_stale_ports(self):
        """Clean up any orphan Daphne processes listening on worker ports before launch."""
        for port in self.worker_ports:
            if self._is_port_occupied(port):
                print(f"[MultiWorkerCluster] Cleaning up stale listener on worker port 127.0.0.1:{port}...")
                if sys.platform == "win32":
                    try:
                        output = subprocess.check_output(f'netstat -ano | findstr ":{port} "', shell=True, text=True)
                        for line in output.splitlines():
                            parts = line.strip().split()
                            if len(parts) >= 5 and "LISTENING" in parts:
                                pid = int(parts[-1])
                                if pid != os.getpid():
                                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
                time.sleep(0.2)

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
                if not writer.is_closing():
                    writer.close()
            except Exception:
                pass

    async def _handle_connection(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ):
        """Proxy incoming TCP connection (HTTP/WebSocket) to next Daphne worker in round-robin."""
        if not self.worker_ports:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass
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
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass
                return

        # Bi-directional stream forwarding
        t1 = asyncio.create_task(self._forward_stream(client_reader, worker_writer))
        t2 = asyncio.create_task(self._forward_stream(worker_reader, client_writer))

        # When either direction finishes (EOF, disconnect), cancel the other immediately
        done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        for w in (client_writer, worker_writer):
            try:
                if not w.is_closing():
                    w.close()
                await w.wait_closed()
            except Exception:
                pass

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
        os.environ.setdefault("CONN_MAX_AGE", "0")
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

        # 1. Early port conflict check: prevent starting workers if proxy port is already occupied
        if self._is_port_occupied(self.proxy_port):
            print(f"\n[MultiWorkerCluster] ERROR: Port {self.proxy_port} is already in use by another running server instance.")
            print(f"[MultiWorkerCluster] Please stop the existing server on {self.bind_host}:{self.proxy_port} before starting a new cluster.\n")
            raise OSError(10048, f"Address ({self.bind_host}, {self.proxy_port}) is already in use.")

        # 2. Clean up any stale/orphaned Daphne worker processes
        self._cleanup_stale_ports()

        # Ensure Redis is running for multi-worker WebSockets
        self._ensure_redis()

        # Start Daphne workers
        for port in self.worker_ports:
            print(f"[MultiWorkerCluster] Starting Daphne worker on 127.0.0.1:{port}...")
            self.spawn_worker(port)

        # Wait briefly for workers to bind sockets
        await asyncio.sleep(1.0)

        # Start Proxy Server with exception-safe fallback
        try:
            self.server = await asyncio.start_server(
                self._handle_connection, self.bind_host, self.proxy_port
            )
        except Exception as exc:
            print(f"\n[MultiWorkerCluster] ERROR: Failed to bind proxy on {self.bind_host}:{self.proxy_port}: {exc}")
            self.shutdown()
            raise
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
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Custom Database connection URL (e.g. postgres://user:pass@host:5432/db)",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=None,
        help="Custom Redis broker connection URL (e.g. redis://host:6379/0)",
    )
    parser.add_argument("--pg-host", type=str, default=None, help="PostgreSQL host")
    parser.add_argument("--pg-port", type=int, default=None, help="PostgreSQL port")
    parser.add_argument("--pg-db", type=str, default=None, help="PostgreSQL database name")
    parser.add_argument("--pg-user", type=str, default=None, help="PostgreSQL user")
    parser.add_argument("--pg-password", type=str, default=None, help="PostgreSQL password")
    parser.add_argument("--redis-host", type=str, default=None, help="Redis host")
    parser.add_argument("--redis-port", type=int, default=None, help="Redis port")
    parser.add_argument("--redis-db", type=int, default=None, help="Redis db index")

    args = parser.parse_args()

    db_url = args.database_url
    if not db_url and (args.pg_host or args.pg_port or args.pg_db):
        host = args.pg_host or "127.0.0.1"
        port = args.pg_port or 5432
        db = args.pg_db or "campus_nexus"
        user = args.pg_user or "campus_admin"
        password = args.pg_password or "CampusPass123!"
        db_url = f"postgres://{user}:{password}@{host}:{port}/{db}"

    r_url = args.redis_url
    if not r_url and (args.redis_host or args.redis_port or args.redis_db is not None):
        r_host = args.redis_host or "127.0.0.1"
        r_port = args.redis_port or 6379
        r_db = args.redis_db if args.redis_db is not None else 0
        r_url = f"redis://{r_host}:{r_port}/{r_db}?protocol=2&socket_timeout=15&socket_connect_timeout=5"

    cluster = MultiWorkerCluster(
        num_workers=args.workers,
        bind_host=args.bind,
        proxy_port=args.port,
        worker_base_port=args.base_port,
        early_launch=args.early_launch,
        settings_module=args.settings,
        database_url=db_url,
        redis_url=r_url,
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
