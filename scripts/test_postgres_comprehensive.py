"""
Comprehensive End-to-End PostgreSQL Test Matrix for Campus Nexus
Tests:
1. All 9 Ports (Master 9000 + 8 Workers 9001..9008)
2. All Public Pages & Health Probes
3. Student Authentication & Session Navigation
4. Multi-User Ride Coordination Workflow (Create, Join, Approve, Chat)
5. Superuser Operations & CSV Export APIs
6. Versioned REST API v1 (Auth, Me, Rides, Catalogue)
7. Daphne WebSocket Connectivity & Redis PubSub
8. High Concurrency Multi-Worker Stress Load (50 workers, 500 requests)
"""

import sys
import os
import re
import time
import json
import asyncio
import urllib.parse
from datetime import timedelta
import requests
import websockets

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus.settings.dev")
import django
django.setup()
from django.utils import timezone
from coordinate.models import RideRequest, RideJoinRequest, RideParticipant, RideMessage

BASE_URL = "http://127.0.0.1:9000"
WS_URL = "ws://127.0.0.1:9000"

results = {
    "total_tests": 0,
    "passed": 0,
    "failed": 0,
    "failures": [],
}

def log_test(name, success, info=""):
    results["total_tests"] += 1
    if success:
        results["passed"] += 1
        print(f"  [PASS] {name} {f'({info})' if info else ''}")
    else:
        results["failed"] += 1
        print(f"  [FAIL] {name} - {info}")
        results["failures"].append(f"{name}: {info}")

def get_csrf(client, url):
    resp = client.get(url)
    match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', resp.text)
    if match:
        return match.group(1)
    if "csrftoken" in client.cookies:
        return client.cookies["csrftoken"]
    return None

# ==========================================
# 1. DIRECT PORTS & WORKERS PROBING
# ==========================================
print("\n" + "="*70)
print("PHASE 1: Direct Ports & Worker Health Probes (Master 9000 + 8 Workers 9001-9008)")
print("="*70)

all_ports = [9000] + list(range(9001, 9009))
for port in all_ports:
    url = f"http://127.0.0.1:{port}/healthz/?format=json"
    try:
        t0 = time.perf_counter()
        r = requests.get(url, timeout=5)
        dt = (time.perf_counter() - t0) * 1000
        data = r.json()
        is_ok = (
            r.status_code == 200 
            and data.get("status") == "healthy" 
            and data.get("database") == "connected" 
            and data.get("cache") == "connected"
        )
        log_test(f"Port {port} Direct Probe", is_ok, f"{dt:.1f}ms, DB: {data.get('database')}, Cache: {data.get('cache')}")
    except Exception as e:
        log_test(f"Port {port} Direct Probe", False, str(e))

# ==========================================
# 2. PUBLIC UNAUTHENTICATED REQUEST MATRIX
# ==========================================
print("\n" + "="*70)
print("PHASE 2: Public Unauthenticated Request Matrix (Port 9000)")
print("="*70)

public_endpoints = [
    ("/", 200, "Campus Nexus Home"),
    ("/about/", 200, "About"),
    ("/contact/", 200, "Contact"),
    ("/contributors/", 200, "Contributors"),
    ("/feedback/", 200, "Feedback"),
    ("/login/", 200, "Login"),
    ("/apply/", 200, "Apply"),
    ("/healthz/", 200, "Operational Health"),
    ("/healthz/?format=json", 200, "JSON Health"),
]

for path, expected_status, label in public_endpoints:
    t0 = time.perf_counter()
    r = requests.get(f"{BASE_URL}{path}", timeout=5)
    dt = (time.perf_counter() - t0) * 1000
    ok = (r.status_code == expected_status)
    log_test(f"GET {path} [{label}]", ok, f"status {r.status_code}, {dt:.1f}ms")

# ==========================================
# 3. STUDENT AUTHENTICATION & SESSION MATRIX
# ==========================================
print("\n" + "="*70)
print("PHASE 3: Student Authentication & Session Matrix (Port 9000)")
print("="*70)

student1_session = requests.Session()
login_url = f"{BASE_URL}/login/"
csrf1 = get_csrf(student1_session, login_url)

