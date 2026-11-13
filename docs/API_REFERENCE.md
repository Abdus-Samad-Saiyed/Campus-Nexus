# HTTP and WebSocket API reference

Reviewed 2026-11-12 against `campus/api/urls.py`, `campus/urls.py`, `coordinate/routing.py`, and the corresponding views and consumers. Route names and serializer behavior in source are authoritative; this page is a navigation aid, not a generated schema.

## Base paths and authentication

- REST endpoints are mounted under `/api/v1/`.
- Browser and PWA views use Django sessions. API authentication helpers accept bearer-token credentials and may accept session cookies for safe methods; inspect `campus/api/auth.py` before building a client that relies on cookie behavior.
- Login and logout are explicit API endpoints. Mutating API endpoints use POST, PATCH, or PUT as indicated by their view and generally require authentication.
- Do not assume all endpoints are public or that an API token is interchangeable with a browser session. Access checks also depend on account verification, relationship, ride participation, and privacy policies.
- JSON error shapes and status codes are implemented in `campus/api/auth.py` and `campus/api/views.py`; they may evolve independently from this summary.

## REST routes

| Method | Path | Purpose / access notes |
| --- | --- | --- |
| `GET` | `/api/v1/` | API root and available service links. |
| `GET` | `/api/v1/catalogue/` | Service catalogue. |
| `GET` | `/api/v1/catalogue/<slug>/` | One service entry. |
| `POST` | `/api/v1/auth/login/` | API sign-in; rate limited. |
| `POST` | `/api/v1/auth/logout/` | Revoke/log out API authentication. |
| `GET`, `PATCH`, `PUT` | `/api/v1/me/` | Read or update the authenticated user's API profile. |
| `POST` | `/api/v1/me/photo/` | Upload the authenticated user's profile photo. |
| `GET` | `/api/v1/rides/` | List rides with supported filters and ordering from `api_ride_list`. |
| `POST` | `/api/v1/rides/create/` | Create a ride; requires applicable student permissions. |
| `GET` | `/api/v1/rides/<ride_id>/` | Ride detail, subject to visibility and participation policy. |
| `POST` | `/api/v1/rides/<ride_id>/join/` | Request or perform a ride join action according to ride policy. |
| `POST` | `/api/v1/rides/<ride_id>/requests/<request_id>/approve/` | Approve a join request when authorized. |
| `POST` | `/api/v1/rides/<ride_id>/requests/<request_id>/decline/` | Decline a join request when authorized. |
| `POST` | `/api/v1/rides/<ride_id>/leave/` | Leave a ride when permitted. |
| `GET` | `/api/v1/my-rides/` | List rides for the authenticated user. |
| `GET` | `/api/v1/notifications/recent/` | Recent notifications. |
| `GET` | `/api/v1/notifications/push/vapid-key/` | Public Web Push application key. |
| `POST` | `/api/v1/notifications/push/subscribe/` | Register a browser push subscription. |
| `POST` | `/api/v1/notifications/push/unsubscribe/` | Remove a browser push subscription. |

The route list is taken from the checked-in URL configuration. Request fields, filters, pagination, and response shapes should be read from the matching view before client implementation; this guide intentionally avoids copied example payloads that had become stale.

## WebSocket routes

| Path | Consumer | Notes |
| --- | --- | --- |
| `/ws/ride/<ride_id>/` | `RideChatConsumer` | Ride coordination for eligible participants. |
| `/ws/chat/direct/<friend_enrollment>/` | `FriendChatConsumer` | Direct chat subject to account and friendship checks. |
| `/ws/notifications/` | `NotificationConsumer` | User notifications. |
| `/ws/operations/` | `OperationsConsumer` | Staff operations updates. |
| `/ws/applications/` | `ApplicationsConsumer` | Application queue updates. |

Connect with `wss://` in HTTPS deployments. `campus/asgi.py`, consumer `connect` methods, and event handlers define the actual handshake, authorization, close codes, and packet formats. Older docs listed different chat paths; those are not current routes.

## Browser-facing endpoints

Some account workflows return HTML or small JSON responses and are not part of the versioned REST API. Current examples include `/login/`, `/apply/`, `/forgot-password/`, `/auth/passkey/...`, `/otp/...`, `/health/`, `/healthz/`, `/manifest.json`, and `/sw.js`. Their routes are registered in `campus/urls.py` and `accounts/urls.py`.

## Client implementation guidance

- Use the documented route and method; do not send secrets in query strings.
- Keep API tokens and Web Push subscription data out of logs and analytics.
- Handle `401`, `403`, `404`, `409`, `429`, and server errors without assuming a particular message string.
- Treat returned profile and ride data as privacy-filtered and do not retain it longer than needed.
- Re-check this guide against URL patterns and view permission checks when changing an endpoint.
