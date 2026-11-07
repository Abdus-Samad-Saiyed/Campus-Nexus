# COORDINATE // Campus Nexus

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Django 6.0.5](https://img.shields.io/badge/Django-6.0.5-green.svg)](https://www.djangoproject.com/)
[![Channels ASGI](https://img.shields.io/badge/Channels-ASGI%20WebSockets-teal.svg)](https://channels.readthedocs.io/)
[![Database](https://img.shields.io/badge/Database-PostgreSQL%2016%20%7C%20SQLite3-blue.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Cache%20%26%20Layers-Redis%207-red.svg)](https://redis.io/)
[![WebAuthn Passkeys](https://img.shields.io/badge/Auth-WebAuthn%20%2F%20Passkeys-orange.svg)](https://webauthn.io/)
[![Mobile API](https://img.shields.io/badge/Mobile-React%20Native%20Ready-purple.svg)](campus/api/)
[![Tests](https://img.shields.io/badge/Tests-322%20Passed%20(100%25)-success.svg)](manage.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Decentralized Student Ride Coordination & Verified Campus Public Utility Platform**  
> Designed, architected, and engineered for the student body of the **National Institute of Technology Srinagar (NIT Srinagar)**.  
> *Sic Itur Ad Astra* ("Thus one journeys to the stars")

---

## 📌 Executive Summary & Problem Statement

At university campuses, commuter students and hostel residents frequently need to coordinate shared cabs, airport shuttles, and inter-city travel (e.g. Srinagar &harr; Jammu / Airport / Railway Station). Traditionally, these requests were posted across dozens of cluttered, ephemeral WhatsApp groups where ride requests were quickly buried, timings were missed, and safety accountability was virtually non-existent.

**COORDINATE** solves this as a dedicated, real-time public digital utility:
1. **Physical Root of Trust & Early Launch Agility:** Accounts are verified in person against physical student ID cards, with a built-in `--early-launch` toggle for instant self-declaration during high-demand end-of-term departure rushes.
2. **Mutual Physical Accountability:** Anonymity is strictly prohibited inside moving vehicles. Confirmed co-passengers mutually share verified identities and pickup phone numbers.
3. **Real-Time Coordination:** ASGI WebSockets deliver instant ride chat, waitlist promotions, occupancy density curves, and departure synchronization.
4. **Institutional Security:** Cryptographic HMAC-SHA256 dual-channel OTPs, FIDO2/WebAuthn biometric passkeys, rate-limited reputation anti-farming, and single active session controls.
5. **Universal PWA & Multi-Client Ready:** Full cross-platform Progressive Web App (PWA) deployment for Android, iOS, Windows, macOS, and Linux—delivering zero-install convenience, offline caching, and 2-week persistent biometric passkey sessions without Play Store or App Store friction, alongside versioned REST API v1 endpoints.

---

## 🏗️ Architecture & Technology Stack

```mermaid
flowchart TD
    Client(["Students: Universal PWA (Android / iOS / Windows / Mac / Linux) & REST API Clients"]) -->|HTTPS:443 & WSS:443| NGINX["NGINX Reverse Proxy (SSL / HTTP/2)"]
    
    subgraph CorePlatform ["Campus Nexus / COORDINATE Engine"]
        NGINX -->|HTTP Reverse Proxy & REST API| Daphne["Daphne ASGI Process Cluster (Port 8000)"]
        NGINX -->|Direct Serve & Caching| WhiteNoise["WhiteNoise Compressed Static Engine"]
        
        Daphne --> Django["Django 6.0.5 Core Application & REST API v1"]
        Daphne --> Channels["Django Channels (ASGI WebSockets)"]
        
        Channels <-->|Redis Channel Layer or LocMem| Redis[("Redis 7 (Pub/Sub & Distributed Cache)")]
        Django <-->|ORM & Check Constraints| Postgres[("PostgreSQL 16 / SQLite3 (Relational Database)")]
        
        Django -->|Protected Media Gate| MediaStorage["Media Storage (Realpath Contained)"]
        Django -->|TLS:587 SMTP Relay| MailRelay["Google SMTP OTP Relay"]
    end

    Django <-->|WebAuthn Assertion / Registration| FIDO2["FIDO2 / Windows Hello Passkeys (webauthn package)"]
```

### Core Technologies
* **Backend:** Python 3.12+, Django 6.0.5, Django Channels 4 (ASGI)
* **Application Server:** Daphne ASGI cluster
* **Database:** PostgreSQL 16 & SQLite3 (Dual production compatibility with WAL mode tuning)
* **In-Memory Cache & Message Broker:** Redis 7 (`channels_redis` channel layer & distributed rate limiters) with standalone LocMem fallback
* **Cross-Platform Mobile & Frontend Architecture:**
  * **Web Client:** Django Templates, HTMX, Vanilla ES6+ JavaScript, Bootstrap 5 Glassmorphism UI, Chart.js
  * **Universal Progressive Web App (PWA):** Web App Manifest (`manifest.json`), Service Worker (`/sw.js`) with cache-first static shell and offline fallback (`/offline/`), standalone fullscreen launch, and post-login install prompts across Android, iOS, Windows, Mac, and Linux.
  * **Programmatic REST & WebSocket APIs:** Dedicated `/api/v1/` REST endpoints with Bearer token auth, CORS headers, and `TokenOrSessionAuthMiddleware` ASGI WebSockets for external tools and bots.
* **Security & Auth:** WebAuthn / FIDO2 Passkeys (`webauthn` library), HMAC-SHA256 OTP verification, Single Active Session Enforcement
* **Reverse Proxy:** NGINX with Let's Encrypt TLS & HTTP/2

---

## 🚀 Key Features & Engineering Highlights

### 1. In-Person Physical Root of Trust & Verification Desk
* Rather than relying solely on automated digital self-assertion, the platform anchors digital trust to physical reality.
* Students apply with their official enrollment number (`YYYY[BMP][BRANCH][ROLL]`) and phone number, verify inbox ownership via a 6-digit email OTP, and present their physical Institute ID card at the campus verification desk.
* Authorized student administrators review and approve accounts directly via the `/operations/` console.

### 2. Early Launch Mode (`--early-launch`) & Temporary Database Isolation
* During end-of-term transit surges when in-person desk verification creates an onboarding bottleneck, the system can be started with the `--early-launch` CLI flag or `EARLY_LAUNCH_MODE=True`.
* **Isolated Temporary Database:** All early access self-declarations, rides, and chats are written to `temp_early_access.sqlite3`. The permanent database is never touched, completely eliminating data corruption or conflicts with real accounts.
* **Untrusted Status (`verified=False`):** Self-declared accounts are explicitly marked untrusted, badged in profiles and rosters, and temporary.
* **Purgeable & Reversible:** Omitting `--early-launch` restores the pristine permanent database. Run `python manage.py purge_early_launch` to purge the temporary database post-rush.

### 3. Dynamic Ride Capacity Modification & Surplus Ejection
* Hosts can adjust total vehicle capacity post-creation (`1 <= total_spots <= 6`).
* When decreasing capacity below current passenger count, hosts must explicitly select which surplus passenger(s) to remove, preventing arbitrary or unhandled drops while automatically recalibrating waitlists.

### 4. Theme-Synchronized Canvas Favicon Engine
* Tab favicons dynamically re-render on an HTML5 `<canvas>` in real-time, matching the active user colorway (Emerald, Cyan, Indigo, Amber, Rose, etc.) or custom hex values.
* Fallback 32x32 PNG endpoint served at `/favicon.ico` with HTTP 200 OK.

### 5. Interactive Database Setup & Management Script
* Built-in interactive setup utility: `python setup.py` (or `python manage.py setup_db`).
* Interactive migration runner that prompts before applying schema updates.
* Full menu-driven database inspector: browse tables, execute DQL (`SELECT`), execute transactional DML/DDL (`INSERT`/`UPDATE`/`DELETE`), and view real-time diagnostics.
* Abstracted User Provisioning: create Superuser Admins, Staff, or Standard Students directly in PostgreSQL or SQLite with automatic enrollment parsing and password hashing.

### 6. Physical Ride Accountability & Audience Comfort
* **No Anonymous Riders IRL:** Inside a shared motor vehicle, passengers and host mutually view each other's verified student name, enrollment, and contact number with one-click call and WhatsApp integration.
* **Spectator Privacy:** Browsing students outside the ride see aggregate occupancy counts without access to passenger rosters or private phone numbers.
* **Audience Comfort Settings:** Ride hosts can specify comfort preferences: `MIXED`, `BOYS`, `GIRLS`, or `FRIENDS`-only.
* **Non-Destructive Soft Cancellation:** When all participants leave an active ride, the platform sets `status = 'CANCELLED'` rather than deleting database rows, permanently preserving safety incident reports, grievance histories, and audit evidence.

### 7. Anti-Farming Reliability Reputation Engine
* Every completed group commute increments a student's `rides_completed` counter.
* To prevent collusion and reputation score farming on a public utility, on-time reliability score boosts (`on_time_count`) are mathematically capped at **3 boosts per 15-day rolling window** per user.
* Enforced by database check constraint: `1 <= total_spots <= 6`.

### 8. FIDO2 / WebAuthn Biometric Passkeys & 2-Week PWA Sessions
* WebAuthn standard implementation allowing students to register and sign in with hardware security keys, Touch ID, Apple Face ID, or Windows Hello biometrics.
* Standalone PWA applications on desktop and mobile remain securely authenticated for 2 weeks with one-touch passkey-only login.

---

## 🧪 Comprehensive Test Suite & Quality Assurance

Campus Nexus maintains a 100% green test suite across four distinct testing tiers:

```bash
# 1. Django Full Unit & Integration Test Suite (268 tests in ~3.5 seconds)
python manage.py test campus.tests accounts coordinate --settings=campus.settings.test

# 2. Automated Platform Route Crawler (49 endpoints checked)
python scripts/e2e/run_platform_crawler.py

# 3. Deep Full-Platform E2E User Journey Verification (64 assertions)
python scripts/e2e/run_full_platform_e2e.py

# 4. Strict Release Packager & Artifact Hygiene Scanner
python scripts/package_release.py
```

### Verified Test Results:
* **Django Test Suite:** `345 passed, 0 failures, 0 errors` (Coverage: Guest Demo Mode, Non-Overlapping Multi-Ride Scheduling, Early Launch Mode, Dynamic Capacity Modification, WebAuthn Passkeys, Single-Session Enforcer, Audience Comfort Filters, Concurrency Locks, Anti-Farming Capping, Protected Media Containment, Universal PWA & Service Worker, Mobile REST API v1, Dual Production DBs).
* **Platform Crawler:** `49 passed, 0 failed` across anonymous, authenticated, administrative, and REST API v1 routes.
* **Full E2E Verification:** `64 passed, 0 failed` across end-to-end user journeys (Application &rarr; OTP Verification &rarr; Admin Physical Desk Approval &rarr; Ride Creation &rarr; Waitlist &rarr; Ride Teardown).
* **Release Packager:** Zero secrets, zero bytecode, zero SQLite databases in git tracking (`0.37 MB` clean package).

---

## 📖 Comprehensive Documentation Suite

All system documentation is organized modularly in the [**`docs/`**](docs/README.md) hub:

| Guide | Core Focus | Link |
| :--- | :--- | :--- |
| **Documentation Hub & Sitemap** | Central directory and persona-based reading order | [**`docs/README.md`**](docs/README.md) |
| **System Architecture** | Django 6 + ASGI Channels, WebSockets protocol, DB topology | [**`ARCHITECTURE.md`**](docs/ARCHITECTURE.md) |
| **Guest Demo Mode & Privacy** | Third DB (`guest_demo.sqlite3`), ephemeral sandbox, student privacy & ride redactions | [**`GUEST_DEMO_AND_PRIVACY.md`**](docs/GUEST_DEMO_AND_PRIVACY.md) |
| **Multi-Ride Scheduling** | Non-overlapping 2-hour buffer rules, semester round-trips, conflict prevention | [**`MULTI_RIDE_SCHEDULING.md`**](docs/MULTI_RIDE_SCHEDULING.md) |
| **Authentication & Security** | Dual-channel OTP (Email+SMS), WebAuthn passkeys, single-session lock | [**`AUTHENTICATION_AND_SECURITY.md`**](docs/AUTHENTICATION_AND_SECURITY.md) |
| **Production Deployment** | Authoritative manual: Daphne, systemd, PostgreSQL 16, Redis 7, NGINX, Cloudflare | [**`DEPLOYMENT.md`**](docs/DEPLOYMENT.md) |
| **Operations & Administration** | Operations desk, in-person verification manual, soft-deletion, audit exports | [**`OPERATIONS_AND_ADMINISTRATION.md`**](docs/OPERATIONS_AND_ADMINISTRATION.md) |
| **REST & WebSocket API Reference** | Versioned API (v1), JSON contracts, Bearer token auth, WebSocket packets | [**`API_REFERENCE.md`**](docs/API_REFERENCE.md) |
| **Mobile & Cross-Platform PWA Architecture** | Universal PWA architecture, Service Worker caching, offline engine, passkeys | [**`MOBILE_ARCHITECTURE.md`**](docs/MOBILE_ARCHITECTURE.md) |
| **PWA Scaffolding & Offline Client Guide** | Web App Manifest, Service Worker lifecycle, standalone detection, offline assets | [**`MOBILE_SCAFFOLDING.md`**](docs/MOBILE_SCAFFOLDING.md) |
| **Mechanical Zen & Themes** | Clockwork watermark SVG, gear physics, scatter animations, theme accents | [**`MECHANICAL_ZEN_AND_THEMES.md`**](docs/MECHANICAL_ZEN_AND_THEMES.md) |
| **Audit-Driven Bug Register** | P0-P3 security vulnerability tracking, race condition fixes, verification proofs | [**`BUG_REGISTER.md`**](docs/BUG_REGISTER.md) |
| **Architectural Baseline Inventory** | Phase 0 baseline inventory, application boundaries, and dependency matrix | [**`AUDIT_BASELINE.md`**](docs/AUDIT_BASELINE.md) |
| **Engineering Manifesto** | Founder philosophy, solving campus friction, Spectre/CrowdStrike inspiration | [**`MANIFESTO.md`**](MANIFESTO.md) |
| **Codebase Taxonomy Map** | 30KB comprehensive file-by-file directory guide and domain map | [**`PROJECT_MAP.md`**](PROJECT_MAP.md) |

---

## 💻 Local Development Quickstart

```bash
# 1. Clone repository
git clone https://github.com/Abdus-Samad-Saiyed/Campus-Nexus.git
cd Campus-Nexus

# 2. Create and activate virtual environment
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows:
.\venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env

# 5. Interactive Database Setup & User Provisioning
python setup.py
# (Or apply migrations directly: python manage.py migrate --settings=campus.settings.dev)

# 6. Run automated test suite
python manage.py test campus.tests accounts coordinate --settings=campus.settings.test

# 7. Start development server
# Standard Mode (with Physical Verification Desk):
python manage.py runserver 0.0.0.0:8000 --settings=campus.settings.dev

# Or Early Launch Mode (Instant Self-Declaration for Transit Rush):
python manage.py runserver --early-launch 0.0.0.0:8000 --settings=campus.settings.dev
```

Access the development portal at `http://127.0.0.1:8000/`.

---

## 👤 Author & Architecture Lead

**Abdus Samad Saiyed**  
*Enrollment:* `2024BCSE065`  
*Department:* Computer Science & Engineering  
*Institution:* National Institute of Technology Srinagar (NIT Srinagar)  
*GitHub:* [@Abdus-Samad-Saiyed](https://github.com/Abdus-Samad-Saiyed)  
*LinkedIn:* [abdussamadsaiyed](https://www.linkedin.com/in/abdussamadsaiyed)  

---

*Sic Itur Ad Astra.*
