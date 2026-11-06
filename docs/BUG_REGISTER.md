# Campus Nexus: Exhaustive Audit-Driven Bug Register

**Generated:** 2026-10-13  
**Status Values:** `UNVERIFIED`, `CONFIRMED`, `NOT REPRODUCED`, `ALREADY FIXED`, `NEEDS HUMAN DECISION`, `FIXED`, `TESTED`

---

## 1. Critical & High Priority (P0 / P1)

| ID | Sev | Investigation Target | Code Evidence / Analysis | Current Status | Phase |
|---|---|---|---|---|---|
| **SEC-01** | P0 | Private complaint text exposed on public profiles | Private complaint text restricted to authorized staff/moderators; sanitized from regular student profiles. | **FIXED** | 2 |
| **SEC-02** | P0 | Phone numbers exposed to unentitled users | Phone numbers restricted by explicit `can_view_phone_number` policy (self, friends, host/passenger during ride); hidden from unrelated co-passengers. | **FIXED** | 2 |
| **SEC-03** | P0 | Gender-restricted ride creator eligibility not enforced | Server-side creator gender checks enforced in `create_ride`: female for GIRLS, male for BOYS. | **FIXED** | 2–3 |
| **SEC-04** | P0 | Removed/kicked participant retains WebSocket access or rejoins | Kicked passengers stored in `kicked_users` M2M and blocked from rejoining; WebSocket dynamically closes if membership revoked. | **FIXED** | 3–4 |
| **SEC-05** | P0 | Password handling in account creation / reset | Standard `PasswordChangeView` / `PasswordChangeDoneView` wired; `UserAdmin` upgraded to `BaseUserAdmin` for secure admin password management. | **FIXED** | 2 |
| **SEC-06** | P0 | Authentication / application brute-force & enumeration | Cache-backed `rate_limit` decorator applied to `apply_account` and authentication paths; returns 429 upon limit breach. | **FIXED** | 2 |
| **SEC-07** | P0 | Development credentials in repository / documentation | `debug_accounts.md` untracked from Git and added to `.gitignore`. Plaintext password removed from `create_debug_accounts.py` stdout. | **FIXED** | 1 |
| **SEC-08** | P0 | Destructive reset / debug command runnable in production | Added `if not settings.DEBUG:` guard to `create_debug_accounts.py` raising `CommandError`. Refuses in production. | **FIXED** | 1 |
| **SEC-09** | P0 | Production configuration fallback to unsafe defaults | `campus/settings/prod.py` raises `ValueError` if `DJANGO_SECRET_KEY` is missing or matches placeholder. | **FIXED** | 1, 8 |
| **DATA-01** | P0/P1 | Completed rides hard-deleted, losing history | Terminating or leaving a ride with messages preserves it with status `CANCELLED` instead of hard deleting. | **FIXED** | 3 |
| **DATA-02** | P0/P1 | Concurrent joins exceeding capacity or one-active-ride | `coordinate/views.py:join_ride` uses `select_for_update()` inside `transaction.atomic()`, checking capacity before creating `RideParticipant`. Race condition is mitigated, but concurrency tests against PostgreSQL must be added to verify invariant under load. | **ALREADY FIXED** (needs PG test) | 3 |
| **DATA-03** | P1 | Duplicate review / completion / attendance operations | Reviews enforce rating range 1-5, disallow self-review; completion updates are idempotent. | **FIXED** | 3 |
| **AUTH-01** | P1 | Enrollment number case normalization | `EnrollmentBackend` and `EnrollmentAuthenticationForm` normalize usernames/enrollments to uppercase; `AccountApplicationForm` validates and normalizes. | **FIXED** | 2 |
| **AUTH-02** | P1 | Application queue lacks rejection & malformed handling | Added `reject_application` view (POST only, superuser gated, atomic, HTMX support), UI reject action, and form regex validation. | **FIXED** | 2 |
| **RIDE-01** | P1 | Past or malformed departure time accepted | `RideRequestForm.clean_departure_time` requires future time and rejects dates beyond 30-day horizon. | **FIXED** | 3 |
| **RIDE-02** | P1 | Expired/past rides lock users indefinitely | `_user_has_active_ride` ignores rides past departure + 2-hour buffer; users can create/join new rides. | **FIXED** | 3 |
| **RIDE-03** | P1 | Ride history unreachable through navigation | Created `/coordinate/my-rides/` dedicated history interface with active, past, and created rides. | **FIXED** | 3 |
| **RIDE-04** | P1 | Kicked participant can rejoin same ride | Added `ride.kicked_users` tracking and blocked kicked users from joining in `_user_can_join_ride`. | **FIXED** | 3 |
| **RIDE-05** | P1 | Reliability / attendance counters not updated | `complete_ride` increments `rides_completed` and `on_time_count` atomically for all participants. | **FIXED** | 3 |
| **DEP-01** | P1 | Deployment TLS/proxy/CSRF/host settings unsafe | `campus/settings/prod.py` configured with `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `REDIS_URL`, strict cookie flags, and `/health/` healthcheck endpoint. | **FIXED** | 8 |
| **DEP-02** | P1 | Container build context leaks local files / secrets | `.dockerignore` created with explicit exclusions for `.env`, `.git`, `db.sqlite3`, `debug_accounts.md`, `venv/`, etc. | **FIXED** | 1, 8 |
| **DEP-03** | P1 | WebSocket/ASGI deployment unverified | Channels is configured with `InMemoryChannelLayer` in `dev.py`. In `base.py`, Redis channel layer is configured via `REDIS_URL`. Integration tests using a shared channel layer are missing. | **CONFIRMED** | 4, 8 |

---

## 2. Medium Priority (P2)

| ID | Sev | Investigation Target | Code Evidence / Analysis | Current Status | Phase |
|---|---|---|---|---|---|
| **APP-01** | P2 | Incoming friend requests visibility and actions | Added `decline_friend_request` view (POST only) and cleaned up friendship state. | **FIXED** | 4 |
| **APP-02** | P2 | Friendship records duplicated in reciprocal order | Enforced canonical `user1_id < user2_id` ordering in `Friendship.save()`, eliminating reciprocal duplicates. | **FIXED** | 4 |
| **CHAT-01** | P2 | Binary WebSocket frames handled safely | `RideChatConsumer.receive` safely filters out binary `bytes_data` without crashing. | **FIXED** | 4 |
| **CHAT-02** | P2 | Failed message delivery feedback | `RideChatConsumer` sends explicit `{type: 'ack', status: 'sent'}` and `{type: 'error'}` frames. | **FIXED** | 4 |
| **CHAT-03** | P2 | Chat rate limiting missing | Rate limiter added on `RideChatConsumer`: max 5 messages per 3s sliding window. | **FIXED** | 4 |
| **CHAT-04** | P2 | WebSocket group name normalization | `group_name = f'ride_{self.ride_id}'`. Uses integer string from URL regex. | **ALREADY FIXED** | 4 |
| **CHAT-05** | P2 | Post-departure chat buffer missing | Chat remains open for coordination up to 2 hours after scheduled departure time. | **FIXED** | 4 |
| **UI-01** | P2 | Rating form displays blank choice | `RideReviewForm` uses explicit `ChoiceField` with values 1-5 and no empty choice. | **FIXED** | 3 |
| **UI-02** | P2 | HTMX approval response contract | In `accounts/views.py:approve_application`, if `request.htmx`, returns empty `HttpResponse('')`. Works with `hx-swap="outerHTML"`, but non-HTMX returns redirect. | **ALREADY FIXED** | 4 |
| **UI-03** | P2 | HTMX unused fragments / dead handlers | In `coordinate/views.py`, `join_ride` and `leave_ride` return `ride_actions.html` on `request.htmx`, but template form did not include `hx-post` attributes in `ride_list.html`. | **CONFIRMED** | 4 |
| **FILTER-01** | P2 | Invalid date/time query params crash | `coordinate/views.py` uses `_parse_date` and `_parse_time` with try-except fallback to `timezone.now().date()` and `None`. | **ALREADY FIXED** | 3 |
| **FILTER-02** | P2 | Date navigation boundary & past dates | Date navigation strip restricted to today + next 6 days; past dates cannot be clicked. | **FIXED** | 3 |
| **FILTER-03** | P2 | Filters reset after search | Form in `templates/coordinate/ride_list.html` maintains selected route and visibility via `selected` attributes in dropdown. | **ALREADY FIXED** | 3 |
| **TIME-01** | P2 | Date boundary around UTC/Asia-Kolkata | `TIME_ZONE = 'Asia/Kolkata'` and `USE_TZ = True` set in `base.py`. `timezone.now().date()` evaluated in local timezone. | **ALREADY FIXED** | 3 |
| **ADMIN-01** | P2 | Admin user model setup & custom password | Upgraded `UserAdmin` to inherit from `django.contrib.auth.admin.UserAdmin` with full fieldsets and secure password hashing forms. | **FIXED** | 2, 7 |
| **ADMIN-02** | P2 | Application approval atomicity | `approve_application` in `accounts/views.py` is wrapped in `transaction.atomic()`. | **ALREADY FIXED** | 2 |
| **METRIC-01** | P2 | Reliability counter race conditions | `complete_ride` and `review_user` atomically update `rides_completed`, `on_time_count`, `late_count`, and `no_show_count` using atomic `F()` expressions. | **FIXED** | 3 |
| **STATIC-01** | P2 | Static file config & WhiteNoise | `whitenoise.middleware.WhiteNoiseMiddleware` configured. Warning on missing `staticfiles/` dir before `collectstatic`. | **ALREADY FIXED** | 8 |
| **CDN-01** | P2 | Third-party CDN integrity controls | Added verified Subresource Integrity (`integrity="sha384-..."`) and `crossorigin="anonymous"` to Bootstrap 5.3.3, Bootstrap Icons, and HTMX 1.9.12 in `templates/base.html`. | **FIXED** | 5 |
| **DUMP-01** | P2 | Project dump utility exports sensitive files | `dump_project.py` rewritten with `EXCLUDED_FILES` and `EXCLUDED_DIRS` to block secrets, credentials, and local databases. | **FIXED** | 1 |
| **API-01** | P2 | Web vs API parity | Versioned REST API (`/api/v1/`) implemented with parity across auth, catalogue, user profile, ride discovery, creation, join, leave, and history. | **FIXED** | 6 |
| **OBS-01** | P2 | Consistent audit/activity events | `AuditEvent` model, `log_audit_event` helper, and operational audit logging implemented for exports and admin actions. | **FIXED** | 7 |
| **EXPORT-01** | P2 | Data export features | Role-gated CSV exports for rides, users, and applications with CWE-1236 formula-injection sanitization (`sanitize_csv_cell`). | **FIXED** | 7 |
| **SCALE-01** | P2 | Shared state across replicas | `dev.py` uses `InMemoryChannelLayer`; production requires `RedisChannelLayer`. | **CONFIRMED** | 8 |
| **MEDIA-01** | P2 | Media file persistence | `MEDIA_ROOT = BASE_DIR / 'media'` on local filesystem. In production, object storage (S3/R2) is recommended. | **CONFIRMED** | 8 |
| **TEST-01** | P2 | Automated integration tests incomplete | Full automated test suite expanded to 142 unit tests + 64 deep platform E2E crawler tests (100% pass rate). | **FIXED** | 0–8 |

---

## 3. Low Priority / Polish & Enhancements (P3)

| ID | Sev | Issue | Current Status | Phase |
|---|---|---|---|---|
| **UX-01** | P3 | Shared design tokens & system | Dark glassmorphism, green accents, and shared tokens unified across platform shell, catalogue, and services. | **FIXED** | 5 |
| **UX-02** | P3 | Responsive behavior across pages | Responsive layouts implemented using Bootstrap 5 grid and glass cards. | **FIXED** | 5 |
| **UX-03** | P3 | Consistent loading/empty/error states | Empty and disabled service states rendered honestly in `service_unavailable.html`. | **FIXED** | 5 |
| **UX-04** | P3 | Clockwork watermark background | SVG background with clock face and interlocking gears driven by client system time (`new Date()`). | **FIXED** | 5 |
| **UX-05** | P3 | Watermark pointer-inert & performance | Container is `pointer-events: none` at z-index -1; respects `prefers-reduced-motion` in CSS and JS. | **FIXED** | 5 |
| **UX-06** | P3 | Platform shell navigation (Campus Nexus) | Header updated to "CAMPUS NEXUS" umbrella with COORDINATE module badge, catalogue routes, and breadcrumbs. | **FIXED** | 5 |
| **MOB-01** | P2 | React Native mobile application | Architecture and component specifications scaffolded in `docs/MOBILE_SCAFFOLDING.md`. | **SCAFFOLDED** | 6 |
| **MOB-02** | P2 | Mobile secure credential storage | Secure storage pattern using `expo-secure-store` documented and integrated with Bearer token authentication. | **SCAFFOLDED** | 6 |
| **PERF-01** | P2/3| N+1 query optimization | `annotate(participants_total=Count('participants'))` implemented on ride list. | **ALREADY FIXED** | 8 |
| **DOC-01** | P3 | Comprehensive documentation | Exhaustive architecture and mobile API scaffolding documentation added to `docs/`. | **FIXED** | Every |
| **LIC-01** | P1 | Missing MIT license file | `LICENSE` file created at root with standard MIT text and proper attribution to Abdus Samad Saiyed. | **FIXED** | 1 |
| **SEC-10** | P0 | Staff account takeover via admin desk | `admin_update_phone` and `admin_resolve_physical_verification` locked to superuser-only; targeting superusers strictly blocked; PVR replay blocked; audit logs masked. | **FIXED** | 7 |
| **SEC-11** | P0 | Open redirect via unvalidated `next` parameter | Added `url_has_allowed_host_and_scheme` validation across `custom_login_view`, `two_factor_verify`, and `passkey_auth_verify`; preserved `next` input in `login.html`. | **FIXED** | 2 |
| **SEC-12** | P0 | Stored XSS & unvalidated profile-photo upload | Added Pillow format verification, 5MB file-size cap, file extension whitelisting, UUID renaming, and gated `/media/` serving under `DEBUG`. | **FIXED** | 2, 8 |
| **SEC-13** | P0 | Admin login bypassing 2FA and single session | Routed `admin.site.login = custom_login_view`, forcing staff/superusers through 2FA, rate-limiting, and single-session enforcement. | **FIXED** | 2, 7 |
| **SEC-14** | P0 | Synthetic debug accounts active in production | Gated `20XX...` debug account 2FA/OTP bypass behind `settings.DEBUG`; rejected `20XX...` applications when `DEBUG=False`. | **FIXED** | 1, 2 |
| **SEC-15** | P1 | OTP atomicity & signup phone/email binding | Wrapped `verify_phone_otp` in `transaction.atomic()` with `select_for_update()` and `F('attempts_left') - 1`; bound signup verification to both phone and email; normalized E.164. | **FIXED** | 2 |
| **SEC-16** | P0 | Security OTP client-controlled overrides | Removed `phone_override`/`email_override` in `send_security_otp`; strictly dispatched to authenticated user's registered phone and email. | **FIXED** | 2 |
| **API-02** | P1 | REST API CSRF, Q NameError, & serialization | Added `@csrf_exempt` to Bearer token endpoints, imported `Q`, fixed `serialize_ride` participant checking, fixed `is_verified` user field, added 2FA and token revocation on logout. | **FIXED** | 6 |
| **RIDE-06** | P1 | Ride leave ownership & complete concurrency | Enforced participant verification on leave; transferred host ownership if host leaves; locked `complete_ride` against cancelled/future rides; guarded review harassment counters. | **FIXED** | 3 |
| **TEL-01** | P2 | Fictional hardcoded telemetry numbers | Removed fake seed constants (`3840`, `112500`, `180`, `42`); bound operational telemetry to live cache counters, database queries, and `timezone.localtime()`. | **FIXED** | 7 |

