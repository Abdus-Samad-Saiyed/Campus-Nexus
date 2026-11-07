"""
Campus Nexus — Service Binder & Health Verification
Ensures PostgreSQL 18.6 and Redis channel layer services are running,
environment variables are properly bound, and database migrations are up to date.
"""

import os
import sys
import socket
import subprocess
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

def check_socket(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def ensure_postgresql():
    print("[POSTGRESQL] Checking PostgreSQL service on 127.0.0.1:5432...", end=" ", flush=True)
    if check_socket("127.0.0.1", 5432):
        print("ONLINE [OK]")
        return True
    
    print("OFFLINE. Attempting to start Windows service 'postgresql-x64-18'...")
    try:
        res = subprocess.run(["net", "start", "postgresql-x64-18"], capture_output=True, text=True)
        time.sleep(2)
        if check_socket("127.0.0.1", 5432):
            print("[POSTGRESQL] Started and responding on port 5432 [OK]")
            return True
        else:
            print(f"[POSTGRESQL] Failed to bind: {res.stdout or res.stderr}")
            return False
    except Exception as e:
        print(f"[POSTGRESQL] Error starting service: {e}")
        return False

def ensure_redis():
    print("[REDIS] Checking Redis server on 127.0.0.1:6379...", end=" ", flush=True)
    if check_socket("127.0.0.1", 6379):
        print("ONLINE [OK]")
        return True

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
        print("[REDIS] Warning: redis-server.exe not found in common paths. Channels will fall back or error if offline.")
        return False

    try:
        print(f"[REDIS] Launching background daemon: {redis_exe}...")
        subprocess.Popen([redis_exe, "--port", "6379", "--daemonize", "no", "--save", "", "--appendonly", "no"],
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        if check_socket("127.0.0.1", 6379):
            print("[REDIS] Started and responding on port 6379 [OK]")
            return True
        else:
            print("[REDIS] Started process, but socket not yet answering.")
            return False
    except Exception as e:
        print(f"[REDIS] Error launching redis-server: {e}")
        return False

def apply_migrations():
    print("[MIGRATIONS] Running schema verification and migrations...")
    python_exe = sys.executable
    manage_py = os.path.join(PROJECT_ROOT, "manage.py")
    env = os.environ.copy()
    env["DJANGO_DEBUG"] = "True"
    res = subprocess.run([python_exe, manage_py, "migrate", "--no-input"], cwd=PROJECT_ROOT, env=env)
    return res.returncode == 0

def bind_and_verify_all():
    print("=" * 60)
    print(" Campus Nexus — Services & Infrastructure Health Check")
    print("=" * 60)
    pg_ok = ensure_postgresql()
    redis_ok = ensure_redis()
    
    if pg_ok:
        mig_ok = apply_migrations()
    else:
        mig_ok = False
        
    print("-" * 60)
    if pg_ok and redis_ok and mig_ok:
        print("[SUCCESS] All database and broker instances are bound and healthy!")
        return True
    else:
        print("[WARNING] One or more components reported warnings. Review output above.")
        return False

if __name__ == "__main__":
    success = bind_and_verify_all()
    sys.exit(0 if success else 1)
