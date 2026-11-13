# Deployment guide

Reviewed 2026-11-12. This guide describes the checked-in Docker setup; it is not a production certification. The repository configuration does not include a public TLS reverse proxy, verified backup/restore automation, or a complete secrets workflow. Review those gaps before exposing the service publicly.

## Deployment files

- `Dockerfile` builds the Django/Daphne image.
- `docker-compose.yml` defines PostgreSQL, Redis, and the web service. The web command runs migrations and collects static assets before starting the multi-worker ASGI cluster.
- `.env.docker.example` is a template only. It contains placeholders and is not a usable secret file.
- `campus/settings/prod.py` validates production configuration and requires VAPID keys outside the image build phase.

The Compose file binds the web container to `127.0.0.1:8000`. It does not provision TLS or an internet-facing proxy. Keep it behind a separately managed HTTPS reverse proxy that forwards HTTP and WebSocket traffic and sets trusted proxy headers correctly.

## Required production configuration

Create a private `.env` file from `.env.docker.example`; do not commit it. Set and verify at least:

- A unique `DJANGO_SECRET_KEY` of 50 or more characters.
- `DJANGO_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for the real hostnames and HTTPS origins.
- Matching strong `POSTGRES_PASSWORD` and `DATABASE_URL` credentials.
- `REDIS_URL` for the Compose Redis service.
- Valid `EMAIL_HOST_PASSWORD` and sender configuration.
- `WEBAUTHN_RP_ID` and HTTPS `WEBAUTHN_ALLOWED_ORIGINS` matching the public domain.
- Fresh production `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` values. Production settings reject missing keys; never use the development fallback.
- Explicit, narrow CORS origins if a separate client needs cross-origin access.

Protect the file with host-level access controls and provide the same values to the web and database services where applicable. Rotate credentials through the secret store and service configuration, not by editing a committed example.

## Build and start

After reviewing `docker-compose.yml`, `.env.docker.example`, and the production settings, use Docker Compose on a host with Docker Engine and the Compose plugin:

```bash
cp .env.docker.example .env
# Edit .env and replace every placeholder with unique, valid values.
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 web
```

Do not start production with template credentials. `docker compose config` checks Compose interpolation but does not validate Django configuration or prove that external email, WebAuthn, push, proxy, or TLS settings work.

## Health and traffic

The application exposes `/health/` and `/healthz/`; both use the same handler. Check the implementation in `campus/operations.py` before using a response as a readiness guarantee. The Compose file does not configure a reverse proxy, TLS certificates, or a public firewall policy.

Route WebSocket requests through the proxy with HTTP/1.1 upgrade support and a suitable idle timeout. The current routes include `/ws/ride/<ride_id>/`, `/ws/chat/direct/<enrollment>/`, `/ws/notifications/`, `/ws/operations/`, and `/ws/applications/`.

## Updating the deployment

1. Back up the database and uploaded media using a procedure tested for this deployment.
2. Review the code and migration changes for the target revision.
3. Build and deploy the new image in a controlled window; the Compose web command applies migrations at startup.
4. Inspect application logs, health behavior, static files, email delivery, WebAuthn origins, push delivery, and WebSocket routing.
5. Keep a tested rollback plan. Database migrations may not be reversible; do not assume that rolling back the image reverses a schema change.

## Backups and recovery

Compose uses named volumes for PostgreSQL data and uploaded media. The repository does not configure an off-host backup schedule or verify restore procedures. Back up PostgreSQL and media separately, encrypt and restrict access to backups, define retention, and regularly test restoration into an isolated environment before relying on the service.

## Known deployment gaps

- The example environment omits VAPID keys required by production settings; add them to the private environment before startup.
- TLS termination, reverse-proxy configuration, firewall policy, and certificate renewal are outside the supplied Compose stack.
- There is no checked-in, verified backup/restore runbook for the Compose volumes.
- The custom multi-worker script and production readiness behavior have not been verified by this documentation review.
- Early-launch database isolation is unverified; see the [documentation audit](DOCUMENTATION_AUDIT.md). Do not assume the mode separates production data.
