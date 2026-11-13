# Load-test fixtures and credentials

These scripts create synthetic accounts and ride records in the database configured by Django settings. The `2099LOAD` naming pattern and `[LOADTEST]` ride marker support cleanup; they do not isolate data from other records or databases.

**Use only with a disposable local SQLite database.** Stop application writers first. Confirm the effective `DATABASE_URL` before setup. Never point the scripts at production, a shared instance, or data that cannot be replaced.

`active_test_users.json` contains generated test login credentials. Keep it private and remove it after the run. The optional snapshot restore copies a complete SQLite file over its target and may discard any changes made after the snapshot. It is not supported as a safe cleanup method for PostgreSQL.

See [load-testing guidance](../README.md) and inspect the setup/cleanup scripts before each run.
