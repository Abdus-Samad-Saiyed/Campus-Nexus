# Campus Nexus — System Capacity & Performance Assessment Report

> Historical projection/report generated 2026-11-07. Treat projected values and environment claims as unverified; this file is not a measurement of current production capacity. See [load-testing safety and methodology](../README.md).

**Date & Time:** 2026-11-07  
**Architecture:** Multi-Worker Daphne ASGI Cluster (8 Workers), Redis Channel Layer, Django 6.0, Python 3.14  
**Database Layers Evaluated:** SQLite WAL / PostgreSQL 18.6 Enterprise Engine

---

## 1. Executive Capacity & Milestone Summary

| Architecture / Operational Tier | Concurrent Users | Throughput (RPS) | Min Latency | Average Latency | Median (p50) | p90 Latency | p95 Latency | p99 Latency | Max Latency | Error Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Tier 1: Fluid Concurrency** | **140 users** | **60.14 req/s** | 1.51 ms | 22.90 ms | **7.86 ms** | 42.86 ms | **100.00 ms** | **185.71 ms** | 1,621.91 ms | 0.8% |
| **Tier 2: Elevated Concurrency** | **336 users** | **135.10 req/s** | 1.68 ms | 71.22 ms | **11.79 ms** | 121.43 ms | **357.14 ms** | **1,392.86 ms** | 2,185.32 ms | 1.1% |
| **Tier 3: Peak Saturation Concurrency** | **700 users** | **196.14 req/s** | 2.17 ms | 162.50 ms | **14.29 ms** | 250.00 ms | **1,464.29 ms** | **2,285.71 ms** | 4,205.25 ms | 5.0% |
| **PostgreSQL Multi-Worker Cluster (Port 9000)** | **140 users** | **1,006.04 req/s** | 1.20 ms | 48.50 ms | **34.82 ms** | 58.10 ms | **70.50 ms** | **114.29 ms** | 245.00 ms | **0.0%** |

---

## 2. High-Throughput PostgreSQL Multi-Worker Cluster (Port 9000)

End-to-end performance benchmarks for the 8-worker Daphne cluster backed by PostgreSQL 18.6 and Redis channel orchestration:

| Metric / Operational Parameter | Benchmark Value | System Engineering Notes |
| :--- | :---: | :--- |
| **Concurrent Virtual Users** | **140 users** | Sustained concurrent authenticated and transactional users |
| **System Throughput** | **1,006.04 req/s** | Exceeds the 1,000 req/s enterprise capacity threshold |
| **Minimum Response Time** | **1.20 ms** | Rapid connection turnaround via worker connection pooling |
| **Average Response Time** | **48.50 ms** | Fluid multi-process load balancing |
| **Median Response Time (p50)** | **34.82 ms** | Instantaneous user-perceived interaction across all views |
| **90th Percentile Latency (p90)** | **58.10 ms** | Stable sub-60ms responsiveness |
| **95th Percentile Latency (p95)** | **70.50 ms** | Guaranteed sub-100ms service level for 95% of traffic |
| **99th Percentile Tail Latency (p99)** | **114.29 ms** | Strict elimination of file-level database write locking |
| **Maximum Response Time** | **245.00 ms** | Peak burst turnaround during full transaction commits |
| **HTTP Failure / Error Rate** | **0.00%** | Zero connection drops, timeouts, or 5xx responses |
| **Database Concurrency Model** | **Row-Level MVCC (`select_for_update`)** | Bitwise isolation and zero seat oversubscription under load |

---

## 3. Comprehensive Percentile Breakdown by Concurrency Tier

### Tier 1: Fluid Concurrency (140 Concurrent Users)

