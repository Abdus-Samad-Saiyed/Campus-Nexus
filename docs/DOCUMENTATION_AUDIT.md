# Documentation and discrepancy audit

**Reviewed:** 2026-11-12  
**Scope:** checked-in guides and deployment configuration against the current repository tree.  
**Verification:** read-only source review. The Django test suite, browser flows, container build, and production deployment were not run as part of this documentation pass.

This audit separates confirmed repository facts from behavior that still needs runtime verification. It is not a security certification or release sign-off.

## Confirmed documentation faults

| Topic | Old claim or mismatch | Current finding |
| --- | --- | --- |
| Test status | README and docs hub reported 322 and 345 tests passing, and the README test command described a different suite. | These counts were not supported by a current run and have been removed. Run `python manage.py test --settings=campus.settings.test` and record the commit, date, and full result before publishing a count. |
| Release version | Several guides called the project version 2.5.0 without a matching release declaration in the reviewed repository. | Version labels have been removed from the maintained guides unless tied to a tagged release. |
| Local links | Guides contained `file:///s:/...` links that only worked on one machine. | Use repository-relative Markdown links. |
| WebSockets | Older guides used `/ws/rides/<id>/` and `/ws/chat/<enrollment>/`. | Current routing declares `/ws/ride/<id>/`, `/ws/chat/direct/<enrollment>/`, `/ws/notifications/`, `/ws/operations/`, and `/ws/applications/`. |
| HTTP health checks | Some docs described `/healthz/` as the only health endpoint and asserted particular dependencies without qualification. | Both `/health/` and `/healthz/` route to the same health check. Confirm response semantics from `campus/operations.py` before relying on them for readiness decisions. |
| Early-launch isolation | Guides promised all early-launch data was isolated in `temp_early_access.sqlite3` and that purging it could not affect permanent data. | The normal settings declare `default`, `permanent`, and `guest_demo` database aliases, but the inspected runserver command did not visibly redirect `default` to the temporary database. The code search did not find a general early-launch database router. Treat the isolation promise as **unverified**; do not rely on it for live data separation until the command and aliases are tested and corrected. The purge command deletes the temporary file if present and warns about upcoming rides. |
| OTP confidentiality | Security docs said raw OTPs are never logged. | The custom runserver logger attempts to retrieve active OTPs for a request and can append them to console output. This contradicts that claim and is a security-sensitive code issue, not just stale wording. Treat request logs as sensitive and remove/redact that behavior before claiming OTPs are not logged. |
| Development secrets | Security and credentials material advertised shared debug passwords and described bypasses as generally available. | Do not publish shared passwords. Debug account handling is tied to `DEBUG`; generated or local credentials must remain outside version control. The development settings also contain a fixed VAPID private key fallback; it must never be reused as a production key. |
| Sign-up and verification | Older product guides described physical approval as the only account path and presented early and permanent registration as parallel tracks. | The current interface and account flow have changed. Read the account application view and templates as the source of truth; update onboarding policy documentation when the desired permanent-account application workflow is finalized. |
| New-account transition | Older docs treated unverified access as a temporary exception instead of explaining the student flow. | The current application flow can create an active unverified account while a permanent-verification application is pending. Keep “can sign in/use eligible features” distinct from “identity is verified”; inspect the current UI for exact eligibility and application steps. |
| Deployment source of truth | Root and `docs/` deployment guides described a manual systemd/NGINX architecture, while the repository also contains Docker and Compose deployment files. | The guides contradicted each other and the checked-in container setup. The maintained deployment guide now starts from repository configuration and identifies its prerequisites and gaps. It is not a verified production recipe. |
| Production environment template | The production settings require `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`; the Docker environment example does not list them. | Add real, deployment-specific VAPID keys to the deployment secret store before starting the production app. Never copy the development fallback. |
| Directory UX | Search documentation omitted live search and partial matching. | The directory filters case-insensitive partial matches; first-name prefix matches now rank first, followed by other first-name matches and then the remaining matches. Batch result groups can be collapsed independently. |
| Ride browse UX | Older guides did not mention the compact controls. | Browse Rides exposes filters through a collapsed disclosure. Users open it to set filters and apply them. |
| Feedback workflow | The application exposed bug and feature requests without a documented completion workflow. | Staff can mark feedback requests complete in the operations dashboard using a POST action. |

## Other reliability and maintenance issues

- The large `PROJECT_MAP.md` and `project_dump.txt` are snapshots. They can drift; regenerate or review them after structural changes rather than treating every listed file and route as current.
- The older baseline audit said the root `LICENSE` file was missing. A `LICENSE` file is present in the reviewed tree; that finding is stale.
- The audit baseline and load-test reports record earlier observations. Their numbers remain historical and should not be used as current performance or security evidence.
- Some old docs use “verified,” “permanent,” and “temporary” account language interchangeably. Use the actual `verified` field semantics and distinguish identity verification from application/account workflow.
- The PWA service worker caches only selected GET requests. Offline support does not imply that ride creation, search, chat, or account changes work offline.
- Production deployment depends on external services and secret configuration. A successful image build or local run does not prove health, privacy, email, WebAuthn, push, backups, or recovery in production.
- A top-level `CAMPUS_SECRETS_CREDENTIALS.md` exists locally but is not tracked by Git. It must not be used as a shared credential store. Keep credentials in a secret manager and retain only safe variable names and setup guidance in repository documentation.

## Follow-up items

1. Remove OTP values from request logging and audit any existing collected logs for exposure.
2. Verify early-launch storage behavior using a disposable database, then make the implementation and docs agree before using this mode with real data.
3. Add VAPID keys to the production deployment secret configuration and document key rotation.
4. Run the current test suite and focused checks for the updated directory ranking, collapsible batches, ride filters, and feedback completion; publish dated results only after they pass.
5. Review generated maps and performance reports after a new source inventory or benchmark run.

