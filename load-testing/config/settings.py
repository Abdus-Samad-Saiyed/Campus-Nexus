"""
Campus Nexus — Load Testing Configuration Settings
Configurable via environment variables with safe defaults.
"""

import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOADTEST_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = LOADTEST_DIR / "reports"
TEST_DATA_DIR = LOADTEST_DIR / "test-data"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Target configuration
TARGET_URL = os.environ.get("CAMPUS_TARGET_URL", "http://127.0.0.1:8000").rstrip("/")
EXTERNAL_URL = os.environ.get("CAMPUS_EXTERNAL_URL", "https://dev.campus-nexus.online").rstrip("/")

# Credentials & Pool Configuration
TEST_PASSWORD = os.environ.get("CAMPUS_LOADTEST_PASSWORD", "LoadTest@Pass123!")
TEST_USERS_COUNT = int(os.environ.get("CAMPUS_LOADTEST_USERS_COUNT", "50"))
ACTIVE_CREDENTIALS_FILE = TEST_DATA_DIR / "active_test_users.json"

# Realistic User Profile Weightings (Sum = 100)
WEIGHT_ANONYMOUS = int(os.environ.get("WEIGHT_ANONYMOUS", "15"))
WEIGHT_AUTHENTICATED = int(os.environ.get("WEIGHT_AUTHENTICATED", "55"))
WEIGHT_TRANSACTIONAL = int(os.environ.get("WEIGHT_TRANSACTIONAL", "25"))
WEIGHT_ADMIN = int(os.environ.get("WEIGHT_ADMIN", "5"))

# Think time between user requests (seconds)
WAIT_TIME_MIN = float(os.environ.get("WAIT_TIME_MIN", "1.0"))
WAIT_TIME_MAX = float(os.environ.get("WAIT_TIME_MAX", "3.0"))

# Engineering Performance Targets
TARGET_ERROR_RATE = float(os.environ.get("TARGET_ERROR_RATE", "1.0"))       # Max 1% errors
TARGET_P95_LATENCY_MS = float(os.environ.get("TARGET_P95_LATENCY_MS", "500.0")) # 500 ms
TARGET_P99_LATENCY_MS = float(os.environ.get("TARGET_P99_LATENCY_MS", "1500.0")) # 1500 ms
TARGET_CPU_MAX_PCT = float(os.environ.get("TARGET_CPU_MAX_PCT", "80.0"))    # Max 80% CPU
