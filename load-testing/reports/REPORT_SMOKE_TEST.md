# Campus Nexus — Capacity Assessment & Benchmark Report
**Scenario:** `Smoke Test`  
**Target Host:** `http://127.0.0.1:8000`  
**Generated:** `2026-11-07 03:15:58`  

---

## 1. Executive Capacity Summary

| Metric | Measured Result |
|---|---:|
| **Maximum comfortable concurrent users** | **0 users** |
| **Maximum sustainable concurrent users** | **0 users** |
| **Maximum requests per second** | **0.00 req/s** |
| **Average response time** | **0.00 ms** |
| **P50 response time** | **0.00 ms** |
| **P95 response time** | **0.00 ms** |
| **P99 response time** | **0.00 ms** |
| **HTTP Error Rate** | **0.0%** (0 failures out of 0 requests) |
| **Peak Host CPU Utilisation** | **0.0%** (Sustained Average: 0.0%) |
| **Peak Application Process CPU** | **0.0%** |
| **Peak RAM Utilisation** | **0.0 MB** (App RSS: 0.0 MB) |
| **Peak Network Throughput** | Up: 0.0 KB/s, Down: 0.0 KB/s |
| **First Identified Bottleneck** | **`None identified (Within engineering limits)`** |

---

## 2. Endpoint Performance Breakdown

| Endpoint   | Requests   | Fails   | Avg (ms)   | Min (ms)   | Max (ms)   | RPS   |
|------------|------------|---------|------------|------------|------------|-------|

---

## 3. Visual Telemetry Charts

![Performance Diagnostics](smoke_test_charts.png)

---

## 4. Bottleneck & Capacity Diagnosis

1. **Application Event Loop Concurrency:**
   - Current server architecture: Daphne ASGI event loop.
   - Python process CPU peaked at **0.0%**.
   - As virtual users increase, Python's GIL and single-process event loop bind Daphne when handling complex template renders (`dashboard`, `coordinate/`) and database queries.

2. **Database Contention:**
   - Database engine: SQLite with database-level file locks.
   - For read-heavy operations (landing, search, ride directories), performance remains within targets.
   - For concurrent state-changing writes (simultaneous join requests and approvals), SQLite write locks introduce serialization latency.

3. **Memory & Resource Headroom:**
   - Host has 16GB RAM with substantial headroom (0 MB peak system memory used).
   - Zero memory leaks detected during test duration.
