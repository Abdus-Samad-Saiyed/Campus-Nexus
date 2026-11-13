# Project map

Reviewed 2026-11-12. This is a navigation guide, not a generated, exhaustive inventory. Use `rg --files` for the current file tree and source code for exact behavior.

## Application structure

| Area | Important paths |
| --- | --- |
| Project settings and entry points | `campus/settings/`, `campus/urls.py`, `campus/asgi.py`, `campus/wsgi.py` |
| Account, sign-in, profile, directory | `accounts/models.py`, `accounts/forms.py`, `accounts/views.py`, `accounts/urls.py`, `accounts/middleware.py` |
| Ride coordination | `coordinate/models.py`, `coordinate/services.py`, `coordinate/views.py`, `coordinate/urls.py`, `coordinate/consumers.py` |
| REST API | `campus/api/urls.py`, `campus/api/views.py`, `campus/api/auth.py` |
| WebSocket routing | `campus/asgi.py`, `coordinate/routing.py`, `accounts/consumers.py`, `campus/consumers.py` |
| Staff operations | `campus/operations.py`, `templates/nexus/operations_dashboard.html` |
| Web interface | `templates/`, `static/css/`, `static/js/` |
| PWA | `static/manifest.json`, `static/sw.js`, root views in `campus/urls.py` |
| Deployment | `Dockerfile`, `docker-compose.yml`, `.env.example`, `.env.docker.example` |
| Load testing | `load-testing/` |

## Settings modules

- `campus.settings.base`: common application setup and aliases.
- `campus.settings.dev`: local-only overrides.
- `campus.settings.prod`: production environment validation.
- `campus.settings.test`: isolated test configuration.

## Useful URL sources

- Root routes: `campus/urls.py`
- Account routes: `accounts/urls.py`
- Ride routes: `coordinate/urls.py`
- API routes: `campus/api/urls.py`
- WebSocket routes: `coordinate/routing.py` and `campus/asgi.py`

See the [API reference](docs/API_REFERENCE.md) for endpoint groups and the [architecture guide](docs/ARCHITECTURE.md) for runtime relationships. Early-launch database isolation is a known discrepancy; see [the documentation audit](docs/DOCUMENTATION_AUDIT.md).
