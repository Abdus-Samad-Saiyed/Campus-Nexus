# Authentication, identity, and security

Reviewed 2026-11-12. This guide describes current code paths and calls out unresolved findings. It is not a security certification. Do not place passwords, OTPs, private keys, or real student data in this document or in source control.

## Account identity and verification

The custom identity model is `accounts.User`. Enrollment numbers are validated by `ENROLLMENT_VALIDATOR` in `accounts/models.py`; the accepted formats include student-style identifiers, debug identifiers, the guest-demo user, and deleted-account tombstones. Do not treat a syntactically valid enrollment number as proof of identity.

The `verified` field controls access to verified-student features. New/self-declared accounts are not physically verified merely because they can use an early-access flow. An account can apply for permanent verification through the account workflow; the exact onboarding choices are defined by `accounts/views.py` and the account templates.

## Sign-in and session protections

- Web sign-in uses the custom enrollment authentication backend and login view in `accounts/`.
- OTP challenges are used by account and security workflows. Current constants include a five-minute expiry, one-minute resend cooldown, and three attempts; verify these in `accounts/two_factor.py` when changing the policy.
- WebAuthn/passkey challenge and verification handlers are registered in `campus/urls.py`. Production requires a relying-party ID and HTTPS origins that match the public site.
- Session controls include single-session and idle-timeout middleware, plus targeted revocation after sensitive account actions.
- Security settings can use OTP or password/passkey step-up verification depending on the action.

## Privacy controls

Directory visibility, profile photo, online status, contact display, friend visibility, and read receipts have separate controls or policies. The student directory filters hidden accounts and performs case-insensitive partial matching across first name, last name, enrollment number, and branch. First-name prefix matches rank before other first-name matches; batch groups can be collapsed independently.

Guest demo access is blocked from student directory, profiles, private messages, WebSockets, and protected media. Its ride browser redacts student identities and departure times. See [Guest demo and privacy](GUEST_DEMO_AND_PRIVACY.md) for the current boundary description.

## OTP handling: unresolved security finding

The current implementation keeps active OTP values in cache/in-memory structures to support operational console output. The custom runserver logger can retrieve and print an active code alongside request logs. Therefore, the older claim that raw codes are never logged is false. Treat console and collected application logs as sensitive, restrict access, and do not claim OTP confidentiality until the logging path is changed and verified. See the [documentation audit](DOCUMENTATION_AUDIT.md).

## Secret management

- Use a secret manager or deployment-specific environment injection for production secrets.
- Keep `.env`, local settings, database dumps, and generated credentials out of Git and shared documentation.
- Production settings require a strong `DJANGO_SECRET_KEY`, explicit hosts, database and email configuration, WebAuthn settings, and VAPID keys.
- The development settings contain insecure defaults and a fixed development VAPID fallback. Never reuse them in production.
- Rotate a credential if it has been committed, pasted into a shared location, or exposed in logs. Removing a value from the current file does not remove it from Git history.

## Early-launch mode: isolation is unverified

The custom runserver command advertises a temporary database, but the inspected settings and command did not establish a clear general redirect of the default database to that file. Account code can also mirror records between the `default` and `permanent` aliases when they differ. Do not use early-launch mode as a production isolation boundary until a disposable-environment test proves which database receives accounts, rides, and sessions and the implementation is corrected if necessary.

## Review checklist for security changes

1. Trace the route, view, policy, model, and database alias that enforce the behavior.
2. Check logs and error responses for personal data, tokens, OTPs, and credentials.
3. Verify every privilege path, including staff, guest demo, unverified accounts, and API clients.
4. Use isolated test data and record the exact command, revision, and result.
5. Update this guide and the [documentation audit](DOCUMENTATION_AUDIT.md) if a claim or control changes.
