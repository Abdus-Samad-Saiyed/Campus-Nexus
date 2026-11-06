# Campus Nexus — Load Testing Data Isolation & Safety

## Isolation Philosophy
Load testing MUST NEVER contaminate or corrupt production or real development datasets.
All synthetic entities created for load testing use strict namespace demarcations:

1. **User Accounts:**
   - Format: `2099LOAD001` through `2099LOAD050`
   - Administrative Account: `2099LOAD999`
   - Role: Student (or Staff/Superuser for admin test account)
   - Verified: True (bypasses unverified account gates)
   - 2FA: Disabled (`two_factor_enabled=False`, `is_debug_account=True`) to allow automated HTTP authentication without SMS/Email OTP dependency.

2. **Rides & Telemetry:**
   - Notes field prefix: `[LOADTEST]`
   - Created only in the future (> 1 day, < 365 days).
   - Tagged for clean automated cascade deletion.

3. **Backup & Teardown Protocol:**
   - `python load-testing/scripts/setup_test_data.py`:
     1. Creates a bitwise backup snapshot of the active SQLite database (`temp_early_access.sqlite3.pre_loadtest_backup` or `db.sqlite3.pre_loadtest_backup`).
     2. Populates only demarcated `2099LOAD*` accounts and `[LOADTEST]` rides.
     3. Saves active credentials to `load-testing/test-data/active_test_users.json`.
   - `python load-testing/scripts/cleanup_test_data.py`:
     1. Queries and deletes all `2099LOAD*` users, participants, ride messages, consent records, and sessions.
     2. Queries and deletes all `[LOADTEST]` rides.
     3. Deletes `active_test_users.json`.
     4. Verifies database consistency and confirms 0 residual load-test records.
