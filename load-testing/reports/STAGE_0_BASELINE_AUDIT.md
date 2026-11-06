# Campus Nexus — Stage 0: Initial Repository and Infrastructure Audit

**Generated:** `2026-11-07 03:20:00`  
**Environment:** Active Self-Hosted Development Environment

---

## 1. Application Architecture

| Component | Current Configuration | Architectural Analysis |
|---|---|---|
| **Django Version** | `6.0.5` | Modern Django LTS release running on Python `3.14.3` (AMD64). |
| **Application Server** | **Daphne 4.2.3** (`runserver` extended) | Runs as a single ASGI process / event loop (PID 19444). Twisted-based reactor. |
| **Workers & Threads** | **1 worker process**, single-threaded Python event loop | Currently, all HTTP requests and WebSocket connections are handled by a single Python process. Does not utilize the 10 physical cores / 16 logical threads of the Intel Core 7 240H without multi-worker reverse proxying. |
| **Django Channels** | **Enabled** (`channels>=4.0`) | Handled directly by Daphne ASGI protocol router. Supports `/ws/chat/` and real-time ride messaging. |
| **Channel Layer** | `channels.layers.InMemoryChannelLayer` | In-memory within the Daphne process. Does not persist across restarts and cannot be shared across multiple worker processes without Redis (`channels_redis`). |
| **Cache Engine** | `django.core.cache.backends.locmem.LocMemCache` | In-memory local memory cache per Python process. |
| **Session Backend** | `django.contrib.sessions.backends.db` | **Database-backed sessions.** Every single HTTP request performs a `SELECT` on `django_session`, and every login/mutation performs an `INSERT` or `UPDATE` on SQLite. |
| **Background Tasks** | In-process asynchronous tasks | No Celery or standalone Redis worker currently active; async operations run inline in the ASGI event loop. |

---

## 2. Database Architecture

| Database Characteristic | Current Configuration | Concurrency Assessment |
|---|---|---|
| **Engine** | **SQLite 3** (`django.db.backends.sqlite3`) | Default backend for local development and early launch. |
| **Active Databases** | Multi-DB routing: `default` (`temp_early_access.sqlite3` under `--early-launch`), `permanent` (`db.sqlite3`), `guest_demo` (`guest_demo.sqlite3`). | Strict physical file isolation between permanent records, early launch temporary accounts, and guest explorers. |
| **Connection Pooling** | `CONN_MAX_AGE = 0` (Django default for SQLite) | Each thread/request creates and tears down a connection. |
| **Locking Model** | **Database-level file lock** (Exclusive write lock) | While SQLite supports concurrent readers, it allows **only ONE active writer at any instant**. Burst writes (e.g. 10 simultaneous students submitting join requests) trigger `sqlite3.OperationalError: database is locked` unless `busy_timeout` is configured. |
| **Indexes** | Comprehensive B-Tree indexes on `enrollment_number`, `departure_time`, `status`, `route`, and foreign keys. | Read queries are indexed and fast (e.g. `user_search` 15ms, `ride_list` 12ms). |
| **Transaction Handling** | `transaction.atomic()` with `select_for_update()` | Codebase is structured with PostgreSQL row-level locking conventions. On SQLite, `select_for_update()` is a syntactic no-op, relying on file-level write transactions. |

---

## 3. Host Infrastructure & Hardware Specs

| Hardware Component | Verified Specification | Headroom Assessment |
|---|---|---|
| **CPU** | **Intel(R) Core(TM) 7 240H** (10 Physical Cores: 6 Performance + 4 Efficient, 16 Logical Processors) | Extreme multi-core headroom (23.5% peak during 10-user benchmark). Underutilized by single-process Daphne. |
| **RAM** | **16 GB DDR5** (16,755,732,480 bytes physical) | High headroom. System baseline: ~14.8 GB available/cached, Daphne process RSS: ~174 MB. Zero memory pressure. |
| **Storage / Disk** | **512 GB Western Digital PC SN5000S NVMe SSD** (BusType: NVMe) | High-speed I/O (>4000 MB/s sequential read/write). Zero disk I/O bottlenecks. |
| **GPU** | **NVIDIA GeForce RTX 5050 Laptop GPU** (8,151 MiB VRAM, Driver 616.92) | Idle during CPU-bound Django HTTP serving. Available for background AI or encryption accelerators if needed. |
| **Operating System** | **Microsoft Windows 11 Home Single Language** (Build 10.0.26200) | Development host OS. |
| **Cloudflare Tunnel** | `cloudflared.exe` (PID 2776) connected to `dev.campus-nexus.online` | Active reverse tunnel routing HTTPS traffic from Cloudflare Anycast edge to `127.0.0.1:8000`. |

---

## 4. Key Bottlenecks Identified

1. **Single-Worker ASGI Event Loop:**
   - Daphne runs on a single core. Password hashing (PBKDF2 with 720k iterations) is intentionally CPU-intensive (~500ms CPU time per login). A burst of 3 logins saturates 100% of that single core for ~1.5 seconds.
   - *Recommendation:* Run multiple worker processes (e.g. 4–8 Daphne / Uvicorn worker processes behind an Nginx reverse proxy) to utilize all 10 cores of the Intel Core 7 240H.

2. **SQLite Write Serialization:**
   - Concurrent write operations (ride creation, seat approvals) compete for the single SQLite file write lock.
   - *Recommendation:* Transitioning to PostgreSQL (as planned) will enable true row-level concurrency via `select_for_update()` and eliminate database file lock contention.

3. **Database-Backed Sessions:**
   - Every request reads `django_session` from SQLite.
   - *Recommendation:* Switch to `django.contrib.sessions.backends.cached_db` or Redis sessions to eliminate 1 query per request.
