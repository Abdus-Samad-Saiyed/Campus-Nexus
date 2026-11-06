#!/usr/bin/env bash
# Run Smoke Test (5 concurrent users, 60s)
TARGET=${CAMPUS_TARGET_URL:-"http://127.0.0.1:8000"}
python load-testing/scripts/run_scenario.py --scenario smoke --target "$TARGET"