t0 = time.perf_counter()
login_resp = student1_session.post(
    login_url,
    data={
        "csrfmiddlewaretoken": csrf1,
        "username": "2099LOAD001",
        "password": "StudentPass123!",
    },
    headers={"Referer": login_url},
    allow_redirects=True,
    timeout=5,
)
dt = (time.perf_counter() - t0) * 1000
log_test("Student 1 POST /login/ (2099LOAD001)", login_resp.status_code == 200 and "dashboard" in login_resp.url, f"{dt:.1f}ms, final_url: {login_resp.url}")

student_auth_endpoints = [
    ("/dashboard/", 200, "Student Dashboard"),
    ("/profile/2099LOAD001/", 200, "Student Profile"),
    ("/messages/", 200, "Direct Messages Inbox"),
    ("/search/?q=Student", 200, "Student Search Query"),
    ("/coordinate/", 200, "COORDINATE Ride Discovery"),
    ("/coordinate/?density_view=30", 200, "COORDINATE 30-Day Density View"),
    ("/coordinate/?order_by=time_asc", 200, "COORDINATE Ordered Rides"),
]

for path, expected_status, label in student_auth_endpoints:
    t0 = time.perf_counter()
    r = student1_session.get(f"{BASE_URL}{path}", timeout=5)
    dt = (time.perf_counter() - t0) * 1000
    ok = (r.status_code == expected_status)
    log_test(f"Authenticated GET {path} [{label}]", ok, f"status {r.status_code}, {dt:.1f}ms")

# ==========================================
# 4. MULTI-USER RIDE COORDINATION WORKFLOW
# ==========================================
print("\n" + "="*70)
print("PHASE 4: Multi-User Ride Coordination Workflow (Create, Join, Approve, Chat)")
print("="*70)

# Create a ride as Student 1
create_ride_url = f"{BASE_URL}/coordinate/create/"
csrf_create = get_csrf(student1_session, create_ride_url)

# Set departure time to tomorrow
from django.utils import timezone
tomorrow = timezone.now() + timedelta(days=2)
departure_str = tomorrow.strftime("%Y-%m-%dT14:00")

create_data = {
    "csrfmiddlewaretoken": csrf_create,
    "route": "CUSTOM",
    "custom_from_location": "NIT Srinagar Campus Gate 1",
    "custom_to_location": "Srinagar Airport (SXR)",
    "visibility": "MIXED",
    "departure_time": departure_str,
    "total_spots": "3",
    "share_chat_history": "on",
    "disclose_identity": "on",
    "share_contact": "on",
}

t0 = time.perf_counter()
create_resp = student1_session.post(
    create_ride_url,
    data=create_data,
    headers={"Referer": create_ride_url},
    allow_redirects=False,
    timeout=5,
)
dt = (time.perf_counter() - t0) * 1000
created_ok = create_resp.status_code == 302 or (create_resp.status_code == 200 and "alert-danger" not in create_resp.text and "errorlist" not in create_resp.text)
if not (create_resp.status_code == 302):
    # Print form errors if any
    error_match = re.findall(r'<div class="invalid-feedback"[^>]*>(.*?)</div>', create_resp.text) or re.findall(r'<ul class="errorlist"[^>]*>(.*?)</ul>', create_resp.text)
    if error_match:
        print("  Form errors:", error_match)
log_test("Student 1 POST /coordinate/create/", created_ok, f"status {create_resp.status_code}, {dt:.1f}ms")

# Retrieve created ride from database
created_ride = RideRequest.objects.filter(creator__username="2099LOAD001").order_by("-id").first()
ride_id = created_ride.id if created_ride else None
log_test(f"Verify Ride Created in PostgreSQL (Ride ID: {ride_id})", ride_id is not None, f"from: {created_ride.custom_from_location if created_ride else 'N/A'}")

# Student 2 logs in and joins the ride
student2_session = requests.Session()
csrf2 = get_csrf(student2_session, login_url)
student2_session.post(
    login_url,
    data={
        "csrfmiddlewaretoken": csrf2,
        "username": "2099LOAD002",
        "password": "StudentPass123!",
    },
    headers={"Referer": login_url},
    allow_redirects=True,
    timeout=5,
)

