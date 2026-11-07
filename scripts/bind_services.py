"""
Campus Nexus — Service Binder & Health Verification
Ensures PostgreSQL, Redis channel broker, and Cloudflare Tunnel services are running
on their configured deployment sockets, environment variables are properly bound,
and database migrations are up to date.
"""

import argparse
import os
import sys
import socket
import subprocess
import time
import urllib.parse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def check_socket(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def is_process_running(proc_name: str) -> bool:
    try:
        res = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {proc_name}"], capture_output=True, text=True)
        return proc_name.lower() in res.stdout.lower()
    except Exception:
        return False


def get_default_pg_socket():
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        if db_url.startswith("sqlite"):
            return "sqlite", 0
        try:
            p = urllib.parse.urlparse(db_url)
            return p.hostname or "127.0.0.1", p.port or 5432
        except Exception:
            pass
    return "127.0.0.1", 5432


def get_default_redis_socket():
    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        try:
            p = urllib.parse.urlparse(redis_url)
            return p.hostname or "127.0.0.1", p.port or 6379
        except Exception:
            pass
    return "127.0.0.1", 6379


def ensure_postgresql(host: str = None, port: int = None):
    if not host or not port:
        host, port = get_default_pg_socket()

    if host == "sqlite":
        print("[DATABASE] Using SQLite local database engine [OK]")
        return True

    print(f"[POSTGRESQL] Checking PostgreSQL service on {host}:{port}...", end=" ", flush=True)
    if check_socket(host, port):
        print("ONLINE [OK]")
        return True

    if host in ("127.0.0.1", "localhost"):
        print("OFFLINE. Attempting to start Windows service 'postgresql-x64-18'...")
        try:
            res = subprocess.run(["net", "start", "postgresql-x64-18"], capture_output=True, text=True)
            time.sleep(2)
            if check_socket(host, port):
                print(f"[POSTGRESQL] Started and responding on port {port} [OK]")
                return True
            else:
                print(f"[POSTGRESQL] Failed to bind: {res.stdout or res.stderr}")
                return False
        except Exception as e:
            print(f"[POSTGRESQL] Error starting service: {e}")
            return False
    else:
        print(f"OFFLINE. Remote PostgreSQL socket {host}:{port} unreachable.")
        return False


def ensure_redis(host: str = None, port: int = None):
    if not host or not port:
        host, port = get_default_redis_socket()

    print(f"[REDIS] Checking Redis server on {host}:{port}...", end=" ", flush=True)
    if check_socket(host, port):
        print("ONLINE [OK]")
        return True

    if host not in ("127.0.0.1", "localhost"):
        print(f"OFFLINE. Remote Redis socket {host}:{port} unreachable.")
        return False

    print("OFFLINE. Searching for redis-server.exe...")
    redis_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\taizod1024.redis-windows-fork_Microsoft.Winget.Source_8wekyb3d8bbwe\redis-server.exe"),
        r"C:\Program Files\Redis\redis-server.exe",
        "redis-server.exe"
    ]

    redis_exe = None
    for p in redis_paths:
        if os.path.exists(p):
            redis_exe = p
            break

    if not redis_exe:
        print(f"[REDIS] Warning: redis-server.exe not found. Channels will fall back if offline on {host}:{port}.")
        return False

    try:
        print(f"[REDIS] Launching background daemon on port {port}: {redis_exe}...")
        subprocess.Popen(
            [redis_exe, "--port", str(port), "--daemonize", "no", "--save", "", "--appendonly", "no"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(1.5)
        if check_socket(host, port):
            print(f"[REDIS] Started and responding on port {port} [OK]")
            return True
        else:
            print(f"[REDIS] Started process, but socket {host}:{port} not answering yet.")
            return False
    except Exception as e:
        print(f"[REDIS] Error launching redis-server: {e}")
        return False


def ensure_cloudflared(daphne_port: int = 8000):
    print("[CLOUDFLARE] Checking Cloudflare Tunnel (cloudflared)...", end=" ", flush=True)
    if is_process_running("cloudflared.exe"):
        print("ONLINE [OK]")
        return True

    # 1. Attempt starting Windows service 'Cloudflared'
    try:
        subprocess.run(["net", "start", "Cloudflared"], capture_output=True, text=True)
        time.sleep(2)
        if is_process_running("cloudflared.exe"):
            print("Started Windows service 'Cloudflared' [OK]")
            return True
    except Exception:
        pass

    # 2. Search for cloudflared executable & token
    token_file = r"C:\ProgramData\cloudflared\token"
    cf_paths = [
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        os.path.expandvars(r"%USERPROFILE%\Downloads\cloudflared.exe"),
        "cloudflared.exe"
    ]
    cf_bin = None
    for p in cf_paths:
        if os.path.exists(p):
            cf_bin = p
            break

    if not cf_bin:
        print("[CLOUDFLARE] Warning: cloudflared.exe not found. Public edge tunnel will remain offline.")
        return False

    try:
        print(f"[CLOUDFLARE] Launching background tunnel: {cf_bin}...")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        if os.path.exists(token_file):
            cmd = [cf_bin, "tunnel", "run", "--token-file", token_file]
        else:
            cmd = [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{daphne_port}"]

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags
        )
        time.sleep(2)
        if is_process_running("cloudflared.exe"):
            print("[CLOUDFLARE] Started tunnel daemon successfully [OK]")
            return True
        else:
            print("[CLOUDFLARE] Tunnel process spawned but not yet responding.")
            return False
    except Exception as e:
        print(f"[CLOUDFLARE] Error launching tunnel: {e}")
        return False


def apply_migrations():
    print("[MIGRATIONS] Running schema verification and migrations...")
    python_exe = sys.executable
    manage_py = os.path.join(PROJECT_ROOT, "manage.py")
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "True"
    res = subprocess.run([python_exe, manage_py, "migrate", "--no-input"], cwd=PROJECT_ROOT, env=env)
    return res.returncode == 0


def bind_and_verify_all(pg_host=None, pg_port=None, redis_host=None, redis_port=None, daphne_port=8000, start_tunnel=True):
    print("=" * 60)
    print(" Campus Nexus — Services & Infrastructure Health Check")
    print("=" * 60)
    pg_ok = ensure_postgresql(pg_host, pg_port)
    redis_ok = ensure_redis(redis_host, redis_port)
    cf_ok = ensure_cloudflared(daphne_port) if start_tunnel else True

    if pg_ok:
        mig_ok = apply_migrations()
    else:
        mig_ok = False

    print("-" * 60)
    if pg_ok and redis_ok and cf_ok and mig_ok:
        print("[SUCCESS] All infrastructure instances (PostgreSQL, Redis, Cloudflared) are bound & healthy!")
        return True
    else:
        print("[WARNING] One or more components reported warnings. Review output above.")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Campus Nexus Service Binder")
    parser.add_argument("--pg-host", type=str, default=None, help="PostgreSQL host")
    parser.add_argument("--pg-port", type=int, default=None, help="PostgreSQL port")
    parser.add_argument("--redis-host", type=str, default=None, help="Redis host")
    parser.add_argument("--redis-port", type=int, default=None, help="Redis port")
    parser.add_argument("--daphne-port", type=int, default=8000, help="Daphne port for cloudflared tunnel")
    parser.add_argument("--no-tunnel", action="store_true", help="Skip cloudflared tunnel start")
    args = parser.parse_args()

    success = bind_and_verify_all(
        pg_host=args.pg_host,
        pg_port=args.pg_port,
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        daphne_port=args.daphne_port,
        start_tunnel=not args.no_tunnel,
    )
    sys.exit(0 if success else 1)
