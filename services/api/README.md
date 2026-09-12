# SixSentences Community Server

This directory is the self-hosted API and worker service for the complete
SixSentences research workspace. It includes research discovery, libraries,
Writer, surveys, interviews and voice, agents, figures, datasets, repository
analysis, authentication, transactional mail and durable workers.

Commercial checkout, subscriptions, billing, waitlist operations, platform
administration and the hosted operator's deployment evidence are deliberately
outside this source tree. Every community workspace receives the same complete
capability profile; local resource and provider-spend limits remain as safety rails.

## Quickstart

Requirements: the public SixSentences monorepo with `sixsentences-engine` at
`0.2.0a1`, Docker with Compose v2, and `openssl` for local secret generation.

From `services/api`:

1. Run `./scripts/init-env.sh` once. It creates a mode-0600 `.env` without printing secrets.
2. Run `docker compose build && docker compose up -d postgres`.
3. Run the fresh community migration with `docker compose run --rm migrate`.
4. Bootstrap the first tenant owner interactively:
   `docker compose run --rm api six-community auth create-owner --email you@example.org --org "My Lab"`.
5. Build the initial local search corpus:
   `docker compose run --rm api six-community corpus sync --profile micro`.
6. Start the application with `docker compose up -d api worker`, then wait for
   both services to become healthy in `docker compose ps`.
7. Sign in through the included community web client configured for
   `http://localhost:8000`. The API schema is at `http://localhost:8000/docs`.

Self-signup is off by default. `create-owner` prompts twice for the password on a
hidden terminal, creates only a tenant-scoped `owner`, and never prints a password,
session token or API key. Additional members can then be managed inside the workspace.

Provider keys are optional. Features that require a provider stay unavailable until
both a key and the corresponding data-processing acknowledgement are configured.
Public spoken interviews additionally require `SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED=true`.
The image includes the pinned Tectonic/SyncTeX and ffmpeg/ffprobe toolchains used
by Writer compilation and interview media processing.

Before production use, read [OPERATIONS.md](OPERATIONS.md). In particular, back
up the PostgreSQL database, `/data`, `/privacy`, and the encrypted runtime secret
copy as distinct recovery inputs. After any data restore, the newest independently
held erasure ledger must be verified and replayed before API traffic resumes.

## Development

Use Python 3.12 and install `requirements-dev.lock` in an isolated environment.
Then run:

```text
ruff check .
ruff format --check .
mypy src
alembic upgrade head
pytest
python scripts/check_web_contracts.py --require-complete
python scripts/audit_community_export.py
```

The generated `SOURCE_EXPORT_MANIFEST.json` binds every copied source file to the
pinned private source commit. `COMMUNITY_EXPORT_MANIFEST.json` binds the final,
sanitized service files. The export scripts refuse an unexpected commit or a
non-empty output directory. The machine-readable
[`contracts/community-parity.json`](contracts/community-parity.json) reconstructs
the exact Core-to-Community route boundary and pins both the central 334-route
web contract and all 349 transports declared in `apps/web/src/lib/api.ts`.
