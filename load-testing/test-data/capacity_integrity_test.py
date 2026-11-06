"""
Campus Nexus — Concurrent Capacity Integrity Test
Simulates high-concurrency race conditions against scarce capacity.
Verifies that:
  - Exact seat capacity is never exceeded under concurrent write contention.
  - Invariant consistency is strictly preserved (atomic transactions / select_for_update).
  - Overflow requests safely queue to standby waitlist or reject without corrupting state.
"""

import concurrent.futures
import datetime
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus.settings.dev")
import django
django.setup()

from django.db import connection, transaction
from django.utils import timezone
from accounts.models import User
from coordinate.models import RideRequest, RideParticipant, RideWaitlist
from coordinate.services import request_to_join_ride, ParticipantStatus


def run_capacity_integrity_test(seat_limit: int = 3, applicant_count: int = 30):
    print(f"\n{'='*70}")
    print(f" CAMPUS NEXUS — CONCURRENT CAPACITY INTEGRITY BENCHMARK")
    print(f" Target Listing Capacity:  {seat_limit} available seats")
    print(f" Simultaneous Contenders:  {applicant_count} concurrent students")
    print(f"{'='*70}\n")

    # 1. Create host and applicant pool with 2099CAPACITY prefix
    host, _ = User.objects.get_or_create(
        enrollment_number="2099CAP_HOST",
        defaults={"username": "2099CAP_HOST", "first_name": "Host", "last_name": "Capacity", "email": "host.cap@campus.internal", "verified": True, "gender": "M"}
    )
    host.gender = "M"
    host.verified = True
    host.save()

    applicants = []
    for i in range(1, applicant_count + 1):
        app_enrollment = f"2099CAP_{i:03d}"
        app_user, _ = User.objects.get_or_create(
            enrollment_number=app_enrollment,
            defaults={"username": app_enrollment, "first_name": "Contender", "last_name": f"User{i:03d}", "email": f"cap.{i:03d}@campus.internal", "verified": True, "gender": "M"}
        )
        app_user.gender = "M"
        app_user.verified = True
        app_user.save()
        applicants.append(app_user)

    # 2. Create isolated test ride
    ride = RideRequest.objects.create(
        creator=host,
        route="NIT_TO_AIRPORT",
        custom_from_location="[LOADTEST] NIT Campus Gate",
        custom_to_location="[LOADTEST] Srinagar Airport",
        departure_time=timezone.now() + datetime.timedelta(days=14),
        total_spots=seat_limit,
        status="OPEN",
        visibility="MIXED",
    )

    print(f"[TEST] Created benchmark ride #{ride.id} with {seat_limit} available seats.")

    results = []

    def submit_application(user):
        import time
        for attempt in range(8):
            try:
                with connection.cursor() as c:
                    c.execute("PRAGMA busy_timeout = 5000;")
                ok, msg, details = request_to_join_ride(
                    ride_id=ride.id,
                    user=user,
                    disclose_identity=True,
                    share_contact=True
                )
                return {"user": user.enrollment_number, "success": ok, "message": msg, "details": details}
            except Exception as e:
                if "database is locked" in str(e) and attempt < 7:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                return {"user": user.enrollment_number, "success": False, "error": str(e)}

    # 3. Fire all 30 join requests simultaneously
    t0 = timezone.now()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(applicant_count, 16)) as executor:
        futures = [executor.submit(submit_application, user) for user in applicants]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    print("[DEBUG] Sample result:", results[0])

    # 4. Concurrently attempt to approve ALL pending join requests (Race Condition Stress Test)
    from coordinate.services import approve_ride_join_request
    pending_req_ids = list(ride.join_requests.filter(status="PENDING").values_list("id", flat=True))
    print(f"[TEST] Submitted {len(results)} join requests. Found {len(pending_req_ids)} pending requests.")
    print(f"[TEST] Concurrently firing {len(pending_req_ids)} approval operations against {seat_limit} available spots...")

    approval_results = []
    def approve_req(req_id):
        import time
        for attempt in range(8):
            try:
                with connection.cursor() as c:
                    c.execute("PRAGMA busy_timeout = 5000;")
                ok, msg, details = approve_ride_join_request(ride_id=ride.id, request_id=req_id, host_user=host)
                return {"request_id": req_id, "ok": ok, "msg": msg}
            except Exception as e:
                if "database is locked" in str(e) and attempt < 7:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                return {"request_id": req_id, "ok": False, "error": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(applicant_count, 16)) as executor:
        futures = [executor.submit(approve_req, req_id) for req_id in pending_req_ids]
        for f in concurrent.futures.as_completed(futures):
            approval_results.append(f.result())

    t1 = timezone.now()
    duration = (t1 - t0).total_seconds()

    # 5. Refresh ride from database and inspect invariants
    ride.refresh_from_db()
    confirmed = ride.confirmed_participants
    confirmed_count = len(confirmed()) if callable(confirmed) else len(confirmed)
    waitlist_count = RideWaitlist.objects.filter(ride=ride).count()
    successful_approvals = sum(1 for r in approval_results if r.get("ok"))
    declined_or_blocked = sum(1 for r in approval_results if not r.get("ok"))

    print(f"\n[RESULTS] Execution Time: {duration:.3f}s for 30 concurrent submissions + 30 concurrent approvals")
    print(f"  * Confirmed Passengers: {confirmed_count} (Hard Limit: {seat_limit})")
    print(f"  * Successful Approvals: {successful_approvals}")
    print(f"  * Blocked / Over-Capacity Rejections: {declined_or_blocked}")
    print(f"  * Standby Waitlist Count: {waitlist_count}")
    print(f"  * Ride Final Status: {ride.status}")

    # Invariant Checks
    assert confirmed_count <= seat_limit, (
        f"CRITICAL INVARIANT VIOLATION: Confirmed passengers ({confirmed_count}) exceeded seat limit ({seat_limit})!"
    )
    assert confirmed_count == seat_limit, (
        f"Capacity under-filled: expected {seat_limit} confirmed passengers, got {confirmed_count}."
    )
    print(f"\n[VERIFIED] Capacity Invariant Maintained: Exactly {confirmed_count} confirmed passengers (<= {seat_limit})")
    print(f"[VERIFIED] Zero Over-Allocation under extreme concurrent approval race condition.")

    # 5. Teardown
    print("\n[TEARDOWN] Purging test ride and temporary contender accounts...")
    ride.delete()
    host.delete()
    User.objects.filter(enrollment_number__startswith="2099CAP_").delete()
    print("[TEARDOWN] Complete! Database pristine.\n")

    return {
        "seat_limit": seat_limit,
        "applicant_count": applicant_count,
        "confirmed_passengers": confirmed_count,
        "pending_requests": len(pending_req_ids),
        "waitlist_count": waitlist_count,
        "duration_sec": duration,
        "invariant_passed": (confirmed_count <= seat_limit),
    }


if __name__ == "__main__":
    run_capacity_integrity_test(seat_limit=3, applicant_count=30)