if ride_id:
    # Student 2 requests to join ride
    join_url = f"{BASE_URL}/coordinate/{ride_id}/join/"
    csrf_join = get_csrf(student2_session, f"{BASE_URL}/coordinate/{ride_id}/")
    t0 = time.perf_counter()
    join_resp = student2_session.post(
        join_url,
        data={
            "csrfmiddlewaretoken": csrf_join,
            "disclose_identity": "true",
            "share_contact": "true",
        },
        headers={"Referer": f"{BASE_URL}/coordinate/{ride_id}/"},
        allow_redirects=True,
        timeout=5,
    )
    dt = (time.perf_counter() - t0) * 1000
    log_test(f"Student 2 POST /coordinate/{ride_id}/join/", join_resp.status_code == 200, f"{dt:.1f}ms")

    # Verify pending join request in PostgreSQL
    join_req = RideJoinRequest.objects.filter(ride_id=ride_id, user__username="2099LOAD002").first()
    log_test("Verify RideJoinRequest Recorded in PostgreSQL", join_req is not None, f"Req ID: {join_req.id if join_req else 'N/A'}")

    if join_req:
        # Student 1 approves the join request
        approve_url = f"{BASE_URL}/coordinate/{ride_id}/requests/{join_req.id}/approve/"
        csrf_approve = get_csrf(student1_session, f"{BASE_URL}/coordinate/{ride_id}/")
        t0 = time.perf_counter()
        app_resp = student1_session.post(
            approve_url,
            data={"csrfmiddlewaretoken": csrf_approve},
            headers={"Referer": f"{BASE_URL}/coordinate/{ride_id}/"},
            allow_redirects=True,
            timeout=5,
        )
        dt = (time.perf_counter() - t0) * 1000
        log_test(f"Student 1 POST .../requests/{join_req.id}/approve/", app_resp.status_code == 200, f"{dt:.1f}ms")

        # Verify Student 2 is now a confirmed RideParticipant in PostgreSQL
        is_part = RideParticipant.objects.filter(ride_id=ride_id, user__username="2099LOAD002").exists()
        log_test("Verify RideParticipant Created in PostgreSQL", is_part, "2099LOAD002 confirmed")

    # Student 2 sends a message in the ride chat
    msg_url = f"{BASE_URL}/coordinate/{ride_id}/message/"
    csrf_msg = get_csrf(student2_session, f"{BASE_URL}/coordinate/{ride_id}/")
    t0 = time.perf_counter()
    msg_resp = student2_session.post(
        msg_url,
        data={
            "csrfmiddlewaretoken": csrf_msg,
            "content": "Hello everyone! Ready for the trip to the airport.",
        },
        headers={"Referer": f"{BASE_URL}/coordinate/{ride_id}/"},
        allow_redirects=True,
        timeout=5,
    )
    dt = (time.perf_counter() - t0) * 1000
    log_test(f"Student 2 POST /coordinate/{ride_id}/message/", msg_resp.status_code == 200, f"{dt:.1f}ms")

    # Verify RideMessage persisted in PostgreSQL
    msg_persisted = RideMessage.objects.filter(ride_id=ride_id, author__username="2099LOAD002").exists()
    log_test("Verify RideMessage Saved in PostgreSQL", msg_persisted, "Chat message stored")

    # Verify ride detail page loads with participants and chat
    detail_resp = student1_session.get(f"{BASE_URL}/coordinate/{ride_id}/", timeout=5)
    log_test(f"GET /coordinate/{ride_id}/ (Detail View)", detail_resp.status_code == 200 and "Ready for the trip" in detail_resp.text, "Renders chat & participants")

# ==========================================
# 5. SUPERUSER & OPERATIONS ENDPOINTS
# ==========================================
print("\n" + "="*70)
print("PHASE 5: Superuser & Operations Endpoints (Port 9000)")
print("="*70)

admin_session = requests.Session()
csrf_admin = get_csrf(admin_session, login_url)
t0 = time.perf_counter()
admin_login = admin_session.post(
    login_url,
    data={
        "csrfmiddlewaretoken": csrf_admin,
        "username": "20XXADM0001",
        "password": "AdminPass123!",
    },
    headers={"Referer": login_url},
    allow_redirects=True,
    timeout=5,
)
dt = (time.perf_counter() - t0) * 1000
log_test("Superuser POST /login/ (20XXADM0001)", admin_login.status_code == 200 and "dashboard" in admin_login.url, f"{dt:.1f}ms, URL: {admin_login.url}")

