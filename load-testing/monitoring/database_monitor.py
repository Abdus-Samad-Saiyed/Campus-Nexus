"""
Campus Nexus — Database Performance & Lock Contention Monitor
Monitors SQLite file size, WAL activity, and query execution times under load.
Detects database busy conditions and lock contention.
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus.settings.dev")
import django
django.setup()

from django.conf import settings
from django.db import connection

LOADTEST_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = LOADTEST_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


class DatabaseMonitor:
    def __init__(self, output_basename: str = "database_metrics", interval_sec: float = 1.0):
        self.output_basename = output_basename
        self.interval_sec = interval_sec
        self.running = False
        self.thread = None
        self.records = []
        self.start_time = None
        self.csv_path = REPORTS_DIR / f"{output_basename}.csv"
        self.json_path = REPORTS_DIR / f"{output_basename}.json"

        db_conf = settings.DATABASES["default"]
        self.db_name = db_conf.get("NAME", "db.sqlite3")
        self.db_path = Path(self.db_name)

    def _sample(self):
        while self.running:
            time.sleep(self.interval_sec)
            t_now = time.time()

            # 1. Measure DB and WAL sizes
            db_size_kb = 0.0
            wal_size_kb = 0.0
            if self.db_path.exists():
                try:
                    db_size_kb = round(self.db_path.stat().st_size / 1024.0, 2)
                    wal_path = self.db_path.with_name(f"{self.db_path.name}-wal")
                    if wal_path.exists():
                        wal_size_kb = round(wal_path.stat().st_size / 1024.0, 2)
                except Exception:
                    pass

            # 2. Probe read query latency (SELECT 1)
            probe_ms = -1.0
            lock_error = False
            try:
                t0 = time.perf_counter()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                probe_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            except Exception as e:
                lock_error = True
                probe_ms = -1.0

            # 3. Probe ORM count query
            count_ms = -1.0
            try:
                t0 = time.perf_counter()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM coordinate_riderequest")
                    cursor.fetchone()
                count_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            except Exception:
                pass

            record = {
                "timestamp": datetime.now().isoformat(),
                "elapsed_sec": round(t_now - self.start_time, 2),
                "db_size_kb": db_size_kb,
                "wal_size_kb": wal_size_kb,
                "select1_ms": probe_ms,
                "count_query_ms": count_ms,
                "lock_contention_error": 1 if lock_error else 0,
            }
            self.records.append(record)

    def start(self):
        self.running = True
        self.start_time = time.time()
        self.records = []
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        print(f"[DB-MONITOR] Database monitoring started on {self.db_name}")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, indent=2)

        if self.records:
            keys = self.records[0].keys()
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(self.records)

        print(f"[DB-MONITOR] Saved {len(self.records)} samples to {self.csv_path} and {self.json_path}")
        return self.records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone Database Monitor")
    parser.add_argument("--duration", type=int, default=60, help="Monitoring duration in seconds")
    parser.add_argument("--output", type=str, default="database_metrics_standalone", help="Output basename")
    args = parser.parse_args()

    m = DatabaseMonitor(output_basename=args.output)
    m.start()
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        m.stop()
