"""
Campus Nexus — Load Testing Teardown & Cleanup Script
Safely and completely purges all load-testing data.
Restores the database to its exact pre-test state.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOADTEST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(LOADTEST_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus.settings.dev")
import django
django.setup()

from django.conf import settings
from django.db import connection, transaction, models
from accounts.models import User
from coordinate.models import RideRequest, RideParticipant, RideMessage, RideConsent, RideWaitlist
from config.settings import ACTIVE_CREDENTIALS_FILE


def cleanup_loadtest_fixtures(restore_backup: bool = False):
    """
    Remove all test users (2099LOAD*), test rides ([LOADTEST]),
    and optionally restore from pre-test backup snapshot.
    """
    print("\n[CLEANUP] Starting load-testing teardown...")

    backup_files = []
    if ACTIVE_CREDENTIALS_FILE.exists():
        try:
            with open(ACTIVE_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                backup_files = data.get("backup_files") or ([data.get("backup_file")] if data.get("backup_file") else [])
        except Exception:
            pass

    target_dbs = ["default"]
    if "temp_early_access" in settings.DATABASES and Path(settings.DATABASES["temp_early_access"]["NAME"]).exists():
        target_dbs.append("temp_early_access")

    for db_alias in target_dbs:
        print(f"[CLEANUP] Cleaning test data from database alias: '{db_alias}'...")
        with transaction.atomic(using=db_alias):
            test_rides = RideRequest.objects.using(db_alias).filter(
                models.Q(custom_from_location__contains="[LOADTEST]") |
                models.Q(creator__enrollment_number__startswith="2099LOAD")
            )
            rides_count = test_rides.count()
            test_rides.delete()
            print(f"[CLEANUP][{db_alias}] Removed {rides_count} test rides.")

            test_users = User.objects.using(db_alias).filter(enrollment_number__startswith="2099LOAD")
            users_count = test_users.count()
            test_users.delete()
            print(f"[CLEANUP][{db_alias}] Removed {users_count} test accounts.")

    # 3. Clean up credentials file
    if ACTIVE_CREDENTIALS_FILE.exists():
        try:
            ACTIVE_CREDENTIALS_FILE.unlink()
            print(f"[CLEANUP] Removed active credentials file {ACTIVE_CREDENTIALS_FILE}")
        except Exception as e:
            print(f"[WARN] Failed to delete credentials file: {e}")

    # 4. If restore_backup is requested, restore pristine DBs
    if restore_backup and backup_files:
        for b in backup_files:
            b_path = Path(b)
            if b_path.exists():
                orig_name = b_path.name.replace(".pre_loadtest_backup", "")
                orig_path = b_path.with_name(orig_name)
                connection.close()
                shutil.copy2(b_path, orig_path)
                print(f"[RESTORE] Restored {orig_path} from {b_path}")
                b_path.unlink(missing_ok=True)

    print("[CLEANUP] Complete! The database has returned cleanly to normal.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purge load testing test data safely.")
    parser.add_argument("--restore-backup", action="store_true", help="Restore pristine pre-test database snapshot")
    args = parser.parse_args()

    cleanup_loadtest_fixtures(restore_backup=args.restore_backup)