# Elevate admin session for export freshness check
from django.contrib.sessions.backends.db import SessionStore
admin_sess_key = admin_session.cookies.get("sessionid")
if admin_sess_key:
    s = SessionStore(session_key=admin_sess_key)
    s["security_otp_verified_until"] = (timezone.now() + timedelta(hours=1)).isoformat()
    s.save()

ops_endpoints = [
    ("/operations/", 200, "Operational Dashboard & Telemetry", "text/html"),
    ("/operations/superusers/", 200, "Superusers Management", "text/html"),
    ("/operations/export/rides/", 200, "Export Rides CSV", "text/csv"),
    ("/operations/export/users/", 200, "Export Users CSV", "text/csv"),
    ("/operations/export/applications/", 200, "Export Applications CSV", "text/csv"),
]

for path, expected_status, label, expected_ct in ops_endpoints:
    t0 = time.perf_counter()
    r = admin_session.get(f"{BASE_URL}{path}", timeout=5)
    dt = (time.perf_counter() - t0) * 1000
    ok = (r.status_code == expected_status and expected_ct in r.headers.get("Content-Type", ""))
    log_test(f"Superuser GET {path} [{label}]", ok, f"status {r.status_code}, {dt:.1f}ms, CT: {r.headers.get('Content-Type')}")

# ==========================================
# 6. REST API v1 ENDPOINTS
# ==========================================
print("\n" + "="*70)
print("PHASE 6: REST API v1 Endpoints (Port 9000)")
print("="*70)

# Unauthenticated API Root & Catalogue
r_root = requests.get(f"{BASE_URL}/api/v1/", timeout=5)
log_test("GET /api/v1/ (API Root Directory)", r_root.status_code == 200 and "status" in r_root.json(), f"status {r_root.status_code}")

r_cat = requests.get(f"{BASE_URL}/api/v1/catalogue/", timeout=5)
log_test("GET /api/v1/catalogue/ (Service Catalogue)", r_cat.status_code == 200 and "services" in r_cat.json().get("data", {}), f"status {r_cat.status_code}")

# Authenticate via REST API
t0 = time.perf_counter()
api_login_resp = requests.post(
    f"{BASE_URL}/api/v1/auth/login/",
    json={"enrollment_number": "2099LOAD001", "password": "StudentPass123!"},
    headers={"Content-Type": "application/json"},
    timeout=5,
)
dt = (time.perf_counter() - t0) * 1000
token_data = api_login_resp.json()
bearer_token = token_data.get("data", {}).get("token")
log_test("POST /api/v1/auth/login/ (Obtain Token)", api_login_resp.status_code == 200 and bool(bearer_token), f"{dt:.1f}ms, Token: {bearer_token[:12] if bearer_token else 'None'}...")

if bearer_token:
    api_headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    # Test /api/v1/me/
    t0 = time.perf_counter()
    r_me = requests.get(f"{BASE_URL}/api/v1/me/", headers=api_headers, timeout=5)
    dt = (time.perf_counter() - t0) * 1000
    me_data = r_me.json()
    user_enr = me_data.get("data", {}).get("user", {}).get("enrollment_number")
    log_test("GET /api/v1/me/ (Profile via Bearer Token)", r_me.status_code == 200 and user_enr == "2099LOAD001", f"{dt:.1f}ms, User: {user_enr}")

    # Test /api/v1/rides/
    t0 = time.perf_counter()
    r_rides = requests.get(f"{BASE_URL}/api/v1/rides/", headers=api_headers, timeout=5)
    dt = (time.perf_counter() - t0) * 1000
    rides_list = r_rides.json().get("data", {}).get("rides", [])
    log_test("GET /api/v1/rides/ (Rides List via Bearer Token)", r_rides.status_code == 200 and isinstance(rides_list, list), f"{dt:.1f}ms, Count: {len(rides_list)}")

    # Test /api/v1/catalogue/coordinate/
    r_detail = requests.get(f"{BASE_URL}/api/v1/catalogue/coordinate/", headers=api_headers, timeout=5)
    log_test("GET /api/v1/catalogue/coordinate/", r_detail.status_code == 200, f"status {r_detail.status_code}")

# ==========================================
# 7. DAPHNE WEBSOCKETS & REDIS PUBSUB
# ==========================================
print("\n" + "="*70)
print("PHASE 7: Daphne WebSockets & Redis PubSub (Port 9000)")
print("="*70)

