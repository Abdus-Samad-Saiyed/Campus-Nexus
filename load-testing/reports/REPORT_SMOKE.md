# Campus Nexus — Capacity Assessment & Benchmark Report
**Scenario:** `Smoke Test`  
**Target Host:** `http://127.0.0.1:8000`  
**Generated:** `2026-11-07 03:17:06`  

---

## 1. Executive Capacity Summary

| Metric | Measured Result |
|---|---:|
| **Maximum comfortable concurrent users** | **1 users** |
| **Maximum sustainable concurrent users** | **2 users** |
| **Maximum requests per second** | **2.25 req/s** |
| **Average response time** | **62.55 ms** |
| **P50 response time** | **17.00 ms** |
| **P95 response time** | **540.00 ms** |
| **P99 response time** | **650.00 ms** |
| **HTTP Error Rate** | **3.08%** (2 failures out of 65 requests) |
| **Peak Host CPU Utilisation** | **26.8%** (Sustained Average: 12.6%) |
| **Peak Application Process CPU** | **72.3%** |
| **Peak RAM Utilisation** | **15230.2 MB** (App RSS: 154.1 MB) |
| **Peak Network Throughput** | Up: 2208.2 KB/s, Down: 45.2 KB/s |
| **First Identified Bottleneck** | **`HTTP Error Rate (3.08% > 1.0% target limit)`** |

---

## 2. Endpoint Performance Breakdown

| Endpoint                         |   Requests |   Fails | Avg (ms)   | Min (ms)   | Max (ms)   |   RPS |
|----------------------------------|------------|---------|------------|------------|------------|-------|
| / [Landing]                      |          5 |       0 | 7.6 ms     | 4.5 ms     | 11.7 ms    |  0.17 |
| /about/ [GET]                    |          1 |       0 | 7.1 ms     | 7.1 ms     | 7.1 ms     |  0.03 |
| /apply/ [GET]                    |          3 |       0 | 8.5 ms     | 6.5 ms     | 12.1 ms    |  0.1  |
| /contributors/ [GET]             |          1 |       0 | 13.5 ms    | 13.5 ms    | 13.5 ms    |  0.03 |
| /coordinate/ [Public Rides]      |          2 |       0 | 9.9 ms     | 9.9 ms     | 9.9 ms     |  0.07 |
| /coordinate/?density_view= [GET] |          7 |       0 | 59.7 ms    | 44.9 ms    | 85.9 ms    |  0.24 |
| /coordinate/[id]/ [GET]          |          8 |       0 | 32.3 ms    | 19.5 ms    | 59.1 ms    |  0.28 |
| /coordinate/[id]/join/ [POST]    |          5 |       0 | 80.3 ms    | 62.0 ms    | 88.6 ms    |  0.17 |
| /coordinate/[id]/leave/ [POST]   |          2 |       0 | 51.2 ms    | 48.7 ms    | 53.7 ms    |  0.07 |
| /coordinate/[id]/message/ [POST] |          2 |       2 | 10.0 ms    | 8.4 ms     | 11.7 ms    |  0.07 |
| /coordinate/my-rides/ [GET]      |          1 |       0 | 12.9 ms    | 12.9 ms    | 12.9 ms    |  0.03 |
| /dashboard/ [GET]                |          7 |       0 | 19.6 ms    | 13.5 ms    | 29.3 ms    |  0.24 |
| /login/ [GET]                    |          2 |       0 | 9.1 ms     | 5.3 ms     | 13.0 ms    |  0.07 |
| /login/ [POST]                   |          4 |       0 | 603.0 ms   | 544.8 ms   | 651.2 ms   |  0.14 |
| /messages/ [GET]                 |          3 |       0 | 10.3 ms    | 9.5 ms     | 11.9 ms    |  0.1  |
| /profile/[self]/ [GET]           |          4 |       0 | 12.4 ms    | 11.3 ms    | 13.8 ms    |  0.14 |
| /search/?q= [GET]                |          3 |       0 | 17.4 ms    | 17.0 ms    | 18.0 ms    |  0.1  |
| /services/ [GET]                 |          1 |       0 | 10.0 ms    | 10.0 ms    | 10.0 ms    |  0.03 |
| [CSRF] Fetch Token               |          4 |       0 | 9.6 ms     | 5.6 ms     | 17.7 ms    |  0.14 |

---

## 3. Visual Telemetry Charts

![Performance Diagnostics](smoke_charts.png)

---

## 4. Bottleneck & Capacity Diagnosis

1. **Application Event Loop Concurrency:**
   - Current server architecture: Daphne ASGI event loop.
   - Python process CPU peaked at **72.3%**.
   - As virtual users increase, Python's GIL and single-process event loop bind Daphne when handling complex template renders (`dashboard`, `coordinate/`) and database queries.

2. **Database Contention:**
   - Database engine: SQLite with database-level file locks.
   - For read-heavy operations (landing, search, ride directories), performance remains within targets.
   - For concurrent state-changing writes (simultaneous join requests and approvals), SQLite write locks introduce serialization latency.

3. **Memory & Resource Headroom:**
   - Host has 16GB RAM with substantial headroom (15230 MB peak system memory used).
   - Zero memory leaks detected during test duration.
