# Campus Nexus — Load Testing & Server Capacity Assessment Suite

A comprehensive, production-grade load testing and capacity benchmarking framework for **Campus Nexus**.
Designed to measure maximum sustainable concurrent users, requests per second, latency percentiles (P50/P95/P99), hardware resource utilization, database concurrency invariants, and Cloudflare Tunnel overhead.

---

## 1. Absolute Data & Database Isolation

> **Priority Guarantee:** Load testing NEVER modifies, corrupts, or deletes real student records.

* **Namespace Demarcation:** All synthetic test accounts are strictly isolated under `2099LOAD001`–`2099LOAD050` and `2099LOAD999` (admin).
* **Test Listings:** All benchmark ride requests carry the `[LOADTEST]` marker in their location and notes fields.
* **Pre-Test Snapshots:** Before any test run, a bitwise file snapshot of the active SQLite database (`.pre_loadtest_backup`) is saved.
* **Automated Teardown:** Upon benchmark conclusion, `cleanup_test_data.py` drops all test records and restores the pristine database snapshot, returning the platform cleanly to its pre-test state.

---

## 2. Directory Architecture

```text
load-testing/
│
├── locustfile.py                 # Primary Locust test suite combining all user profiles
├── requirements.txt              # Testing framework dependencies
├── README.md                     # Comprehensive operational guide
│
├── config/
│   ├── settings.py               # Configurable thresholds, weights, & environment defaults
│   └── endpoints.py              # Verified application route registry
│
├── users/
│   ├── common.py                 # CSRF extraction, authentication & credential pool leasing
│   ├── anonymous.py              # Profile A: Anonymous discovery visitors (15%)
│   ├── authenticated.py          # Profile B: Ordinary browsing students (55%)
│   ├── transactional.py          # Profile C: Active ride joiners, creators & chatters (25%)
│   └── admin.py                  # Profile D: Operational & analytics desk reviewers (5%)
│
├── monitoring/
│   ├── system_monitor.py         # Sub-second CPU, RAM, Disk, Network & GPU (RTX 5050) telemetry
│   └── database_monitor.py       # SQLite WAL file tracking, DB size, and lock contention monitor
│
├── scripts/
│   ├── setup_test_data.py        # Safe fixture population and pre-test DB backup
│   ├── cleanup_test_data.py      # Automated teardown & bitwise database restore
│   ├── run_scenario.py           # Cross-platform benchmark orchestrator
│   ├── generate_report.py        # Markdown, terminal table, and multi-panel chart generator
│   ├── run_smoke_test.ps1 / .sh  # Quick verification scenario (5 users)
│   ├── run_baseline_test.ps1/.sh # Normal workload baseline (10 users)
│   ├── run_load_test.ps1 / .sh   # Progressive load test (35 users)
│   └── run_stress_test.ps1 / .sh # Stress & saturation test (80 users)
│
├── reports/                      # Auto-generated CSVs, HTML reports, Markdown summaries & PNG charts
│
└── test-data/
    ├── README.md                 # Test data isolation charter
    └── capacity_integrity_test.py # Concurrency race condition integrity test
```

---

## 3. Configuration & Environment Variables

The framework is fully configurable via environment variables without modifying source files:

| Environment Variable | Default | Purpose |
|---|---|---|
| `CAMPUS_TARGET_URL` | `http://127.0.0.1:8000` | Target host (Local Daphne or Cloudflare Tunnel) |
| `CAMPUS_LOADTEST_USERS_COUNT` | `50` | Number of test student accounts to generate |
| `CAMPUS_LOADTEST_PASSWORD` | `LoadTest@Pass123!` | Password for synthetic test accounts |
| `WAIT_TIME_MIN` | `1.0` | Minimum think time between user requests (seconds) |
| `WAIT_TIME_MAX` | `3.0` | Maximum think time between user requests (seconds) |
| `WEIGHT_ANONYMOUS` | `15` | Percentage of anonymous visitor virtual users |
| `WEIGHT_AUTHENTICATED` | `55` | Percentage of ordinary browsing student virtual users |
| `WEIGHT_TRANSACTIONAL` | `25` | Percentage of state-changing transactional virtual users |
| `WEIGHT_ADMIN` | `5` | Percentage of administrative desk virtual users |

---

## 4. Running Benchmarks

### Option A: Using Pre-Configured Scenarios

From PowerShell:
```powershell
# 1. Smoke Test (5 concurrent users, 60s)
.\load-testing\scripts\run_smoke_test.ps1

# 2. Baseline Test (10 concurrent users, 120s)
.\load-testing\scripts\run_baseline_test.ps1

# 3. Progressive Load Test (35 concurrent users, 180s)
.\load-testing\scripts\run_load_test.ps1

# 4. Stress Test (80 concurrent users, 120s)
.\load-testing\scripts\run_stress_test.ps1
```

From Bash:
```bash
chmod +x load-testing/scripts/*.sh
./load-testing/scripts/run_smoke_test.sh
./load-testing/scripts/run_baseline_test.sh
```

### Option B: Universal Custom Benchmark Runner

```powershell
# Run custom duration against local server
.\venv\Scripts\python.exe load-testing/scripts/run_scenario.py --scenario baseline --target http://127.0.0.1:8000 --users 20 --duration 90s

# Run benchmark across Cloudflare Tunnel
.\venv\Scripts\python.exe load-testing/scripts/run_scenario.py --scenario baseline --target https://dev.campus-nexus.online --users 10 --duration 60s
```

### Option C: Concurrent Capacity Integrity Test

Verifies that concurrent write races against scarce capacity (e.g., 30 students claiming 3 seats simultaneously) never violate business invariants:
```powershell
.\venv\Scripts\python.exe load-testing/test-data/capacity_integrity_test.py
```

---

## 5. Distributed Load Generation (Master/Worker Configuration)

To isolate the load generator from the application hosting laptop and prevent CPU contention:

### On the Load Generator Machine (Machine B):
```bash
# 1. Start Locust Master
locust -f load-testing/locustfile.py --master --host http://<HOST_IP>:8000

# 2. Start Worker Processes (one per CPU core)
locust -f load-testing/locustfile.py --worker --master-host=<MACHINE_B_IP>
```

Open `http://localhost:8089` on Machine B to control the distributed swarm.

---

## 6. Cleanup & Reset

To manually trigger a complete cleanup and restore the database to its pre-test snapshot:
```powershell
.\venv\Scripts\python.exe load-testing/scripts/cleanup_test_data.py --restore-backup
```