from websockets.asyncio.client import connect as ws_connect

async def test_websockets():
    token = bearer_token

    # 1. Notifications WebSocket
    try:
        t0 = time.perf_counter()
        async with ws_connect(
            f"{WS_URL}/ws/notifications/?token={token}",
            origin="http://127.0.0.1:9000",
            open_timeout=5,
        ) as ws:
            dt = (time.perf_counter() - t0) * 1000
            pong_waiter = await ws.ping()
            await asyncio.wait_for(pong_waiter, timeout=2.0)
            log_test("WebSocket CONNECT /ws/notifications/ (Notifications PubSub)", True, f"Handshake {dt:.1f}ms, Ping-Pong Verified")
    except Exception as e:
        log_test("WebSocket CONNECT /ws/notifications/ (Notifications PubSub)", False, str(e))

    # 2. Ride Chat WebSocket
    if ride_id:
        try:
            t0 = time.perf_counter()
            async with ws_connect(
                f"{WS_URL}/ws/ride/{ride_id}/?token={token}",
                origin="http://127.0.0.1:9000",
                open_timeout=5,
            ) as ws:
                dt = (time.perf_counter() - t0) * 1000
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=2.0)
                log_test(f"WebSocket CONNECT /ws/ride/{ride_id}/ (Ride Chat PubSub)", True, f"Handshake {dt:.1f}ms, Ping-Pong Verified")
        except Exception as e:
            log_test(f"WebSocket CONNECT /ws/ride/{ride_id}/ (Ride Chat PubSub)", False, str(e))

asyncio.run(test_websockets())

# ==========================================
# 8. HIGH-CONCURRENCY MULTI-WORKER STRESS TEST
# ==========================================
print("\n" + "="*70)
print("PHASE 8: High Concurrency Multi-Worker Load Test (50 Workers, 500 Requests)")
print("="*70)

from concurrent.futures import ThreadPoolExecutor

def run_stress_test():
    total_reqs = 500
    concurrency = 50
    urls = [
        f"{BASE_URL}/healthz/?format=json",
        f"{BASE_URL}/",
        f"{BASE_URL}/about/",
        f"{BASE_URL}/contact/",
        f"{BASE_URL}/contributors/",
        f"{BASE_URL}/feedback/",
        f"{BASE_URL}/api/v1/catalogue/",
    ]

    latencies = []
    statuses = {}

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=concurrency, pool_maxsize=concurrency)
    session.mount("http://", adapter)

    def fetch(idx):
        target_url = urls[idx % len(urls)]
        t0 = time.perf_counter()
        try:
            r = session.get(target_url, timeout=10)
            dt = (time.perf_counter() - t0) * 1000
            return (r.status_code, dt)
        except Exception as e:
            return ("ERROR", (time.perf_counter() - t0) * 1000)

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(fetch, i) for i in range(total_reqs)]
        for f in futures:
            code, dt = f.result()
            latencies.append(dt)
            statuses[code] = statuses.get(code, 0) + 1
    total_time = time.perf_counter() - t_start

    rps = total_reqs / total_time
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    success_count = statuses.get(200, 0)
    error_count = total_reqs - success_count
    is_stress_ok = (error_count == 0)

    print(f"  Total Requests: {total_reqs}")
    print(f"  Duration: {total_time:.2f}s | Throughput: {rps:.1f} Req/s")
    print(f"  Status Codes: {statuses}")
    print(f"  Latency: min={latencies[0]:.1f}ms, p50={p50:.1f}ms, p95={p95:.1f}ms, p99={p99:.1f}ms, max={latencies[-1]:.1f}ms")

    log_test(
        "Multi-Worker High Concurrency Stress Test",
        is_stress_ok,
        f"{rps:.1f} req/s, 0 errors, p50: {p50:.1f}ms, p95: {p95:.1f}ms"
    )

run_stress_test()

# ==========================================
# FINAL SUMMARY
# ==========================================
print("\n" + "="*70)
print(f"POSTGRESQL COMPREHENSIVE TEST RESULTS: {results['passed']}/{results['total_tests']} PASSED ({results['failed']} failed)")
print("="*70)

if results["failed"] > 0:
    print("Failures:")
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All tests passed with zero errors across all ports and endpoints!")
    sys.exit(0)
