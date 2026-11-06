#!/usr/bin/env bash
# Run Baseline Test (10 concurrent users, 120s)
TARGET=${CAMPUS_TARGET_URL:-"http://127.0.0.1:8000"}
python load-testing/scripts/run_scenario.py --scenario baseline --target "$TARGET"
