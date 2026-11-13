# Load testing

Reviewed 2026-11-12 against the runner, fixture, and cleanup scripts. This harness creates privileged test accounts and can overwrite SQLite database files during restoration. It is not safe to aim at production, a shared development database, or any database containing data that is not disposable.

## Before running

1. Use a separate local checkout and an explicitly disposable SQLite database. Do not load a production `.env`; the scripts initialize Django from the selected settings module and can therefore use the configured `DATABASE_URL`.
2. Stop the application and all writers using the target database before setup. The optional full-file restore can overwrite concurrent changes.
3. Verify `DATABASE_URL` resolves to the disposable database. The namespace prefix does not isolate the database by itself.
4. Confirm enough disk space for a database copy, report files, and generated charts.
5. Use synthetic data only. Keep `active_test_users.json` private; it contains credentials and is generated in `load-testing/test-data/`.

The harness does not provide a production safety guarantee. Its fixture setup and cleanup target `default` and may also target a configured `temp_early_access` alias. Cleanup removes users whose enrollment starts with `2099LOAD`, rides marked with `[LOADTEST]`, and can restore a SQLite file snapshot. A misconfigured target or pre-existing matching fixture can cause data loss.

## Layout

- `locustfile.py`: workload entry point.
- `users/`: anonymous, authenticated, transactional, and admin user profiles.
- `config/`: targets, weights, credentials, and thresholds.
- `monitoring/`: system and database monitors.
- `scripts/setup_test_data.py`: fixture creation and SQLite snapshots.
- `scripts/cleanup_test_data.py`: fixture removal and optional snapshot restoration.
- `scripts/run_scenario.py`: benchmark orchestration.
- `reports/`: dated benchmark output.

## Dependencies

Install the project environment first, then install the separate benchmark requirements:

```bash
python -m pip install -r requirements.txt
python -m pip install -r load-testing/requirements.txt
```

## Run against a disposable local instance

The orchestration script supports `smoke`, `baseline`, `load`, `stress`, and `burst` scenarios. It creates fixtures, starts monitors and Locust, generates reports, then requests cleanup and snapshot restoration.

```bash
python load-testing/scripts/run_scenario.py --scenario smoke --target http://127.0.0.1:8000
```

Optional parameters include `--users` and `--duration`. Check the current script's CLI before using them. Do not run against a remote production host or public service without an explicitly approved test plan and isolated dataset.

## Results and interpretation

Reports in `reports/` are snapshots from earlier runs. They are not current capacity guarantees and some scenarios contain failures. Compare only runs with documented commit, host hardware, database engine, worker count, target, user model, duration, and error rate. The harness does not model every Android client, network condition, or production dependency.

## Cleanup caution

If a run stops unexpectedly, inspect the active database and backup files before running cleanup. `--restore-backup` replaces the SQLite database file with the saved snapshot; it can erase changes made after that snapshot. Do not run it while the app is connected or against PostgreSQL. Review `scripts/cleanup_test_data.py` and confirm every target path before using it.
