# Campus Nexus — Guest Demo Mode & Student Privacy Specification

> **Platform Version:** 2.5.0 (Guest Demo & Multi-Ride Release)  
> **Institution:** National Institute of Technology Srinagar (NIT Srinagar)  
> **Maintainer:** Abdus Samad Saiyed (`2024BCSE065`)  

---

## 1. Executive Summary & Purpose

Campus Nexus and its flagship module **COORDINATE** are engineered specifically for the verified student body of NIT Srinagar. However, external stakeholders (prospective students, recruiters, academic evaluators, and external visitors) require the ability to inspect platform capabilities, user experience, and ride booking mechanics without:
1. Requiring institutional student credentials (`@nitsri.ac.in` email or enrollment numbers).
2. Compromising real students' privacy, whereabouts, or security on the public internet.

The **Guest Demo Mode** provides an isolated, ephemeral demonstration sandbox that allows visitors to explore the platform safely while enforcing strict zero-leakage student privacy controls.

---

## 2. Architectural Pillars

```
+-----------------------------------------------------------------------------+
|                            EXTERNAL DEMO VISITOR                            |
+-----------------------------------------------------------------------------+
                                      |
                    Hits /demo/ (or "Explore Guest Demo")
                                      |
                                      v
+-----------------------------------------------------------------------------+
|                            GUEST DEMO SANDBOX                               |
|                                                                             |
|  1. Authenticated as synthetic user: GUEST_DEMO                             |
|  2. Session tagged: request.session['is_guest_demo'] = True                 |
|  3. Bound to unique browser session key                                     |
+-----------------------------------------------------------------------------+
          |                                                   |
          | Read-Only Redacted Queries                        | Read / Write Ephemeral
          v                                                   v
+-------------------------------+           +---------------------------------+
|   DEFAULT DATABASE            |           |   THIRD DATABASE                |
|   (db.sqlite3 / Postgres)     |           |   (guest_demo.sqlite3)          |
|                               |           |                                 |
| - Real student rides queried  |           | - Isolated from default DB      |
| - Identifies & times redacted |           | - Stores guest test rides       |
| - Cannot join or enter rooms  |           | - Tagged with demo_session_key  |
|                               |           | - Purged on exit or expiry      |
+-------------------------------+           +---------------------------------+
```

---

## 3. Database Isolation: The Third Database (`guest_demo.sqlite3`)

Campus Nexus maintains three distinct database configurations in `campus/settings/base.py`:
1. **`default` (`db.sqlite3` / PostgreSQL):** Production relational database holding permanent student records, verified profiles, and active campus rides.
2. **`temp_early_access` (`temp_early_access.sqlite3`):** Ephemeral database used during `--early-launch` mode for student self-declaration transit rushes.
3. **`guest_demo` (`guest_demo.sqlite3`):** Isolated sandbox database exclusively dedicated to storing ephemeral guest demo test bookings.

### Key Invariants:
- **Zero Cross-Contamination:** Guest demo rides are written to `RideRequest.objects.using('guest_demo')`. Real students never query `guest_demo.sqlite3`.
- **Session-Scoped Ephemerality:** Demo rides store `demo_session_key = request.session.session_key`. A guest only sees test rides created within their own browser session.
- **Session Termination Purge:** When a guest clicks **Exit Demo** or calls `/demo/exit/`, all demo rides and participant records matching their session key are deleted from `guest_demo.sqlite3`.
- **Garbage Collection:** Any demo ride older than 2 hours is automatically pruned upon demo entry.

---

## 4. Student Privacy & Data Redaction Rules

When a visitor is in Guest Demo Mode (`is_guest_demo(request) == True`):

