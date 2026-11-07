"""
Campus Nexus — Locust Common Helpers
Provides CSRF extraction, authentication helpers, and thread-safe credential leasing.
"""

import json
import queue
import re
from typing import Optional, Tuple
from config.settings import ACTIVE_CREDENTIALS_FILE, TEST_PASSWORD


_CREDENTIALS_QUEUE = queue.Queue()
_ADMIN_CREDENTIALS = {"enrollment_number": "2099LOAD999", "password": TEST_PASSWORD}
_ACTIVE_TEST_RIDES = []
_INITIALIZED = False


def init_credentials_pool():
    """Load test accounts from JSON fixture into a thread-safe Queue."""
    global _INITIALIZED, _ADMIN_CREDENTIALS, _ACTIVE_TEST_RIDES
    if _INITIALIZED:
        return

    if ACTIVE_CREDENTIALS_FILE.exists():
        try:
            with open(ACTIVE_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                admin = data.get("admin")
                if admin:
                    _ADMIN_CREDENTIALS = admin
                students = data.get("students", [])
                for s in students:
                    _CREDENTIALS_QUEUE.put(s)
                _ACTIVE_TEST_RIDES = data.get("test_rides", [])
                _INITIALIZED = True
                print(f"[AUTH-POOL] Loaded {len(students)} student credentials and {len(_ACTIVE_TEST_RIDES)} test rides.")
                return
        except Exception as e:
            print(f"[WARN] Error reading credentials file: {e}")

    # Fallback synthetic pool if setup script wasn't run
    for i in range(1, 51):
        _CREDENTIALS_QUEUE.put({
            "enrollment_number": f"2099LOAD{i:03d}",
            "password": TEST_PASSWORD,
            "role": "student"
        })
    _INITIALIZED = True


def lease_student_credential():
    """Acquire a unique student credential for the lifetime of a Locust virtual user."""
    init_credentials_pool()
    try:
        cred = _CREDENTIALS_QUEUE.get_nowait()
        # Re-enqueue so users can cycle if more virtual users spawn than accounts
        _CREDENTIALS_QUEUE.put(cred)
        return cred
    except queue.Empty:
        return {"enrollment_number": "2099LOAD001", "password": TEST_PASSWORD}


def get_admin_credential():
    """Retrieve the dedicated administrative account."""
    init_credentials_pool()
    return _ADMIN_CREDENTIALS


def get_active_test_rides():
    """Retrieve the list of active load-test ride IDs."""
    init_credentials_pool()
    return _ACTIVE_TEST_RIDES or [1, 2, 3]


def extract_csrf_token(client, initial_path: str = "/login/") -> Optional[str]:
    """
    Fetch a page and extract Django's CSRF token from either
    cookies or the hidden <input name='csrfmiddlewaretoken'> tag.
    """
    resp = client.get(initial_path, name="[CSRF] Fetch Token")
    token = client.cookies.get("csrftoken")
    if token:
        return token

    # Fallback to regex parse on HTML body
    match = re.search(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']', resp.text)
    if match:
        return match.group(1)
    return None


def authenticate_locust_user(client, enrollment: str, password: str) -> bool:
    """
    Execute standard Django session login with CSRF token.
    Returns True if login succeeded (302 redirect or session established).
    """
    csrf_token = extract_csrf_token(client, "/login/")
    if not csrf_token:
        return False

    client.headers["X-LoadTest-Bypass"] = "campus-nexus-benchmark-active"
    headers = {
        "Referer": f"{client.base_url}/login/",
        "X-CSRFToken": csrf_token,
        "X-LoadTest-Bypass": "campus-nexus-benchmark-active",
    }
    data = {
        "username": enrollment,
        "password": password,
        "csrfmiddlewaretoken": csrf_token,
    }

    with client.post(
        "/login/",
        data=data,
        headers=headers,
        catch_response=True,
        allow_redirects=True,
        name="/login/ [POST]"
    ) as resp:
        if resp.status_code in (200, 302) and ("dashboard" in resp.url or "coordinate" in resp.url or client.cookies.get("sessionid")):
            resp.success()
            return True
        else:
            resp.failure(f"Login failed for {enrollment}: HTTP {resp.status_code}")
            return False
