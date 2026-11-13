# Documentation hub

Reviewed 2026-11-12. These guides describe the repository as inspected on that date. Source code and checked-in configuration remain the authority when behavior changes. Older audit and load-test documents are historical records, not current release evidence.

## Start here

| Need | Document |
| --- | --- |
| Set up a local checkout | [Root README](../README.md) |
| Understand known documentation and code discrepancies | [Documentation audit](DOCUMENTATION_AUDIT.md) |
| Find the main apps, settings, entry points, and routes | [Architecture](ARCHITECTURE.md) |
| Integrate with HTTP and WebSocket endpoints | [API reference](API_REFERENCE.md) |
| Understand sign-in, account verification, passkeys, and privacy controls | [Authentication and security](AUTHENTICATION_AND_SECURITY.md) |
| Operate the staff dashboard and account workflows | [Operations and administration](OPERATIONS_AND_ADMINISTRATION.md) |
| Configure and deploy the application | [Deployment](DEPLOYMENT.md) |

## Product and application guides

- [Guest demo and privacy](GUEST_DEMO_AND_PRIVACY.md)
- [Multi-ride scheduling](MULTI_RIDE_SCHEDULING.md)
- [Mobile and PWA architecture](MOBILE_ARCHITECTURE.md)
- [PWA assets and offline behavior](MOBILE_SCAFFOLDING.md)
- [Themes and mechanical visual effects](MECHANICAL_ZEN_AND_THEMES.md)
- [Project map](../PROJECT_MAP.md)
- [Engineering manifesto](../MANIFESTO.md)

## Historical evidence

- [Audit baseline](AUDIT_BASELINE.md): snapshot from the earlier audit; not a current assessment.
- [Bug register](BUG_REGISTER.md): historical findings that must be rechecked against current code.
- [Load-testing guide and archived reports](../load-testing/README.md): benchmark instructions and dated results. Read the run metadata and limitations before comparing results.

## Documentation conventions

- Use relative Markdown links so documentation works in repository browsers and local checkouts.
- Label historical findings with their date and revision when known. Avoid unqualified claims such as “all tests pass,” “secure,” or “zero leakage.”
- Treat `.env` files, credentials, database snapshots, and user data as sensitive. Document variable names and setup steps, never real secret values or shared passwords.
- Keep setup instructions copyable and state the operating system, required services, and settings module they assume.
- Update this hub and the [documentation audit](DOCUMENTATION_AUDIT.md) when adding, moving, or deprecating a guide.