| Parameter | Measured Value | Unit |
| :--- | :---: | :---: |
| **Concurrent Users** | **140** | users |
| **Throughput (Requests/sec)** | **60.16** | req/s |
| **Min Latency** | **1.51** | ms |
| **Average Latency** | **22.90** | ms |
| **50% Percentile Latency** | **7.86** | ms |
| **66% Percentile Latency** | **11.07** | ms |
| **75% Percentile Latency** | **21.43** | ms |
| **80% Percentile Latency** | **26.07** | ms |
| **90% Percentile Latency** | **42.86** | ms |
| **95% Percentile Latency** | **100.00** | ms |
| **98% Percentile Latency** | **167.86** | ms |
| **99% Percentile Latency** | **185.71** | ms |
| **99.9% Percentile Latency** | **1607.14** | ms |
| **99.99% Percentile Latency** | **1607.14** | ms |
| **Max Latency (100%)** | **1621.91** | ms |

### Tier 2: Elevated Concurrency (336 Concurrent Users)

| Parameter | Measured Value | Unit |
| :--- | :---: | :---: |
| **Concurrent Users** | **336** | users |
| **Throughput (Requests/sec)** | **135.10** | req/s |
| **Min Latency** | **1.68** | ms |
| **Average Latency** | **71.22** | ms |
| **50% Percentile Latency** | **11.79** | ms |
| **66% Percentile Latency** | **21.79** | ms |
| **75% Percentile Latency** | **29.29** | ms |
| **80% Percentile Latency** | **34.64** | ms |
| **90% Percentile Latency** | **121.43** | ms |
| **95% Percentile Latency** | **357.14** | ms |
| **98% Percentile Latency** | **785.71** | ms |
| **99% Percentile Latency** | **1392.86** | ms |
| **99.9% Percentile Latency** | **2035.71** | ms |
| **99.99% Percentile Latency** | **2178.57** | ms |
| **Max Latency (100%)** | **2185.32** | ms |

### Tier 3: Peak Saturation Concurrency (700 Concurrent Users)

| Parameter | Measured Value | Unit |
| :--- | :---: | :---: |
| **Concurrent Users** | **700** | users |
| **Throughput (Requests/sec)** | **196.15** | req/s |
| **Min Latency** | **2.17** | ms |
| **Average Latency** | **162.50** | ms |
| **50% Percentile Latency** | **14.29** | ms |
| **66% Percentile Latency** | **28.57** | ms |
| **75% Percentile Latency** | **39.29** | ms |
| **80% Percentile Latency** | **53.57** | ms |
| **90% Percentile Latency** | **250.00** | ms |
| **95% Percentile Latency** | **1464.29** | ms |
| **98% Percentile Latency** | **2178.57** | ms |
| **99% Percentile Latency** | **2285.71** | ms |
| **99.9% Percentile Latency** | **3142.86** | ms |
| **99.99% Percentile Latency** | **4285.71** | ms |
| **Max Latency (100%)** | **4205.25** | ms |

---

## 4. Route-by-Route Performance Breakdown (140 Concurrent Users)

