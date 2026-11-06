# Campus Nexus: Architecture & Baseline Inventory (Phase 0)

**Generated:** 2026-10-13  
**Status:** Baseline established. No application code or database schema modified.

---

## 1. Product Model & Repository Context

- **Repository:** `Campus-Project` (root on disk: `S:\Campus-Project`).
- **Product Name:** Campus Nexus (umbrella campus-services platform).
- **Existing Service:** COORDINATE (peer-to-peer campus ride sharing).
- **Institutional Context:** NIT Srinagar.
- **Licensing:** MIT License intended for all modules; `LICENSE` file is currently **MISSING** from the repository root (see `LIC-01`).

---

## 2. Technical Stack & Installed Dependencies

### Python & Django Runtime
- **Python:** 3.14.3 (Windows x86_64)
- **Django:** 6.0.5
- **ASGI / WebSockets:** `channels` (4.3.2), `asgiref` (3.11.1), `daphne` (installed/configured)
- **Configuration:** `django-environ` (0.14.0)
- **Static Assets:** `whitenoise` (6.12.0)
- **Frontend Enhancements:** `django-htmx` (1.27.0), Bootstrap 5.3.3 (CDN), Bootstrap Icons 1.11.3 (CDN), Inter font (Google Fonts)
- **Database Driver (Prod):** `psycopg[binary]` (3.3.6) installed in dependencies

---

## 3. Architecture & Application Boundaries

```
Campus-Project/
├── campus/                     # Project configuration & settings package
│   ├── settings/
│   │   ├── base.py             # Common settings, django-environ, apps, middleware
│   │   ├── dev.py              # In-memory channels layer, console email, DEBUG=True
│   │   └── prod.py             # DEBUG=False, security headers, SSL/HSTS
│   ├── asgi.py                 # ProtocolTypeRouter (HTTP + Channels WebSocket auth)
│   ├── wsgi.py                 # Standard WSGI entrypoint
│   └── urls.py                 # Root URL router
├── accounts/                   # Shared student identity & administration
│   ├── models.py               # User (AbstractUser), AccountApplication, FriendRequest, Friendship
│   ├── views.py                # landing, dashboard, profile_view, apply_account, admin_analytics, ...
│   ├── forms.py                # AccountApplicationForm
│   ├── urls.py                 # Route definitions
│   └── management/commands/    # create_debug_accounts
├── coordinate/                 # COORDINATE ride-sharing domain service
│   ├── models.py               # RideRequest, RideParticipant, RideReview, RideMessage
│   ├── views.py                # ride_list, create_ride, ride_detail, join_ride, leave_ride, ...
│   ├── consumers.py            # RideChatConsumer (AsyncWebsocketConsumer)
│   ├── routing.py              # ws/ride/<ride_id>/ route
│   └── forms.py                # RideRequestForm, RideReviewForm, RideMessageForm
├── static/                     # CSS & client-side scripts
│   ├── css/main.css            # Dark glassmorphism design tokens & styles
│   └── js/chat.js              # WebSocket chat client with fallback
└── templates/                  # Server-side HTML templates
    ├── base.html
    ├── landing.html
    ├── accounts/
    └── coordinate/
```

---

## 4. Secret & Sensitive Data Findings

1. **Development Credentials in Documentation:**
   - `debug_accounts.md` documents test accounts with their shared password in plain text.
   - `accounts/management/commands/create_debug_accounts.py` defines `DEBUG_PASSWORD` in cleartext.
2. **Production Default Secrets:**
   - `campus/settings/base.py`: `SECRET_KEY` falls back to `'insecure-dev-key-change-me'` when `DJANGO_SECRET_KEY` is unset.
   - `create_debug_accounts` has no explicit guard against running in production (`DEBUG=False`).
3. **Container Build Context & Ignore Files:**
   - `.dockerignore` is **MISSING**. Without it, local databases, development `.env`, virtual environment folders, and secret dumps could be copied into Docker images during `COPY . .`.
4. **Project Dump Utility (`dump_project.py`):**
   - Untracked script that recursively dumps all project text files into `project_dump.txt`. Does not explicitly exclude `.env`, `debug_accounts.md`, or sensitive credential files.

---

## 5. Existing Test Baseline

- **Total Tests Discovered:** 15 (in `accounts/tests.py` and `coordinate/tests.py`).
- **Test Command:** `python manage.py test`
- **Result:** `FAILED (failures=5, errors=1)`
- **Pass Count:** 9 passed.
- **Failures / Errors:**
  1. `accounts.tests.AccountWorkflowTests.test_valid_application_normalizes_and_hashes_password`: `ERROR` (Form expects `confirm_password` but test payload omitted it; form fails validation, record is not created).
  2. `accounts.tests.AccountWorkflowTests.test_approval_requires_post_and_admin`: `FAIL` (GET request returns 302 redirect instead of 405 Method Not Allowed).
  3. `accounts.tests.AccountWorkflowTests.test_friend_request_requires_post_and_accepts_once`: `FAIL` (GET request returns 302 redirect instead of 405 Method Not Allowed).
  4. `accounts.tests.AccountWorkflowTests.test_invalid_and_duplicate_applications_are_rejected`: `FAIL` (Enrollment number validation error string mismatch: expected `'Invalid enrollment number format'`, got standard regex validator text).
  5. `coordinate.tests.RideWorkflowTests.test_create_ride_adds_creator_and_capacity_one_is_full`: `FAIL` (Form validation rejects `total_spots=1` because minimum spots is 2; returns 200 with form error instead of 302 redirect).
  6. `coordinate.tests.RideWorkflowTests.test_review_requires_completed_participated_ride`: `FAIL` (View checks `status='COMPLETED'` via `get_object_or_404`, returning 404 instead of 403 when ride is not completed).

---

## 6. Data Preservation & Recovery Plan

- **Development Database:** `db.sqlite3` is ignored by Git and contains local development test records.
- **Preservation Policy:** Do not drop or delete `db.sqlite3`. Automated tests run exclusively against Django's isolated in-memory/test database (`test_db.sqlite3`).
- **Backup Verification:** For any future schema migration, `db.sqlite3` should be backed up to `db.sqlite3.bak` before running migrations, and rollback capability verified.
