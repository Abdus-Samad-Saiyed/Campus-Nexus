"""
Campus Nexus — Automated Benchmark Scenario Runner
Orchestrates:
  1. Safe Test Data Setup (isolated test accounts + DB backup)
  2. Asynchronous System Resource Monitoring (psutil + nvidia-smi)
  3. Database Performance Monitoring (SQLite WAL + query latency)
  4. Headless Locust Execution
  5. Multi-panel Visualization & Markdown Report Generation
  6. Automated Database Teardown & Pristine Restoration
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

LOADTEST_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = LOADTEST_DIR.parent
REPORTS_DIR = LOADTEST_DIR / "reports"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(LOADTEST_DIR))

from monitoring.system_monitor import SystemMonitor
from monitoring.database_monitor import DatabaseMonitor
from scripts.setup_test_data import setup_loadtest_fixtures
from scripts.cleanup_test_data import cleanup_loadtest_fixtures
from scripts.generate_report import generate_benchmark_report


SCENARIOS = {
    "smoke": {
        "name": "Smoke Test",
        "users": 5,
        "spawn_rate": 1,
        "duration": "60s",
        "duration_sec": 60,
    },
    "baseline": {
        "name": "Baseline Test",
        "users": 10,
        "spawn_rate": 2,
        "duration": "120s",
        "duration_sec": 120,
    },
    "load": {
        "name": "Progressive Load Test",
        "users": 35,
        "spawn_rate": 5,
        "duration": "180s",
        "duration_sec": 180,
    },
    "stress": {
        "name": "Stress Test",
        "users": 80,
        "spawn_rate": 10,
        "duration": "120s",
        "duration_sec": 120,
    },
    "burst": {
        "name": "Burst Traffic Test",
        "users": 60,
        "spawn_rate": 30,
        "duration": "60s",
        "duration_sec": 60,
    },
}


def run_benchmark(scenario_key: str, target_url: str = "http://127.0.0.1:8000", custom_users: int = None, custom_duration: str = None):
    scenario = SCENARIOS.get(scenario_key.lower(), SCENARIOS["baseline"])
    scenario_name = scenario["name"]
    users = custom_users or scenario["users"]
    duration = custom_duration or scenario["duration"]
    spawn_rate = scenario["spawn_rate"]
    prefix = scenario_key.lower()

    print(f"\n{'='*75}")
    print(f" CAMPUS NEXUS — EXECUTING BENCHMARK: {scenario_name.upper()}")
    print(f" Target Host:       {target_url}")
    print(f" Virtual Users:     {users} concurrent")
    print(f" Spawn Rate:        {spawn_rate} users/sec")
    print(f" Duration:          {duration}")
    print(f"{'='*75}\n")

    # 1. Setup isolated test data
    setup_loadtest_fixtures(count=max(users + 5, 20))

    # 2. Start Monitors
    sys_mon = SystemMonitor(output_basename=f"{prefix}_system")
    db_mon = DatabaseMonitor(output_basename=f"{prefix}_db")
    sys_mon.start()
    db_mon.start()

    locust_csv_prefix = REPORTS_DIR / prefix
    locust_html = REPORTS_DIR / f"{prefix}_report.html"

    # 3. Launch Locust Headless
    python_exe = sys.executable
    locust_cmd = [
        python_exe, "-m", "locust",
        "-f", str(LOADTEST_DIR / "locustfile.py"),
        "--headless",
        "-u", str(users),
        "-r", str(spawn_rate),
        "--run-time", str(duration),
        "--host", target_url,
        "--csv", str(locust_csv_prefix),
        "--html", str(locust_html),
    ]

    print(f"[RUNNER] Launching Locust engine...")
    proc = subprocess.run(locust_cmd)

    # 4. Stop Monitors
    time.sleep(1.0)
    sys_mon.stop()
    db_mon.stop()

    # 5. Generate Comprehensive Report & Charts
    print(f"\n[RUNNER] Generating diagnostic report and performance charts...")
    generate_benchmark_report(scenario_name=scenario_name, target_url=target_url, prefix=prefix)

    # 6. Safe Teardown & Restoration
    print(f"\n[RUNNER] Initiating safety teardown...")
    cleanup_loadtest_fixtures(restore_backup=True)

    print(f"\n{'='*75}")
    print(f" BENCHMARK RUN COMPLETED: {scenario_name.upper()}")
    print(f" Report files saved in {REPORTS_DIR}")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Campus Nexus Benchmark Orchestrator")
    parser.add_argument("--scenario", type=str, default="smoke", choices=list(SCENARIOS.keys()), help="Scenario key")
    parser.add_argument("--target", type=str, default="http://127.0.0.1:8000", help="Target URL (local or Cloudflare)")
    parser.add_argument("--users", type=int, default=None, help="Override concurrent users")
    parser.add_argument("--duration", type=str, default=None, help="Override duration (e.g. 60s, 2m)")
    args = parser.parse_args()

    run_benchmark(
        scenario_key=args.scenario,
        target_url=args.target,
        custom_users=args.users,
        custom_duration=args.duration,
    )
