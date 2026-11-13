# Operations and administration

Reviewed 2026-11-12 against `campus/operations.py`, `campus/urls.py`, `accounts/views.py`, and the operations templates.

## Staff access

The operations dashboard is at `/operations/`. Views generally require staff or superuser access; destructive or high-privilege actions may be restricted to superusers. The route decorators and view checks are the authority. Keep staff roles limited and review access when adding a dashboard action.

## Feedback and bug requests

Users submit feedback and bug reports through the feedback form. Staff can mark a feedback request complete using the operations dashboard. The action is a POST route at `/operations/feedback/<feedback_id>/complete/`; retain CSRF protection and verify authorization when changing it. “Complete” records workflow status and does not itself verify that the underlying bug is fixed or deployed.

## Account applications and permanent verification

The application queue and approval/rejection actions are managed by staff with the required permissions. Review an applicant's institutional details using the approved process before changing verification state. Avoid exporting or sharing application data unless required for the role. Account-flow details are implemented in `accounts/views.py` and the templates.

The account system also has unverified/self-declared states. The early-launch storage isolation claim is unresolved; do not use it as a data protection boundary. See the [documentation audit](DOCUMENTATION_AUDIT.md).

## Administrative actions and exports

The root URL configuration registers staff tools for announcements, user/phone management, verification resolution, and CSV exports. Some exports are limited to superusers. Inspect the exact route decorators and view logic before granting access or automating exports. Treat downloaded CSV files as sensitive personal data, store them securely, and delete them according to the institution's retention rules.

Account deletion uses soft-deletion behavior in the operations code. Confirm related records, access revocation, and retention effects from the current implementation before promising that data is either erased or retained permanently.

## Health endpoints

`/health/` and `/healthz/` are aliases for the same handler. Check `campus/operations.py:health_check` for dependency checks, response codes, and any information returned before wiring probes to an orchestrator or public monitor.

## Operational safety

- Require HTTPS and restrict access to admin routes at the application and network layers.
- Use POST for state-changing actions and keep CSRF and role checks in place.
- Do not paste credentials, OTPs, private keys, student records, or exported data into tickets or logs.
- Keep an auditable reason for privileged manual changes and establish institution-approved retention and incident procedures.
- Back up before schema, deletion, or account migration operations; test restores in an isolated environment.
