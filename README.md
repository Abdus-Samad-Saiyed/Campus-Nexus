# Campus Nexus

Campus Nexus is a student community platform. **COORDINATE** is its ride sharing feature for planning campus and intercity trips. This repository contains the Django application, server-rendered web interface, Progressive Web App assets, REST API, and WebSocket services.

> Documentation reviewed on 2026-11-12. Version and test totals are intentionally omitted unless tied to a reproducible release or recorded run.

## What is here

- Student account applications, sign-in, profiles, privacy settings, and directory search.
- Ride discovery, ride requests, group coordination, waitlists, feedback, and operations tools.
- Guest demo mode, WebAuthn passkeys, web push, and browser install support.
- Django REST endpoints under `/api/v1/` and ASGI WebSockets.

Feature behavior and availability depend on settings and deployment configuration. See the [documentation hub](docs/README.md) for architecture, authentication, API, operations, mobile, and deployment guides. See [known discrepancies](docs/DOCUMENTATION_AUDIT.md) before relying on older audit or benchmark claims.

## Local development

Requirements: Python 3.12 or newer. SQLite is configured by the local environment template. Redis is needed for shared production WebSockets; the development settings can use an in-memory channel layer when Redis is unavailable.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate --settings=campus.settings.dev
python manage.py runserver 127.0.0.1:8000 --settings=campus.settings.dev
```

### macOS or Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
python manage.py migrate --settings=campus.settings.dev
python manage.py runserver 127.0.0.1:8000 --settings=campus.settings.dev
```

Open <http://127.0.0.1:8000/>. The local environment template uses an intentionally insecure development secret and console email delivery. Never use it for a public or production deployment. Do not commit `.env` files or real credentials.

## Tests and checks

The test settings use isolated in-memory databases and test media. To run the current Django suite:

```bash
python manage.py test --settings=campus.settings.test
```

This README does not claim a passing test count. Record the command, date, revision, and result when reporting a run. See [test and audit evidence](docs/DOCUMENTATION_AUDIT.md) for the status of older reports.

## Deployment

The repository includes a Dockerfile, Compose configuration, and a production environment template. Start with [deployment guidance](docs/DEPLOYMENT.md) and inspect the Compose file and environment template before deploying. Production requires a unique secret key, valid host and origin settings, a database, Redis (unless explicitly configured otherwise), SMTP credentials, WebAuthn origin settings, and VAPID keys.

Do not run a production deploy from this quickstart. The deployment guide describes required configuration and remaining verification work.

## Documentation

- [Documentation hub](docs/README.md)
- [Documentation audit and known discrepancies](docs/DOCUMENTATION_AUDIT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API reference](docs/API_REFERENCE.md)
- [Authentication and security](docs/AUTHENTICATION_AND_SECURITY.md)
- [Operations](docs/OPERATIONS_AND_ADMINISTRATION.md)
- [Deployment](docs/DEPLOYMENT.md)