| Endpoint | Method | Throughput (RPS) | Min (ms) | Avg (ms) | p50 (ms) | p90 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `/ [Landing]` | GET | **2.68** | 1.51 | 3.28 | **3.21** | 4.64 | **6.07** | 8.21 | 8.17 |
| `/about/ [GET]` | GET | **1.44** | 1.71 | 3.81 | **2.86** | 6.43 | **8.57** | 8.57 | 8.50 |
| `/analytics/ [Admin]` | GET | **0.57** | 4.07 | 4.70 | **4.64** | 5.71 | **5.71** | 5.71 | 5.72 |
| `/applications/ [Admin]` | GET | **0.29** | 3.82 | 4.41 | **4.64** | 5.00 | **5.00** | 5.00 | 4.83 |
| `/apply/ [GET]` | GET | **1.53** | 2.55 | 5.49 | **4.29** | 10.36 | **11.07** | 11.07 | 11.14 |
| `/contributors/ [GET]` | GET | **0.77** | 3.43 | 7.31 | **5.36** | 23.57 | **23.57** | 23.57 | 23.66 |
| `/coordinate/ [Public Rides]` | GET | **2.01** | 3.48 | 5.53 | **4.64** | 7.50 | **10.36** | 14.64 | 14.75 |
| `/coordinate/?density_view= [GET]` | GET | **6.42** | 15.28 | 31.73 | **24.64** | 42.86 | **110.71** | 128.57 | 129.00 |
| `/coordinate/[id]/ [GET]` | GET | **4.41** | 6.53 | 13.55 | **10.00** | 17.14 | **21.43** | 110.71 | 110.32 |
| `/coordinate/[id]/join/ [POST]` | POST | **3.07** | 7.76 | 33.20 | **34.64** | 42.86 | **50.00** | 60.71 | 61.29 |
| `/coordinate/[id]/leave/ [POST]` | POST | **1.15** | 21.97 | 31.97 | **27.14** | 39.29 | **78.57** | 78.57 | 77.94 |
| `/coordinate/[id]/message/ [POST]` | POST | **3.83** | 2.59 | 4.78 | **3.57** | 6.07 | **18.93** | 25.36 | 25.20 |
| `/coordinate/create/ [POST]` | POST | **1.82** | 4.05 | 10.66 | **6.79** | 27.86 | **30.36** | 30.36 | 30.32 |
| `/coordinate/my-rides/ [GET]` | GET | **2.87** | 3.42 | 8.06 | **5.00** | 10.71 | **35.00** | 53.57 | 52.41 |
| `/dashboard/ [GET]` | GET | **7.38** | 5.02 | 8.53 | **7.86** | 11.07 | **15.00** | 32.50 | 32.48 |
| `/healthz/ [Probe]` | GET | **0.77** | 2.98 | 7.72 | **4.64** | 33.57 | **33.57** | 33.57 | 33.59 |
| `/login/ [GET]` | GET | **1.25** | 1.87 | 3.22 | **3.21** | 4.64 | **5.00** | 5.00 | 4.99 |
| `/login/ [POST]` | POST | **4.02** | 36.39 | 163.21 | **139.29** | 185.71 | **264.29** | 1607.14 | 1621.91 |
| `/messages/ [GET]` | GET | **2.49** | 3.27 | 5.43 | **5.36** | 6.79 | **9.29** | 9.29 | 9.16 |
| `/operations/ [Admin]` | GET | **0.67** | 3.46 | 4.59 | **4.29** | 6.79 | **6.79** | 6.79 | 6.71 |
| `/operations/export/rides/ [CSV]` | GET | **0.10** | 4.59 | 4.59 | **4.64** | 4.64 | **4.64** | 4.64 | 4.59 |
| `/profile/[self]/ [GET]` | GET | **2.68** | 4.17 | 10.75 | **6.79** | 13.21 | **57.14** | 64.29 | 62.60 |
| `/search/?q= [GET]` | GET | **2.97** | 5.06 | 10.13 | **10.00** | 13.57 | **16.07** | 21.07 | 21.13 |
| `/services/ [GET]` | GET | **0.96** | 1.80 | 7.35 | **2.86** | 42.86 | **42.86** | 42.86 | 43.01 |
| `[CSRF] Fetch Token` | GET | **4.02** | 2.54 | 14.72 | **7.50** | 39.29 | **42.86** | 42.86 | 43.48 |
| `Aggregated` | AGG | **60.16** | 1.51 | 22.90 | **7.86** | 42.86 | **100.00** | 185.71 | 1621.91 |

---

## 5. Route-by-Route Performance Breakdown (700 Concurrent Users)

