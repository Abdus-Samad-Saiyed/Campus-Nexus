"""
Campus Nexus — Comprehensive Locust Benchmark Suite
Combines realistic user archetypes:
  - Profile A: Anonymous Visitor       (15%)
  - Profile B: Authenticated Student   (55%)
  - Profile C: Transactional User      (25%)
  - Profile D: Administrative User     (5%)
"""

import sys
from pathlib import Path

# Ensure load-testing directory is on sys.path
LOADTEST_DIR = Path(__file__).resolve().parent
if str(LOADTEST_DIR) not in sys.path:
    sys.path.insert(0, str(LOADTEST_DIR))

from locust import events
from config.settings import (
    WEIGHT_ANONYMOUS,
    WEIGHT_AUTHENTICATED,
    WEIGHT_TRANSACTIONAL,
    WEIGHT_ADMIN,
    TARGET_URL,
)
from users.anonymous import AnonymousVisitor
from users.authenticated import AuthenticatedStudent
from users.transactional import TransactionalUser
from users.admin import AdministrativeUser
from users.common import init_credentials_pool

# Set archetype weights dynamically
AnonymousVisitor.weight = WEIGHT_ANONYMOUS
AuthenticatedStudent.weight = WEIGHT_AUTHENTICATED
TransactionalUser.weight = WEIGHT_TRANSACTIONAL
AdministrativeUser.weight = WEIGHT_ADMIN


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Invoked when Locust initiates the test session."""
    target = environment.host or TARGET_URL
    print(f"\n{'='*70}")
    print(f" CAMPUS NEXUS — LOCUST BENCHMARK INITIALIZING")
    print(f" Target Host: {target}")
    print(f" Archetype Distribution:")
    print(f"   * Anonymous Visitors:       {WEIGHT_ANONYMOUS}%")
    print(f"   * Authenticated Students:   {WEIGHT_AUTHENTICATED}%")
    print(f"   * Transactional Operations: {WEIGHT_TRANSACTIONAL}%")
    print(f"   * Administrative Desk:      {WEIGHT_ADMIN}%")
    print(f"{'='*70}\n")
    init_credentials_pool()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Invoked when the benchmark concludes."""
    print(f"\n{'='*70}")
    print(f" CAMPUS NEXUS — BENCHMARK COMPLETE")
    print(f"{'='*70}\n")
