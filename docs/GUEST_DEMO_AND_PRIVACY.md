# Guest demo and privacy boundaries

Reviewed 2026-11-12 against `accounts/demo.py`, account and ride views, consumers, and protected-media handling. This is a description of code paths, not a guarantee against every deployment or data leak.

## Entry and session behavior

The guest demo is entered at `/demo/` (with `/guest-demo/` as an alias) and exited at `/demo/exit/`. A guest session is tagged in the session and uses the synthetic `GUEST_DEMO` account. Demo rides are session-tagged and written through the `guest_demo` database alias. The code prunes demo rides older than two hours when demo readiness runs and removes session rides on exit.

The demo account may also be ensured in the default database for authentication. Therefore, describe the demo as isolating demo ride records and enforcing request-level privacy policies; do not describe the whole demo identity as existing only in a third database.

## What demo visitors can and cannot do

- Browse real rides with personal identities and departure times redacted in demo views.
- Create demo rides and inspect their own demo ride experience.
- Cannot use the student directory, student profiles, direct messages, protected media, or student WebSocket channels through the guest session.
- Cannot join real student rides or use their live coordination rooms through the guest flow.

The enforcement is distributed across `accounts/demo.py`, account and ride views, policies, consumers, and `serve_protected_media` in `campus/urls.py`. When modifying a page or endpoint, review every data path; hiding a link in the template is not an access control.

## Data boundaries and limitations

The default, permanent, and guest-demo aliases are declared in `campus/settings/base.py`. Ride and participant code explicitly uses the guest-demo alias for demo ride storage. A general `DATABASE_ROUTERS` policy is not configured in the inspected settings. Do not infer that every guest account, session, message, cache entry, or log record is isolated in the guest database.

Verify database settings per environment. In the test settings, the aliases use in-memory SQLite by default. In deployment, the environment controls the database URLs. Guest-session cleanup and age pruning are application behavior; they do not replace database backups, retention policies, or privacy review.

## Route references

- Entry: `/demo/`, `/guest-demo/`
- Exit: `/demo/exit/`
- Directory and account routes: `accounts/urls.py`
- Ride routes: `coordinate/urls.py`
- WebSockets: `coordinate/routing.py`
- Protected media: `serve_protected_media` in `campus/urls.py`

For unresolved early-launch storage claims, see the [documentation audit](DOCUMENTATION_AUDIT.md). Do not confuse early-launch mode with guest demo mode; their isolation behavior is different and early-launch isolation is currently unverified.
