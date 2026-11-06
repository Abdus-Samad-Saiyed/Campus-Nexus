"""
Campus Nexus — System & Infrastructure Resource Monitor
Samples CPU, RAM, Disk, Network, GPU, and Daphne process metrics at 1-second resolution.
Stores timestamped measurements to correlate with Locust benchmark runs.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import psutil

LOADTEST_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = LOADTEST_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


class SystemMonitor:
    def __init__(self, output_basename: str = "system_metrics", target_process_name: str = "python"):
        self.output_basename = output_basename
        self.target_process_name = target_process_name
        self.running = False
        self.thread = None
        self.records = []
        self.start_time = None
        self.csv_path = REPORTS_DIR / f"{output_basename}.csv"
        self.json_path = REPORTS_DIR / f"{output_basename}.json"

    def _get_gpu_metrics(self):
        """Query nvidia-smi for NVIDIA RTX 5050 utilisation and VRAM."""
        try:
            cmd = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=1).decode("utf-8").strip()
            parts = [p.strip() for p in out.split(",")]
            if len(parts) >= 3:
                return {
                    "gpu_util_pct": float(parts[0]),
                    "gpu_vram_used_mb": float(parts[1]),
                    "gpu_vram_total_mb": float(parts[2]),
                }
        except Exception:
            pass
        return {"gpu_util_pct": 0.0, "gpu_vram_used_mb": 0.0, "gpu_vram_total_mb": 0.0}

    def _find_target_processes(self):
        """Find Daphne / Django Python server processes."""
        procs = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info["name"] or "").lower()
                cmdline = " ".join(p.info["cmdline"] or []).lower()
                if "python" in name and ("runserver" in cmdline or "daphne" in cmdline or "manage.py" in cmdline):
                    procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return procs

    def _sample(self):
        # Baseline network & disk counters
        net_prev = psutil.net_io_counters()
        disk_prev = psutil.disk_io_counters()
        t_prev = time.time()

        while self.running:
            time.sleep(1.0)
            t_now = time.time()
            dt = max(t_now - t_prev, 0.001)

            # System CPU & Memory
            cpu_total = psutil.cpu_percent(interval=None)
            cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
            mem = psutil.virtual_memory()

            # Network I/O
            net_now = psutil.net_io_counters()
            net_sent_kb_s = ((net_now.bytes_sent - net_prev.bytes_sent) / 1024.0) / dt
            net_recv_kb_s = ((net_now.bytes_recv - net_prev.bytes_recv) / 1024.0) / dt
            net_prev = net_now

            # Disk I/O
            disk_now = psutil.disk_io_counters()
            disk_read_kb_s = ((disk_now.read_bytes - disk_prev.read_bytes) / 1024.0) / dt if disk_now and disk_prev else 0.0
            disk_write_kb_s = ((disk_now.write_bytes - disk_prev.write_bytes) / 1024.0) / dt if disk_now and disk_prev else 0.0
            disk_prev = disk_now
            t_prev = t_now

            # Application Process Stats
            app_procs = self._find_target_processes()
            app_cpu_total = 0.0
            app_rss_mb = 0.0
            app_threads = 0
            for ap in app_procs:
                try:
                    app_cpu_total += ap.cpu_percent(interval=None)
                    app_rss_mb += ap.memory_info().rss / (1024.0 * 1024.0)
                    app_threads += ap.num_threads()
                except Exception:
                    pass

            gpu_stats = self._get_gpu_metrics()

            record = {
                "timestamp": datetime.now().isoformat(),
                "elapsed_sec": round(t_now - self.start_time, 2),
                "cpu_total_pct": cpu_total,
                "ram_used_mb": round(mem.used / (1024.0 * 1024.0), 1),
                "ram_available_mb": round(mem.available / (1024.0 * 1024.0), 1),
                "ram_percent": mem.percent,
                "app_cpu_pct": round(app_cpu_total, 2),
                "app_rss_mb": round(app_rss_mb, 2),
                "app_threads": app_threads,
                "disk_read_kb_s": round(disk_read_kb_s, 2),
                "disk_write_kb_s": round(disk_write_kb_s, 2),
                "net_sent_kb_s": round(net_sent_kb_s, 2),
                "net_recv_kb_s": round(net_recv_kb_s, 2),
                "gpu_util_pct": gpu_stats["gpu_util_pct"],
                "gpu_vram_used_mb": gpu_stats["gpu_vram_used_mb"],
            }
            self.records.append(record)

    def start(self):
        """Start asynchronous monitoring."""
        self.running = True
        self.start_time = time.time()
        self.records = []
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        print(f"[MONITOR] System resource monitoring started -> {self.output_basename}")

    def stop(self):
        """Stop monitoring and persist data to CSV and JSON."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

        # Write JSON
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, indent=2)

        # Write CSV
        if self.records:
            keys = self.records[0].keys()
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(self.records)

        print(f"[MONITOR] Captured {len(self.records)} samples. Saved to {self.csv_path} and {self.json_path}")
        return self.records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone System Monitor")
    parser.add_argument("--duration", type=int, default=60, help="Monitoring duration in seconds")
    parser.add_argument("--output", type=str, default="system_metrics_standalone", help="Output basename")
    args = parser.parse_args()

    m = SystemMonitor(output_basename=args.output)
    m.start()
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        m.stop()
