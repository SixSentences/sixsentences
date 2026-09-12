# SixSentences Community API

This directory contains the independently deployable FastAPI service for a
self-hosted research workspace. It stores every workspace row behind an
organization boundary and supports SQLite for a single-node install or
PostgreSQL for a multi-process deployment.

The current implementation includes local authentication and API keys,
projects, research runs, a library, datasets, figures, surveys, text and spoken
public interviews, writer documents, knowledge pages, brainstorming jobs, and a
durable SMTP outbox. AI and speech leave the deployment only when its operator
explicitly configures compatible providers. Public spoken interviews retain
transcripts, not audio.

This API is alpha software. See [COMMUNITY-BOUNDARY.md](COMMUNITY-BOUNDARY.md)
for exact product-contract coverage and [FRESH-INSTALL.md](FRESH-INSTALL.md)
for a clean installation.

## Local quick start

From the repository root, with Python 3.12+ and `uv` installed:

```console
uv sync --project services/api --frozen --all-groups
SIX_COMMUNITY_DATABASE_URL=sqlite:///./var/community.sqlite3 \
  uv run --project services/api six-community migrate
SIX_COMMUNITY_DATABASE_URL=sqlite:///./var/community.sqlite3 \
  uv run --project services/api six-community serve --host 127.0.0.1 --port 8000
```

In another terminal, start the durable worker with the same environment:

```console
uv run --project services/api six-community-worker
```

Readiness is available at `GET /health/ready`; the generated API schema is at
`GET /openapi.json`. Registration is enabled by default for local development
and can be disabled after the first account is created.

## Quality and boundary checks

```console
uv run --project services/api --frozen ruff check services/api/src services/api/scripts services/api/tests
uv run --project services/api --frozen mypy services/api/src
uv run --project services/api --frozen pytest services/api/tests
uv run --project services/api --frozen python services/api/scripts/audit_boundary.py
uv run --project services/api --frozen python services/api/scripts/check_web_contracts.py
```

The last command reports exact method/path coverage against the open web
client. It exits non-zero with `--require-complete`; this prevents a partial API
from being represented as a contract-complete self-hosted stack.