| Data Field | Normal Student View | Guest Demo Mode View | Rationale |
| :--- | :--- | :--- | :--- |
| **Route** | `NIT Srinagar → Srinagar Airport` | `NIT Srinagar → Srinagar Airport` | Shows platform coverage and utility. |
| **Occupancy** | `2 / 4 Occupied` | `2 / 4 Occupied` | Shows capacity management in action. |
| **Departure Time** | `06:15 AM · Monday, Nov 30` | `Scheduled Departure (Protected in Demo)` | **Zero location tracking:** Protects students' travel schedule and physical whereabouts from strangers. |
| **Host Name** | `Abdus Samad Saiyed` | `Verified Student (Protected)` | Protects student identity from wide internet scraping. |
| **Host Enrollment** | `2024BCSE065` | `[Hidden]` | Institutional credentials withheld. |
| **Host Rating** | `100% on-time` | `[Hidden]` | Reputation metrics withheld. |
| **Host Profile Link** | `href="/profile/2024BCSE065/"` | `[Disabled]` | Prevents navigation to student profile. |
| **Co-Riders List** | Badges with passenger names & enrollments | `X Students Joined (Identities Protected)` | Protects all confirmed passenger identities. |
| **Ride Action Button**| `Request to Join` / `Open Room` | `Join Ride (Demo Restricted)` | Triggers modal explaining demo mode protection. Real rides cannot be joined. |
| **Live Coordination Room**| Accessible to confirmed riders | **Blocked (Redirect with Warning)** | Live chat, pickup notes, and passenger details are strictly private. |

---

## 5. Complete Quarantine of Sensitive Endpoints

Guests in demo mode are quarantined from all internal campus systems:

1. **Student Directory (`/directory/` / `user_search`):** Blocked. Redirects to dashboard with `messages.error`.
2. **Student Profiles (`/profile/<enrollment>/`):** Blocked. Protected by `@verified_student_required` and `can_view_profile_field`.
3. **Direct Messages & Inbox (`/messages/` & `/messages/<enrollment>/`):** Blocked. Quarantined from 1-on-1 student messaging.
4. **WebSockets (`/ws/chat/` & `/ws/ride/`):** Rejected at `connect()`. `FriendChatConsumer` and `RideConsumer` drop connections from demo users.
5. **Protected Media (`/media/...`):** Forbidden. `serve_protected_media` returns HTTP 403 Forbidden to any demo request.
6. **Central Policy Guard:** `is_verified_student(user)` returns `False` for any guest user, even if `EARLY_LAUNCH_MODE` is enabled.

---

## 6. Interactive Demo Capabilities

Visitors in Guest Demo Mode are not merely presented with static screenshots; they can experience real platform interactions safely:

- **Browse Redacted Rides:** Filter and sort real student rides by route, visibility, date, and density curves with redaction active.
- **Interactive Booking:** Click **Offer a Ride** to experience the multi-step ride creation form. Submitted rides are saved into `guest_demo.sqlite3` and rendered with a `[DEMO TEST RIDE]` badge.
- **Inspect Coordination Room:** Open the live coordination room for their own demo test ride to inspect the clockwork status indicator, participant layout, and chat interface.
- **Exit Demo:** One-click exit in the top banner or navigation dropdown purges all ephemeral session data and returns to the public landing page.

---

## 7. Verification & Automated Test Coverage

The Guest Demo Mode is verified by the automated test suite in [`coordinate/test_guest_demo.py`](file:///s:/Campus-Project/coordinate/test_guest_demo.py):
- `test_policy_guarantees_guest_is_not_verified_student`: Verifies policies reject guest access to student data.
- `test_guest_demo_entry_and_session`: Verifies `/demo/` sets session state and demo user.
- `test_guest_quarantine_student_directory`: Verifies directory browsing is blocked.
- `test_guest_quarantine_student_profile`: Verifies profile URLs are blocked.
- `test_guest_quarantine_messages_and_chat`: Verifies direct messaging is blocked.
- `test_guest_quarantine_protected_media`: Verifies media access returns 403.
- `test_guest_sees_redacted_student_rides_on_coordinate`: Verifies timing and identity redaction.
- `test_guest_cannot_join_or_waitlist_real_ride`: Verifies join/waitlist actions are rejected.
- `test_guest_cannot_enter_real_ride_live_room`: Verifies live rooms cannot be entered.
- `test_guest_can_create_ephemeral_ride_in_third_db`: Verifies creation in third database and isolation from default DB.
- `test_guest_session_cleanup_on_exit`: Verifies ephemeral data is wiped upon session exit.
