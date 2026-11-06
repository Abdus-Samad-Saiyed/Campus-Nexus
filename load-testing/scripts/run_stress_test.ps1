# Run Stress Test (80 concurrent users, 120s)
$Target = if ($env:CAMPUS_TARGET_URL) { $env:CAMPUS_TARGET_URL } else { "http://127.0.0.1:8000" }
python load-testing/scripts/run_scenario.py --scenario stress --target $Target
