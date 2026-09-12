# Fresh installation and health verification

## 1. Configure

Copy `.env.example` into your secret manager or container environment. The
application deliberately does not load a local `.env` file. Use a PostgreSQL
URL for multiple API/worker processes; SQLite is intended for a single node.

Create the SQLite directory when using the example path:

```console
mkdir -p var
```

## 2. Install and migrate

Run from the repository root so the API can install the sibling public engine:

```console
uv sync --project services/api --frozen --all-groups
uv run --project services/api six-community migrate
uv run --project services/api six-community check
```

For PostgreSQL, create an empty database and grant the application role schema
ownership before running the same migration command. The baseline never reads
or transforms a hosted database.

## 3. Start API and worker

```console
uv run --project services/api six-community serve --host 0.0.0.0 --port 8000
uv run --project services/api six-community-worker
```

Terminate TLS at a trusted reverse proxy. Configure `SIX_COMMUNITY_ALLOWED_HOSTS`
and `SIX_COMMUNITY_ALLOWED_ORIGINS` as JSON arrays. Do not expose SQLite through
multiple replicas.

## 4. Verify

```console
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
curl --fail http://127.0.0.1:8000/openapi.json
```

Then register a local account with `POST /auth/register`, retain the returned
bearer token in the client only, and disable registration if the deployment is
not intended to accept additional organizations.

## Container image

The Dockerfile must be built from the repository root because the service uses
the sibling open engine package:

```console
docker build -f services/api/Dockerfile -t sixsentences-community-api .
```

Run migrations as a one-shot task before starting the image. Mount a writable
directory at `/data` for SQLite, or provide a PostgreSQL URL. The image runs as
an unprivileged user and does not contain configuration or user data.

## Backup and retention

Back up the database and any deployment-owned object storage with independent,
encrypted tooling. Test restoration into an isolated environment. Operators
must separately implement expiry of survey/interview records according to
their participant notice; the API records the declared retention window but
does not claim that a database backup has already expired it.
