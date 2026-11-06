# Campus Nexus — Multi-Ride Scheduling & Conflict Prevention Specification

> **Platform Version:** 2.5.0 (Guest Demo & Multi-Ride Release)  
> **Institution:** National Institute of Technology Srinagar (NIT Srinagar)  
> **Maintainer:** Abdus Samad Saiyed (`2024BCSE065`)  

---

## 1. Problem Statement & Motivation

At university campuses such as NIT Srinagar, student travel is rarely limited to a single one-off journey. Common transit patterns include:
1. **Vacation / Festival Round Trips:** A student departs campus to the airport or railway station for Diwali/Eid, and simultaneously needs to schedule their return cab back to campus several days or weeks later.
2. **Pre-Semester & Post-Exam Planning:** Students frequently plan travel ahead of time across different months (e.g. mid-term break, end-of-semester exam departures).
3. **Inter-city & Local Utility Trips:** Weekend travel to downtown Srinagar (Lal Chowk / TRC) alongside scheduled airport runs.

Previously, a legacy invariant (`user_has_active_ride`) enforced a global lock restricting each student to exactly one active or future ride across the entire platform. This proved overly restrictive, forcing students to wait until they arrived at their destination before scheduling their return trip—risking full cabs and lost coordination opportunities.

---

## 2. The Non-Overlapping Buffer Invariant

Under the updated architecture, students are permitted to create, host, and join **multiple rides**, provided those rides **do not overlap in time**.

### Overlap Definition & Buffer Window
A ride departing at time $T_{new}$ overlaps with an existing confirmed ride departing at time $T_{existing}$ if:

$$|T_{new} - T_{existing}| < 2\text{ hours}$$

```
                          [ Existing Ride Window ]
                  T_existing - 2h    T_existing    T_existing + 2h
  -----------------------|---------------*---------------|-----------------------> Time
     ALLOWED              CONFLICT (OVERLAP)                ALLOWED
  (e.g., Return trip,   (Cannot physically be on         (e.g., Later trip,
   different day)        two moving cabs simultaneously)  next week / month)
```

### Rationale:
- **Physical Transit Time:** The transit duration between the NIT Srinagar campus (Hazratbal) and Srinagar International Airport (SXR) or Srinagar Railway Station (Nowgam) typically ranges between 45 to 90 minutes depending on city traffic and security checkpoints.
- **Physical Impossibility Guard:** A student cannot physically depart in two cabs within 2 hours of each other. Blocking overlapping departures eliminates double-booking and prevents seat hoarding.
- **Student Responsibility & Reputation:** Outside the 2-hour buffer, managing attendance across non-overlapping rides is the student's responsibility. Missing a scheduled cab damages the student's public on-time reliability score and exposes them to proctorial incident reporting.

---

## 3. Enforcement Points & Architecture

Conflict prevention is enforced across four distinct layers:

### 1. Service Layer (`coordinate/services.py`)
- **`user_has_overlapping_ride(user, departure_time, exclude_ride_id=None, buffer_hours=2)`:**
  Queries for confirmed rides (`status__in=['OPEN', 'FULL']`) within `[departure_time - 2h, departure_time + 2h]` where the user is confirmed as host or passenger.
- **`user_can_join_ride(user, ride)`:** Rejects join requests if `user_has_overlapping_ride(user, ride.departure_time)` evaluates to `True`, returning `OVERLAPPING_RIDE_EXISTS`.
- **`approve_ride_join_request`:** Checks overlap before confirming a passenger.
- **`_promote_next_waitlist_passenger`:** Skips waitlist candidates who have since confirmed another overlapping ride.

### 2. View Layer (`coordinate/views.py`)
- **`create_ride`:**
  - Removed blanket active-ride gate on GET. Students can open the creation form anytime.
  - On POST, checks `_user_has_overlapping_ride(request.user, departure_time)`. If a conflict exists, adds a field error on `departure_time`:
    > *"You already have another confirmed ride departing within 2 hours of this time. Rides cannot overlap."*
- **`ride_list`:**
  - Pre-fetches the student's confirmed departure timestamps in a single batch query to prevent N+1 queries.
  - Computes `ride.has_overlap` for every visible ride card.
  - Sets `ride.can_join = False` and `ride.can_waitlist = False` if an overlap conflict exists.

### 3. API Layer (`campus/api/views.py`)
- **`api_ride_create`:** Parses `departure_time` from JSON body and checks `user_has_overlapping_ride(user, dep_time)`. Returns HTTP 409 Conflict with `ACTIVE_RIDE_EXISTS` code if a conflict is detected.
- **`api_ride_join`:** Validates non-overlap before queuing join request.

### 4. User Interface Layer
- **Ride Browser (`templates/coordinate/ride_list.html`):** The **"Propose New Ride"** action is always visible in the header alongside **"My Rides"**, rather than being replaced by "Cancel My Ride".
- **Ride Cards (`templates/coordinate/partials/ride_list_content.html`):** If a ride card conflicts with another confirmed booking, the action button is replaced with:
  ```html
  <button class="btn btn-dark-custom w-100 text-warning" disabled>
      <i class="bi bi-clock-history me-1"></i> Time Conflict (Overlaps)
  </button>
  ```
- **Ride History (`templates/coordinate/my_rides.html`):** Displays all active upcoming non-overlapping rides in chronological sequence, allowing students to inspect each coordination room independently.

---

## 4. Automated Test Coverage

The multi-ride capability and conflict prevention rules are verified by [`coordinate/test_multi_ride.py`](file:///s:/Campus-Project/coordinate/test_multi_ride.py):
1. `test_student_can_create_multiple_non_overlapping_rides`: Verifies scheduling 3 distinct trips (Diwali trip, return trip, exam trip).
2. `test_overlapping_ride_creation_is_rejected`: Verifies creating a ride within 2 hours of an existing booking triggers a validation error.
3. `test_student_can_join_multiple_non_overlapping_rides`: Verifies joining multiple distinct rides hosted by other students.
4. `test_joining_overlapping_ride_is_prevented`: Verifies joining a conflicting ride within 2 hours is strictly prevented.
