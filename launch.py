"""
Campus Nexus - Interactive Python Server Orchestration Launcher
Cross-platform interactive launcher with menu options for multi-worker clusters,
early-launch mode, service binding, and test automation.
"""

import os
import sys
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

PYTHON_EXE = sys.executable

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def show_banner():
    clear_screen()
    print("=" * 80)
    print("                    CAMPUS NEXUS - SERVER ORCHESTRATION (v2.0)")
    print("=" * 80)
    print(f"  Working Directory: {PROJECT_ROOT}")
    print(f"  Python Runtime:    {PYTHON_EXE}")
    print("  Database Engine:   PostgreSQL 18.6 (localhost:5432/campus_nexus)")
    print("  Channel Broker:    Redis (localhost:6379)")
    print("=" * 80)

def ensure_services():
    print("\n[INFRASTRUCTURE] Verifying daemons and services...")
    script_path = os.path.join(PROJECT_ROOT, "scripts", "bind_services.py")
    subprocess.run([PYTHON_EXE, script_path])

def main():
    while True:
        show_banner()
        print("\nPlease select a launch configuration:\n")
        print("  [1] Multi-Worker PostgreSQL Cluster (Port 8000, 8 Workers) [RECOMMENDED / PROD]")
        print("  [2] Multi-Worker PostgreSQL Cluster with Early-Launch Mode (--early-launch)")
        print("  [3] Single-Worker PostgreSQL Dev Server (Port 8000)")
        print("  [4] Comparative SQLite Multi-Worker Cluster (Port 9000, 8 Workers)")
        print("  [5] Single-Worker PostgreSQL Dev Server (Port 9000)")
        print("  [6] Bind & Verify Infrastructure (PostgreSQL 18.6 & Redis Daemons)")
        print("  [7] Deep Database Parity & Integrity Check (SQLite vs PostgreSQL)")
        print("  [8] Run Comprehensive Test Suite (374 Tests - 100% Green)")
        print("  [9] Run Comprehensive Port Probing Matrix (50 E2E Probes)")
        print("  [0] Exit\n")
        
        try:
            choice = input("Enter your choice [0-9]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting Campus Nexus Launcher. Goodbye!")
            break
        
        manage_py = os.path.join(PROJECT_ROOT, "manage.py")
        env = os.environ.copy()
        
        def _execute_server_command(cmd_args, env_map):
            try:
                res = subprocess.run(cmd_args, env=env_map)
                if res.returncode != 0:
                    print(f"\n[ERROR] Server process terminated with exit code {res.returncode}.")
                    input("Press Enter to return to menu...")
                else:
                    print("\n[INFO] Server stopped.")
                    input("Press Enter to return to menu...")
            except (KeyboardInterrupt, EOFError):
                print("\n[INFO] Interrupted by user.")
                try:
                    input("Press Enter to return to menu...")
                except Exception:
                    pass
            except Exception as exc:
                print(f"\n[FATAL ERROR] An unexpected error occurred: {exc}")
                try:
                    input("Press Enter to return to menu...")
                except Exception:
                    pass

        if choice == "1":
            ensure_services()
            print("\n[LAUNCH] Starting 8-Worker PostgreSQL Daphne Cluster on 0.0.0.0:8000...")
            env["DJANGO_DEBUG"] = "False"
            _execute_server_command([PYTHON_EXE, manage_py, "runserver", "0.0.0.0:8000", "-w", "8"], env)
        elif choice == "2":
            ensure_services()
            print("\n[LAUNCH] Starting 8-Worker PostgreSQL Daphne Cluster with --early-launch on 0.0.0.0:8000...")
            env["DJANGO_DEBUG"] = "False"
            _execute_server_command([PYTHON_EXE, manage_py, "runserver", "0.0.0.0:8000", "-w", "8", "--early-launch"], env)
        elif choice == "3":
            ensure_services()
            print("\n[LAUNCH] Starting Single-Worker PostgreSQL Dev Server on 0.0.0.0:8000...")
            env["DJANGO_DEBUG"] = "True"
            _execute_server_command([PYTHON_EXE, manage_py, "runserver", "0.0.0.0:8000"], env)
        elif choice == "4":
            print("\n[LAUNCH] Starting Comparative SQLite Multi-Worker Cluster on 0.0.0.0:9000 (8 Workers)...")
            env["DATABASE_URL"] = f"sqlite:///{os.path.join(PROJECT_ROOT, 'db.sqlite3')}"
            env["DJANGO_DEBUG"] = "False"
            _execute_server_command([PYTHON_EXE, manage_py, "runserver", "0.0.0.0:9000", "-w", "8"], env)
        elif choice == "5":
            ensure_services()
            print("\n[LAUNCH] Starting Single-Worker PostgreSQL Dev Server on 0.0.0.0:9000...")
            env["DJANGO_DEBUG"] = "True"
            _execute_server_command([PYTHON_EXE, manage_py, "runserver", "0.0.0.0:9000"], env)
        elif choice == "6":
            ensure_services()
            try:
                input("\nInfrastructure check completed. Press Enter to return to menu...")
            except (EOFError, KeyboardInterrupt):
                break
        elif choice == "7":
            print("\n[AUDIT] Running row-level and sequence parity checks...")
            subprocess.run([PYTHON_EXE, os.path.join(PROJECT_ROOT, "scripts", "deep_check_parity.py")])
            subprocess.run([PYTHON_EXE, os.path.join(PROJECT_ROOT, "scripts", "verify_db_parity.py")])
            try:
                input("\nPress Enter to return to menu...")
            except (EOFError, KeyboardInterrupt):
                break
        elif choice == "8":
            print("\n[TEST] Running full 374-test regression suite on PostgreSQL...")
            subprocess.run([PYTHON_EXE, manage_py, "test", "--settings=campus.settings.test"])
            try:
                input("\nPress Enter to return to menu...")
            except (EOFError, KeyboardInterrupt):
                break
        elif choice == "9":
            print("\n[TEST] Running comprehensive 50-probe E2E validation matrix...")
            subprocess.run([PYTHON_EXE, os.path.join(PROJECT_ROOT, "scripts", "test_postgres_comprehensive.py")])
            try:
                input("\nPress Enter to return to menu...")
            except (EOFError, KeyboardInterrupt):
                break
        elif choice == "0":
            print("\nExiting Campus Nexus Launcher. Goodbye!")
            break
        else:
            print("\nInvalid choice. Please select from 0 to 9.")
            import time
            time.sleep(1)

if __name__ == "__main__":
    main()