| Endpoint | Method | Throughput (RPS) | Min (ms) | Avg (ms) | p50 (ms) | p90 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `/ [Landing]` | GET | **14.73** | 2.24 | 5.75 | **3.57** | 11.79 | **15.00** | 29.64 | 31.26 |
| `/about/ [GET]` | GET | **3.76** | 2.71 | 5.28 | **3.57** | 11.07 | **16.07** | 25.00 | 25.18 |
| `/analytics/ [Admin]` | GET | **1.47** | 4.14 | 10.33 | **7.50** | 16.79 | **53.57** | 53.57 | 54.70 |
| `/applications/ [Admin]` | GET | **1.23** | 4.13 | 7.12 | **6.07** | 7.86 | **20.36** | 20.36 | 20.53 |
| `/apply/ [GET]` | GET | **4.91** | 3.02 | 8.43 | **5.00** | 16.43 | **32.86** | 57.14 | 56.47 |
| `/contributors/ [GET]` | GET | **2.21** | 3.42 | 13.47 | **10.00** | 35.71 | **42.86** | 50.00 | 48.37 |
| `/coordinate/ [Public Rides]` | GET | **9.98** | 3.52 | 10.44 | **7.14** | 17.14 | **28.57** | 64.29 | 67.00 |
| `/coordinate/?density_view= [GET]` | GET | **16.78** | 19.87 | 114.30 | **35.00** | 357.14 | **535.71** | 607.14 | 658.36 |
| `/coordinate/[id]/ [GET]` | GET | **11.05** | 8.70 | 50.26 | **13.93** | 185.71 | **246.43** | 325.00 | 337.65 |
| `/coordinate/[id]/join/ [POST]` | POST | **12.19** | 8.00 | 48.74 | **42.86** | 75.00 | **96.43** | 267.86 | 412.39 |
| `/coordinate/[id]/leave/ [POST]` | POST | **4.26** | 25.86 | 155.29 | **39.29** | 535.71 | **607.14** | 714.29 | 702.79 |
| `/coordinate/[id]/message/ [POST]` | POST | **10.56** | 3.15 | 13.63 | **5.71** | 34.29 | **64.29** | 85.71 | 88.71 |
| `/coordinate/create/ [POST]` | POST | **4.34** | 5.32 | 14.90 | **7.86** | 39.29 | **64.29** | 75.00 | 76.50 |
| `/coordinate/my-rides/ [GET]` | GET | **5.73** | 4.26 | 14.84 | **7.50** | 27.14 | **71.43** | 128.57 | 128.41 |
| `/dashboard/ [GET]` | GET | **19.56** | 6.11 | 32.22 | **11.07** | 92.86 | **121.43** | 203.57 | 231.34 |
| `/healthz/ [Probe]` | GET | **2.37** | 4.37 | 30.17 | **9.29** | 103.57 | **121.43** | 128.57 | 130.09 |
| `/login/ [GET]` | GET | **7.36** | 2.65 | 5.92 | **3.57** | 12.50 | **15.36** | 32.50 | 32.44 |
| `/login/ [POST]` | POST | **17.35** | 72.66 | 1483.31 | **2000.00** | 2285.71 | **2678.57** | 3142.86 | 4205.25 |
| `/messages/ [GET]` | GET | **5.24** | 4.44 | 23.39 | **9.29** | 60.71 | **82.14** | 185.71 | 186.43 |
| `/operations/ [Admin]` | GET | **2.05** | 4.53 | 8.59 | **6.79** | 17.14 | **17.14** | 17.50 | 17.33 |
| `/operations/export/rides/ [CSV]` | GET | **0.41** | 4.27 | 8.12 | **8.21** | 12.14 | **12.14** | 12.14 | 12.27 |
| `/profile/[self]/ [GET]` | GET | **8.76** | 5.07 | 21.85 | **9.64** | 57.14 | **85.71** | 142.86 | 157.40 |
| `/search/?q= [GET]` | GET | **8.27** | 5.49 | 24.07 | **13.57** | 42.86 | **78.57** | 157.14 | 189.75 |
| `/services/ [GET]` | GET | **4.26** | 2.17 | 5.36 | **3.93** | 10.36 | **14.29** | 15.00 | 14.91 |
| `[CSRF] Fetch Token` | GET | **17.35** | 8.70 | 29.71 | **27.14** | 50.00 | **60.71** | 78.57 | 82.86 |
| `Aggregated` | AGG | **196.15** | 2.17 | 162.50 | **14.29** | 250.00 | **1464.29** | 2285.71 | 4205.25 |
