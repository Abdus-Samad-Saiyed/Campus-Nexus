"""
Campus Nexus - Interactive Python Server Orchestration Launcher (v2.1)
Cross-platform interactive launcher and supervisor with:
  * Deployment socket configurator for Daphne (HTTP/WS), PostgreSQL, and Redis
  * Multi-worker process scaling (1 to 32 Daphne ASGI workers)
  * Automated infrastructure orchestration (PostgreSQL, Redis, Cloudflare Tunnel)
  * Deployment profile persistence and parity audit suites.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
from typing import Dict, Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

PYTHON_EXE = sys.executable
PROFILES_FILE = os.path.join(PROJECT_ROOT, "scripts", "launch_profiles.json")


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def check_socket(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def is_process_running(proc_name: str) -> bool:
    if sys.platform == "win32":
        try:
            res = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {proc_name}"], capture_output=True, text=True)
            return proc_name.lower() in res.stdout.lower()
        except Exception:
            return False
    else:
        try:
            res = subprocess.run(["pgrep", "-f", proc_name], capture_output=True, text=True)
            return bool(res.stdout.strip())
        except Exception:
            return False


def get_active_profile() -> Dict[str, Any]:
    default_profile = {
        "name": "Production Multi-Worker Cluster (Recommended)",
        "daphne_host": "0.0.0.0",
        "daphne_port": 8000,
        "daphne_workers": 8,
        "worker_base_port": 8001,
        "pg_host": "127.0.0.1",
        "pg_port": 5432,
        "pg_db": "campus_nexus",
        "pg_user": "campus_admin",
        "pg_password": "CampusPass123!",
        "redis_host": "127.0.0.1",
        "redis_port": 6379,
        "redis_db": 0,
        "debug": False,
        "early_launch": False,
    }
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            active_name = data.get("active_profile", "production")
            if active_name in data.get("profiles", {}):
                return {"key": active_name, "data": data["profiles"][active_name], "all": data}
        except Exception:
            pass
    return {"key": "production", "data": default_profile, "all": None}


def save_active_profile(profile_key: str, profile_data: Dict[str, Any]) -> bool:
    try:
        data = {"active_profile": profile_key, "profiles": {}}
        if os.path.exists(PROFILES_FILE):
            try:
                with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data["active_profile"] = profile_key
        if "profiles" not in data:
            data["profiles"] = {}
        data["profiles"][profile_key] = profile_data
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"[WARNING] Could not save profile: {e}")
        return False


def wait_for_keypress(prompt="Press any key to return to menu..."):
    print(f"\n{prompt}", end="", flush=True)
    if sys.platform == "win32":
        try:
            import msvcrt
            msvcrt.getch()
            print()
            return
        except Exception:
            pass
    try:
        input()
    except Exception:
        pass


def show_banner():
    clear_screen()
    prof = get_active_profile()
    p = prof["data"]

    pg_online = check_socket(p["pg_host"], int(p["pg_port"]))
    redis_online = check_socket(p["redis_host"], int(p["redis_port"]))
    cf_online = is_process_running("cloudflared.exe") or is_process_running("cloudflared")

    print("=" * 80)
    print("                    CAMPUS NEXUS - SERVER ORCHESTRATION (v2.1)")
    print("=" * 80)
    print(f"  Profile:   {p.get('name', 'Custom')} [{prof['key']}]")
    print(f"  Daphne:    {p['daphne_host']}:{p['daphne_port']} ({p['daphne_workers']} Workers on ports {p['worker_base_port']}..{int(p['worker_base_port']) + int(p['daphne_workers']) - 1})")
    
    pg_status = "[ONLINE]" if pg_online else "[OFFLINE - Socket Unreachable]"
    print(f"  Database:  PostgreSQL {p['pg_host']}:{p['pg_port']}/{p['pg_db']} {pg_status}")

    redis_status = "[ONLINE]" if redis_online else "[OFFLINE - Socket Unreachable]"
    print(f"  Channels:  Redis Broker {p['redis_host']}:{p['redis_port']}/{p['redis_db']} {redis_status}")

    cf_status = "[ONLINE]" if cf_online else "[OFFLINE - Auto-starts on launch]"
    print(f"  Tunnel:    Cloudflare Anycast (dev.campus-nexus.online) {cf_status}")

    print(f"  Runtime:   {PYTHON_EXE} (Django 6.0, Python 3.14)")
    print("=" * 80)


def ensure_infrastructure(pg_host="127.0.0.1", pg_port=5432, redis_host="127.0.0.1", redis_port=6379, daphne_port=8000):
    print(f"\n[INFRASTRUCTURE] Verifying daemons (PostgreSQL, Redis, Cloudflared) on {pg_host}:{pg_port}, {redis_host}:{redis_port}, Port {daphne_port}...")
    script_path = os.path.join(PROJECT_ROOT, "scripts", "bind_services.py")
    res = subprocess.run([
        PYTHON_EXE, script_path,
        "--pg-host", str(pg_host),
        "--pg-port", str(pg_port),
        "--redis-host", str(redis_host),
        "--redis-port", str(redis_port),
        "--daphne-port", str(daphne_port)
    ])
    if res.returncode != 0:
        print("\n[WARNING] Infrastructure verification returned non-zero. Continuing anyway...")


def execute_server(cmd_args, env_map, pg_host="127.0.0.1", pg_port=5432, redis_host="127.0.0.1", redis_port=6379, daphne_port=8000):
    try:
        ensure_infrastructure(pg_host, pg_port, redis_host, redis_port, daphne_port)
        res = subprocess.run(cmd_args, env=env_map)
        if res.returncode != 0:
            print(f"\n[ERROR] Server process terminated with exit code {res.returncode}.")
            wait_for_keypress()
        else:
            print("\n[INFO] Server stopped.")
            wait_for_keypress()
    except (KeyboardInterrupt, EOFError):
        print("\n[INFO] Interrupted by user.")
        wait_for_keypress()
    except Exception as exc:
        print(f"\n[FATAL ERROR] An unexpected error occurred: {exc}")
        wait_for_keypress()


def configure_custom_deployment():
    clear_screen()
    print("=" * 80)
    print("         CAMPUS NEXUS — CUSTOM DEPLOYMENT SOCKET & WORKER CONFIGURATOR")
    print("=" * 80)
    print("Configure deployment sockets (IP:Port), worker processes, and database settings.\n")

    active = get_active_profile()["data"]
    cpu_cores = os.cpu_count() or 4

    print("--- [1] DAPHNE ASGI / HTTP / WEBSOCKET DEPLOYMENT SOCKET ---")
    d_host = input(f"  Public Bind Host [default: {active['daphne_host']}]: ").strip() or active['daphne_host']
    d_port_in = input(f"  Public Proxy Port [default: {active['daphne_port']}]: ").strip()
    d_port = int(d_port_in) if d_port_in else active['daphne_port']
    d_workers_in = input(f"  Daphne Worker Count (System has {cpu_cores} threads) [default: {active['daphne_workers']}]: ").strip()
    d_workers = int(d_workers_in) if d_workers_in else active['daphne_workers']
    d_base_in = input(f"  Worker Internal Base Port [default: {active['worker_base_port']}]: ").strip()
    d_base = int(d_base_in) if d_base_in else active['worker_base_port']

    print("\n--- [2] POSTGRESQL DATABASE DEPLOYMENT SOCKET ---")
    pg_host = input(f"  PostgreSQL Host [default: {active['pg_host']}]: ").strip() or active['pg_host']
    pg_port_in = input(f"  PostgreSQL Port [default: {active['pg_port']}]: ").strip()
    pg_port = int(pg_port_in) if pg_port_in else active['pg_port']
    pg_db = input(f"  Database Name [default: {active['pg_db']}]: ").strip() or active['pg_db']
    pg_user = input(f"  Database Username [default: {active['pg_user']}]: ").strip() or active['pg_user']
    pg_pass = input(f"  Database Password [default: {active['pg_password']}]: ").strip() or active['pg_password']

    print("\n--- [3] REDIS CHANNEL BROKER SOCKET ---")
    r_host = input(f"  Redis Host [default: {active['redis_host']}]: ").strip() or active['redis_host']
    r_port_in = input(f"  Redis Port [default: {active['redis_port']}]: ").strip()
    r_port = int(r_port_in) if r_port_in else active['redis_port']
    r_db_in = input(f"  Redis DB Index [default: {active['redis_db']}]: ").strip()
    r_db = int(r_db_in) if r_db_in else active['redis_db']

    print("\n--- [4] RUNTIME FLAGS ---")
    debug_in = input("  Enable Debug Traceable Mode? (y/N) [default: N]: ").strip().lower()
    is_debug = debug_in.startswith("y")
    early_in = input("  Enable Early Launch Mode (--early-launch)? (y/N) [default: N]: ").strip().lower()
    is_early = early_in.startswith("y")

    # Live Socket Probing
    print("\n[PROBING] Testing connection to configured sockets...")
    pg_ok = check_socket(pg_host, pg_port)
    redis_ok = check_socket(r_host, r_port)
    print(f"  * PostgreSQL Socket ({pg_host}:{pg_port}): {'CONNECTED [OK]' if pg_ok else 'OFFLINE / UNREACHABLE [WARN]'}")
    print(f"  * Redis Broker Socket ({r_host}:{r_port}): {'CONNECTED [OK]' if redis_ok else 'OFFLINE / UNREACHABLE [WARN]'}")
    print(f"  * Daphne Deployment Socket: {d_host}:{d_port} ({d_workers} Workers on ports {d_base}..{d_base + d_workers - 1})\n")

    print("What would you like to do?")
    print("  [L] Launch Cluster with these parameters")
    print("  [S] Save Profile and Launch")
    print("  [B] Return to Main Menu")

    action = input("\nEnter selection [L/S/B]: ").strip().lower()
    if action == "b":
        return

    profile_obj = {
        "name": f"Custom Deployment ({d_host}:{d_port}, {d_workers} Workers)",
        "daphne_host": d_host,
        "daphne_port": d_port,
        "daphne_workers": d_workers,
        "worker_base_port": d_base,
        "pg_host": pg_host,
        "pg_port": pg_port,
        "pg_db": pg_db,
        "pg_user": pg_user,
        "pg_password": pg_pass,
        "redis_host": r_host,
        "redis_port": r_port,
        "redis_db": r_db,
        "debug": is_debug,
        "early_launch": is_early,
    }

    if action == "s":
        p_name = input("Enter profile name to save as [default: custom]: ").strip() or "custom"
        save_active_profile(p_name, profile_obj)
        print(f"Profile '{p_name}' saved as active configuration!")

    env = os.environ.copy()
    env["DATABASE_URL"] = f"postgres://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"
    env["REDIS_URL"] = f"redis://{r_host}:{r_port}/{r_db}?protocol=2&socket_timeout=15&socket_connect_timeout=5"
    env["DJANGO_DEBUG"] = "True" if is_debug else "False"

    manage_py = os.path.join(PROJECT_ROOT, "manage.py")
    cmd = [PYTHON_EXE, manage_py, "runserver", f"{d_host}:{d_port}", "-w", str(d_workers)]
    if is_early:
        cmd.append("--early-launch")

    print(f"\n[LAUNCH] Starting {d_workers}-Worker Daphne Cluster on {d_host}:{d_port}...")
    execute_server(cmd, env, pg_host=pg_host, pg_port=pg_port, redis_host=r_host, redis_port=r_port, daphne_port=d_port)


def switch_deployment_profile():
    clear_screen()
    print("=" * 80)
    print("              CAMPUS NEXUS — SWITCH DEPLOYMENT PROFILE")
    print("=" * 80)

    if not os.path.exists(PROFILES_FILE):
        print("\nNo saved profiles file found.")
        time.sleep(1)
        return

    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    profiles = data.get("profiles", {})
    active = data.get("active_profile", "")

    print("\nAvailable deployment profiles:\n")
    mapping = {}
    for idx, (k, v) in enumerate(profiles.items(), 1):
        marker = " (ACTIVE)" if k == active else ""
        print(f"  [{idx}] {k}{marker} — {v.get('name', '')}")
        mapping[str(idx)] = k

    print("\n  [B] Back to Main Menu\n")
    sel = input("Select profile number to activate: ").strip()
    if sel in mapping:
        chosen = mapping[sel]
        data["active_profile"] = chosen
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"\nActivated profile '{chosen}'!")
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Campus Nexus Launcher & Orchestration Control")
    parser.add_argument("-w", "--workers", type=int, default=None, help="Number of Daphne worker processes")
    parser.add_argument("-p", "--port", type=int, default=None, help="Public Daphne port")
    parser.add_argument("-b", "--bind", type=str, default=None, help="Public bind host")
    parser.add_argument("--pg-host", type=str, default=None, help="PostgreSQL host")
    parser.add_argument("--pg-port", type=int, default=None, help="PostgreSQL port")
    parser.add_argument("--redis-host", type=str, default=None, help="Redis host")
    parser.add_argument("--redis-port", type=int, default=None, help="Redis port")
    parser.add_argument("--debug", action="store_true", help="Enable Django DEBUG mode")
    parser.add_argument("--early-launch", action="store_true", help="Enable early launch mode")
    parser.add_argument("--check-services", action="store_true", help="Run infrastructure service checks and exit")
    args = parser.parse_args()

    if args.check_services:
        prof = get_active_profile()["data"]
        pg_h = args.pg_host or prof["pg_host"]
        pg_p = args.pg_port or prof["pg_port"]
        r_h = args.redis_host or prof["redis_host"]
        r_p = args.redis_port or prof["redis_port"]
        port = args.port or prof["daphne_port"]
        ensure_infrastructure(pg_h, pg_p, r_h, r_p, port)
        return

    # Headless / Direct CLI invocation
    if args.workers or args.port or args.bind or args.pg_port or args.redis_port:
        prof = get_active_profile()["data"]
        host = args.bind or prof["daphne_host"]
        port = args.port or prof["daphne_port"]
        workers = args.workers or prof["daphne_workers"]
        pg_h = args.pg_host or prof["pg_host"]
        pg_p = args.pg_port or prof["pg_port"]
        r_h = args.redis_host or prof["redis_host"]
        r_p = args.redis_port or prof["redis_port"]

        env = os.environ.copy()
        env["DATABASE_URL"] = f"postgres://{prof['pg_user']}:{prof['pg_password']}@{pg_h}:{pg_p}/{prof['pg_db']}"
        env["REDIS_URL"] = f"redis://{r_h}:{r_p}/{prof['redis_db']}?protocol=2&socket_timeout=15&socket_connect_timeout=5"
        env["DJANGO_DEBUG"] = "True" if args.debug else "False"

        manage_py = os.path.join(PROJECT_ROOT, "manage.py")
        cmd = [PYTHON_EXE, manage_py, "runserver", f"{host}:{port}", "-w", str(workers)]
        if args.early_launch:
            cmd.append("--early-launch")
        print(f"\n[HEADLESS LAUNCH] Starting {workers}-Worker Daphne Cluster on {host}:{port}...")
        execute_server(cmd, env, pg_host=pg_h, pg_port=pg_p, redis_host=r_h, redis_port=r_p, daphne_port=port)
        return

    # Interactive Menu
    manage_py = os.path.join(PROJECT_ROOT, "manage.py")

    while True:
        show_banner()
        active = get_active_profile()["data"]

        print("\nSelect a launch configuration or configurator:\n")
        print(f"  [1] Active Profile Cluster ({active['daphne_host']}:{active['daphne_port']}, {active['daphne_workers']} Workers) [RECOMMENDED / CURRENT PROFILE]")
        print(f"  [2] Active Profile Cluster with Early-Launch Mode (--early-launch)")
        print(f"  [3] Single-Worker Dev Server ({active['daphne_host']}:{active['daphne_port']}, Debug=True)")
        print("  [4] Turbo High-Concurrency Cluster (16 Workers on 0.0.0.0:8000)")
        print("  [5] Comparative SQLite Multi-Worker Cluster (Port 9000, 8 Workers)")
        print("\n  --- CUSTOM SOCKETS & PROFILE MANAGEMENT ---")
        print("  [C] Custom Deployment & Socket Configurator (Select IP/Port sockets & worker counts)")
        print("  [P] Switch / Manage Deployment Profiles (Production, Turbo, Dev, Staging, Custom)")
        print("\n  --- INFRASTRUCTURE & VERIFICATION ---")
        print("  [6] Bind & Verify Infrastructure (PostgreSQL, Redis & Cloudflared Daemons)")
        print("  [7] Deep Database Parity & Integrity Check (SQLite vs PostgreSQL)")
        print("  [8] Run Full Test Suite (100% Green Validation)")
        print("  [9] Run Comprehensive Port Probing Matrix (50 E2E Probes)")
        print("  [0] Exit\n")

        try:
            choice = input("Enter your choice [1-9, C, P, 0]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting Campus Nexus Launcher. Goodbye!")
            break

        env = os.environ.copy()

        if choice == "1":
            p = get_active_profile()["data"]
            env["DATABASE_URL"] = f"postgres://{p['pg_user']}:{p['pg_password']}@{p['pg_host']}:{p['pg_port']}/{p['pg_db']}"
            env["REDIS_URL"] = f"redis://{p['redis_host']}:{p['redis_port']}/{p['redis_db']}?protocol=2&socket_timeout=15&socket_connect_timeout=5"
            env["DJANGO_DEBUG"] = "True" if p.get("debug") else "False"
            cmd = [PYTHON_EXE, manage_py, "runserver", f"{p['daphne_host']}:{p['daphne_port']}", "-w", str(p['daphne_workers'])]
            if p.get("early_launch"):
                cmd.append("--early-launch")
            print(f"\n[LAUNCH] Starting {p['daphne_workers']}-Worker Daphne Cluster on {p['daphne_host']}:{p['daphne_port']}...")
            execute_server(cmd, env, pg_host=p["pg_host"], pg_port=p["pg_port"], redis_host=p["redis_host"], redis_port=p["redis_port"], daphne_port=p["daphne_port"])

        elif choice == "2":
            p = get_active_profile()["data"]
            env["DATABASE_URL"] = f"postgres://{p['pg_user']}:{p['pg_password']}@{p['pg_host']}:{p['pg_port']}/{p['pg_db']}"
            env["REDIS_URL"] = f"redis://{p['redis_host']}:{p['redis_port']}/{p['redis_db']}?protocol=2&socket_timeout=15&socket_connect_timeout=5"
            env["DJANGO_DEBUG"] = "False"
            cmd = [PYTHON_EXE, manage_py, "runserver", f"{p['daphne_host']}:{p['daphne_port']}", "-w", str(p['daphne_workers']), "--early-launch"]
            print(f"\n[LAUNCH] Starting {p['daphne_workers']}-Worker Cluster with --early-launch on {p['daphne_host']}:{p['daphne_port']}...")
            execute_server(cmd, env, pg_host=p["pg_host"], pg_port=p["pg_port"], redis_host=p["redis_host"], redis_port=p["redis_port"], daphne_port=p["daphne_port"])

        elif choice == "3":
            p = get_active_profile()["data"]
            env["DATABASE_URL"] = f"postgres://{p['pg_user']}:{p['pg_password']}@{p['pg_host']}:{p['pg_port']}/{p['pg_db']}"
            env["REDIS_URL"] = f"redis://{p['redis_host']}:{p['redis_port']}/{p['redis_db']}?protocol=2&socket_timeout=15&socket_connect_timeout=5"
            env["DJANGO_DEBUG"] = "True"
            cmd = [PYTHON_EXE, manage_py, "runserver", f"{p['daphne_host']}:{p['daphne_port']}"]
            print(f"\n[LAUNCH] Starting Single-Worker PostgreSQL Server on {p['daphne_host']}:{p['daphne_port']} (Debug=True)...")
            execute_server(cmd, env, pg_host=p["pg_host"], pg_port=p["pg_port"], redis_host=p["redis_host"], redis_port=p["redis_port"], daphne_port=p["daphne_port"])

        elif choice == "4":
            env["DJANGO_DEBUG"] = "False"
            cmd = [PYTHON_EXE, manage_py, "runserver", "0.0.0.0:8000", "-w", "16"]
            print("\n[LAUNCH] Starting Turbo High-Concurrency Daphne Cluster (16 Workers) on 0.0.0.0:8000...")
            execute_server(cmd, env, daphne_port=8000)

        elif choice == "5":
            env["DATABASE_URL"] = f"sqlite:///{os.path.join(PROJECT_ROOT, 'db.sqlite3')}"
            env["DJANGO_DEBUG"] = "False"
            cmd = [PYTHON_EXE, manage_py, "runserver", "0.0.0.0:9000", "-w", "8"]
            print("\n[LAUNCH] Starting Comparative SQLite Multi-Worker Cluster on 0.0.0.0:9000 (8 Workers)...")
            execute_server(cmd, env, daphne_port=9000)

        elif choice == "c":
            configure_custom_deployment()

        elif choice == "p":
            switch_deployment_profile()

        elif choice == "6":
            p = get_active_profile()["data"]
            ensure_infrastructure(p["pg_host"], p["pg_port"], p["redis_host"], p["redis_port"], p["daphne_port"])
            wait_for_keypress("Infrastructure check completed. Press any key to return to menu...")

        elif choice == "7":
            print("\n[AUDIT] Running row-level and sequence parity checks...")
            subprocess.run([PYTHON_EXE, os.path.join(PROJECT_ROOT, "scripts", "deep_check_parity.py")])
            subprocess.run([PYTHON_EXE, os.path.join(PROJECT_ROOT, "scripts", "verify_db_parity.py")])
            wait_for_keypress()

        elif choice == "8":
            print("\n[TEST] Running full regression test suite on PostgreSQL...")
            subprocess.run([PYTHON_EXE, manage_py, "test", "--settings=campus.settings.test"])
            wait_for_keypress()

        elif choice == "9":
            print("\n[TEST] Running comprehensive 50-probe E2E validation matrix...")
            subprocess.run([PYTHON_EXE, os.path.join(PROJECT_ROOT, "scripts", "test_postgres_comprehensive.py")])
            wait_for_keypress()

        elif choice == "0":
            print("\nExiting Campus Nexus Launcher. Goodbye!")
            break

        else:
            print("\nInvalid choice. Please select an option from the menu.")
            time.sleep(1)


if __name__ == "__main__":
    main()
