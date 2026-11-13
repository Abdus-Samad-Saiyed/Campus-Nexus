# Campus Nexus: product principles

Campus Nexus is a student community platform, with COORDINATE supporting ride planning and shared travel. These principles guide design decisions; they are not a statement that every aspiration below is fully implemented or independently verified.

## Trust with clear status

Enrollment details and digital credentials help students use the service, but a submitted enrollment number is not proof of identity. The product distinguishes unverified/self-declared accounts from verified student accounts. Explain the status plainly and make the path to permanent verification understandable.

## Privacy shaped by context

Public pages should not expose student records. Student discovery and profiles use account status and privacy preferences. Ride coordination may require participants to see information needed to meet safely, while people browsing a ride should receive only the information the product allows them to see. Keep those audiences and purposes explicit in both policy and interface.

## Useful, accessible campus software

Optimize for the device students actually use, including Android phones and constrained networks. Keep common tasks discoverable, reduce unnecessary onboarding and repeated actions, use touch-friendly controls, and provide clear feedback when an action succeeds or fails.

Current directory behavior ranks first-name prefix matches first, supports partial matching across the indexed fields, and groups results by batch with independently collapsible groups. Ride browsing keeps detailed filters in a collapsed disclosure. These are current interface behaviors, not permanent product guarantees.

## Responsible operations

Treat account, ride, contact, application, and location-related data as sensitive. Limit staff access, protect exports, preserve an audit trail where required, define retention with the institution, and make deletion and backup behavior clear. Never claim that information is “never logged” or “permanently retained” without checking the actual implementation and operational systems.

## Accurate engineering communication

Prefer verifiable claims over superlatives. Label old audits and benchmarks as historical evidence. Do not publish shared credentials, OTPs, private keys, personal records, or production URLs and passwords together. Update documentation when routes, permissions, data handling, or deployment requirements change.

## Maintainer notes

- Follow the [documentation hub](docs/README.md) and [documentation audit](docs/DOCUMENTATION_AUDIT.md).
- Security and data-isolation claims require source review and runtime verification.
- Product intent does not replace accessibility, privacy, and security review.

*Sic Itur Ad Astra.*
