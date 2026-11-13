# Multiple ride scheduling

Reviewed 2026-11-12 against `coordinate/services.py`, the web views, and API views.

## Rule

A student may participate in more than one active ride when departure times do not fall within the configured overlap window. `user_has_overlapping_ride()` defaults to a two-hour window on either side of the proposed departure and checks confirmed participation in rides with `OPEN` or `FULL` status. The query uses an inclusive time range, so exactly two hours apart is still considered a conflict by the current implementation.

This check applies to creating rides, joining, approval, and waitlist promotion in the service and view flows. API behavior is implemented in `campus/api/views.py`; do not assume the web and API response messages are identical.

## Where to maintain the rule

- Shared check: `coordinate/services.py:user_has_overlapping_ride`
- Web create/join/reschedule and eligibility flows: `coordinate/views.py`
- API create/join flows: `campus/api/views.py`
- Ride cards and user feedback: templates under `templates/coordinate/`

Change the shared rule and all calling paths together. Consider transactions and concurrent joins when changing capacity or participation logic.

## Ride discovery controls

Browse Rides places detailed date, route, visibility, queue, and ordering filters in a collapsed “Filters and sort” disclosure. Users open the section and submit the criteria. The selected criteria remain visible in the form after the results refresh.

## Verification

The scheduling tests are in `coordinate/test_multi_ride.py`. This documentation review did not run them. When reporting verification, record the revision, exact command, and result; an old test list is not current evidence.
