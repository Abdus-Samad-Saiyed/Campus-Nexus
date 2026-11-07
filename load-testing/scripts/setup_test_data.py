"""
Campus Nexus — Load Testing Fixture Setup Script
Safely populates isolated, demarcated test users and rides.
Guarantees zero interference with real student accounts or permanent records.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOADTEST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(LOADTEST_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus.settings.dev")
import django
django.setup()

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection, transaction
from django.utils import timezone
from accounts.models import User
from coordinate.models import RideRequest
from config.settings import TEST_PASSWORD, TEST_USERS_COUNT, ACTIVE_CREDENTIALS_FILE, TEST_DATA_DIR


def backup_database():
    """Create a pristine bitwise backup of active databases before any test data is inserted."""
    backups = []
    for db_alias in ("default", "temp_early_access"):
        if db_alias in settings.DATABASES:
            db_name = settings.DATABASES[db_alias]["NAME"]
            db_path = Path(db_name)
            if db_path.exists() and db_path.is_file():
                backup_path = db_path.with_name(f"{db_path.name}.pre_loadtest_backup")
                shutil.copy2(db_path, backup_path)
                print(f"[BACKUP] Created database snapshot for {db_alias} at {backup_path}")
                backups.append(str(backup_path))
    return backups


def setup_loadtest_fixtures(count: int = TEST_USERS_COUNT, password: str = TEST_PASSWORD):
    """Create isolated test users and test rides across both default and temp_early_access databases."""
    print(f"\n[SETUP] Preparing {count} load-test student accounts + 1 admin account...")
    from scripts.cleanup_test_data import cleanup_loadtest_fixtures
    cleanup_loadtest_fixtures(restore_backup=False)
    backup_files = backup_database()

    users_created = []
    admin_credential = None

    target_dbs = ["default"]
    if "temp_early_access" in settings.DATABASES and Path(settings.DATABASES["temp_early_access"]["NAME"]).exists():
        target_dbs.append("temp_early_access")

    hashed_password = make_password(password)

    for db_alias in target_dbs:
        print(f"[SETUP] Populating test accounts into database alias: '{db_alias}'...")
        with transaction.atomic(using=db_alias):
            for i in range(1, count + 1):
                enrollment = f"2099LOAD{i:03d}"
                user, created = User.objects.using(db_alias).get_or_create(
                    enrollment_number=enrollment,
                    defaults={
                        "username": enrollment,
                        "first_name": "LoadTest",
                        "last_name": f"Student{i:03d}",
                        "email": f"loadtest.{i:03d}@campus.internal",
                        "phone_number": f"99999{i:05d}",
                        "gender": "M" if i % 2 == 1 else "F",
                        "verified": True,
                        "is_active": True,
                        "two_factor_enabled": False,
                        "password": hashed_password,
                    }
                )
                user.username = enrollment
                user.password = hashed_password
                user.verified = True
                user.two_factor_enabled = False
                user.save(using=db_alias)
                if db_alias == "default":
                    users_created.append({
                        "enrollment_number": enrollment,
                        "password": password,
                        "role": "student"
                    })

            # Create admin account (2099LOAD999)
            admin_enrollment = "2099LOAD999"
            admin_user, created = User.objects.using(db_alias).get_or_create(
                enrollment_number=admin_enrollment,
                defaults={
                    "username": admin_enrollment,
                    "first_name": "LoadTest",
                    "last_name": "Administrator",
                    "email": "loadtest.admin@campus.internal",
                    "phone_number": "9999999999",
                    "gender": "M",
                    "verified": True,
                    "is_active": True,
                    "is_staff": True,
                    "is_superuser": True,
                    "two_factor_enabled": False,
                    "password": hashed_password,
                }
            )
            admin_user.username = admin_enrollment
            admin_user.password = hashed_password
            admin_user.is_staff = True
            admin_user.is_superuser = True
            admin_user.verified = True
            admin_user.two_factor_enabled = False
            admin_user.save(using=db_alias)
            if db_alias == "default":
                admin_credential = {
                    "enrollment_number": admin_enrollment,
                    "password": password,
                    "role": "admin"
                }

            # Create dedicated test rides
            creator = User.objects.using(db_alias).get(enrollment_number="2099LOAD001")
            test_routes = [
                ("NIT_TO_AIRPORT", "[LOADTEST] NIT Campus Gate", "[LOADTEST] Srinagar Airport"),
                ("NIT_TO_LAL_CHOWK", "[LOADTEST] NIT Campus Gate", "[LOADTEST] Lal Chowk"),
                ("NIT_TO_RAILWAY_STATION", "[LOADTEST] NIT Campus Gate", "[LOADTEST] Railway Station"),
                ("LAL_CHOWK_TO_NIT", "[LOADTEST] Lal Chowk", "[LOADTEST] NIT Campus Gate"),
                ("AIRPORT_TO_NIT", "[LOADTEST] Srinagar Airport", "[LOADTEST] NIT Campus Gate"),
            ]

            rides_created = []
            base_time = timezone.now() + datetime.timedelta(days=7)

            for idx, (route_key, src, dst) in enumerate(test_routes):
                dep_time = base_time + datetime.timedelta(hours=idx * 4)
                ride, r_created = RideRequest.objects.using(db_alias).get_or_create(
                    creator=creator,
                    custom_from_location=src,
                    custom_to_location=dst,
                    defaults={
                        "route": route_key,
                        "departure_time": dep_time,
                        "total_spots": 3,
                        "visibility": "MIXED",
                        "status": "OPEN",
                    }
                )
                ride.total_spots = 3
                ride.status = "OPEN"
                ride.departure_time = dep_time
                ride.visibility = "MIXED"
                ride.save(using=db_alias)
                rides_created.append(ride.id)

    # Save credentials metadata
    payload = {
        "created_at": timezone.now().isoformat(),
        "database": settings.DATABASES["default"]["NAME"],
        "backup_files": backup_files,
        "password": password,
        "admin": admin_credential,
        "students": users_created,
        "test_rides": rides_created,
    }

    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"[SETUP] Successfully configured {len(users_created)} student accounts, 1 admin, and {len(rides_created)} test rides.")
    print(f"[SETUP] Credentials written to {ACTIVE_CREDENTIALS_FILE}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup load testing test data safely.")
    parser.add_argument("--count", type=int, default=TEST_USERS_COUNT, help="Number of student test accounts")
    parser.add_argument("--password", type=str, default=TEST_PASSWORD, help="Default test password")
    args = parser.parse_args()

    setup_loadtest_fixtures(count=args.count, password=args.password)
