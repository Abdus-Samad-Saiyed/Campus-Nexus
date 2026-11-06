#!/usr/bin/env bash
# Run Progressive Load Test (35 concurrent users, 180s)
TARGET=${CAMPUS_TARGET_URL:-"http://127.0.0.1:8000"}
python load-testing/scripts/run_scenario.py --scenario load --target "$TARGET"
