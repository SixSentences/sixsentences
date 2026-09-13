<p align="center">
  <img
    src="docs/assets/sixsentences-mark.png"
    width="96"
    height="96"
    alt="SixSentences mark"
  >
</p>

<h1 align="center">SixSentences</h1>

<p align="center">
  <strong>An open research workspace for evidence, data, interviews, and writing.</strong>
</p>

<p align="center">
  Keep sources, transformations, decisions, and generated work inspectable—from
  the first search to the final manuscript.
</p>

<p align="center">
  <a href="https://sixsentences.com">Website</a>
  &nbsp;·&nbsp;
  <a href="https://app.sixsentences.com">Hosted workspace</a>
  &nbsp;·&nbsp;
  <a href="#quick-start">Self-host</a>
  &nbsp;·&nbsp;
  <a href="CONTRIBUTING.md">Contribute</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/SixSentences/sixsentences/discussions">Discuss</a>
</p>

<p align="center">
  <a href="https://github.com/SixSentences/sixsentences/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/SixSentences/sixsentences?include_prereleases&sort=semver&style=flat-square"></a>
  <a href="https://github.com/SixSentences/sixsentences/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/SixSentences/sixsentences/ci.yml?branch=main&label=CI&style=flat-square"></a>
  <a href="https://github.com/SixSentences/sixsentences/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://img.shields.io/github/actions/workflow/status/SixSentences/sixsentences/codeql.yml?branch=main&label=CodeQL&style=flat-square"></a>
  <a href="https://github.com/SixSentences/sixsentences/actions/workflows/security.yml"><img alt="Security scan" src="https://img.shields.io/github/actions/workflow/status/SixSentences/sixsentences/security.yml?branch=main&label=security&style=flat-square"></a>
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-5C4EE5?style=flat-square"></a>
</p>

<p align="center">
  <img
    src="https://github.com/SixSentences/sixsentences/releases/download/v0.2.0-alpha.1/sixsentences-overview.gif"
    width="720"
    alt="SixSentences research workspace product overview"
  >
</p>

> [!WARNING]
> `v0.2.0-alpha.1` is an early self-hosting release. APIs, migrations, and UI
> contracts can change before `1.0`. Inspect outputs and validate methods;
> software cannot guarantee an exhaustive search or a scientifically valid
> conclusion.

## Research, end to end

SixSentences brings the working parts of a research project into one traceable
workspace:

| | |
| --- | --- |
| **Discover and review** | Search scholarly metadata, organize a library, deduplicate records, screen evidence, and preserve source context. |
| **Work with research data** | Import bounded tables, create deterministic profiles and analyses, and build figures with visible assumptions. |
| **Run studies** | Design surveys and text or spoken interviews with explicit participant information, consent, retention, and processing gates. |
| **Develop ideas** | Connect project knowledge, brainstorming, evidence, and decisions without hiding where content came from. |
| **Write with provenance** | Draft manuscripts and LaTeX documents while keeping citations, source anchors, and revisions inspectable. |
| **Automate deliberately** | Run background jobs and optional AI-assisted workflows against operator-selected providers, budgets, and data-processing controls. |

The community edition is a complete application stack: a Next.js workspace,
FastAPI service, PostgreSQL database, background worker, Caddy edge proxy,
Manifest V3 browser-capture client, typed Python research engine, and source for
the native macOS Companion. No customer data, production configuration,
provider credential, or signed native binary is bundled.

## Quick start

Requirements: Docker Engine and Docker Compose 2.33.1 or newer. Local mode uses
loopback-only public origins; use the documented TLS mode and host firewalling
before accepting real users or research data.

```console
git clone --branch v0.2.0-alpha.1 --depth 1 https://github.com/SixSentences/sixsentences.git
cd sixsentences
make up
```

Open <http://localhost> and sign in.

`make up` runs [`deploy/community/quickstart.sh`](deploy/community/quickstart.sh):
it writes a private local configuration, runs the fail-closed preflight, builds
the images, starts the stack health-gated, and prompts once for the first owner
account. That last step matters — self-signup is off until you configure mail,
so without an owner account nobody can sign in. The script never overwrites an
existing configuration and prints no secret value.

A tagged release also publishes the two images, so a deployment can skip the
build entirely:

```console
export SIX_API_IMAGE=ghcr.io/sixsentences/community-api:v0.2.0-alpha.1
export SIX_WEB_IMAGE=ghcr.io/sixsentences/community-web:v0.2.0-alpha.1-localhost
make up
```

The web client bakes its public origin at build time, so the published web image
serves `http://localhost` only; a public TLS deployment builds its own with
`make build-up`.

<details>
<summary>The same start as individual commands</summary>

```console
bash deploy/community/init-env.sh --local
bash deploy/community/preflight.sh .env.selfhost
docker compose --env-file .env.selfhost up --build --detach --wait
docker compose --env-file .env.selfhost run --rm api \
  six-community auth create-owner --email you@example.org --org "My Lab"
```

</details>

Read the [self-hosting guide](deploy/community/README.md) before enabling
registration, mail, external models, web search, or spoken interviews, and read
the [backup and restore runbook](deploy/community/BACKUP-RESTORE.md) before
storing user data. `make help` lists the other deployment shortcuts.

macOS users can also build the source-only
[Companion](apps/companion-macos/README.md) against the same deployment. It adds
local Apple Speech for spoken live interviews and brainstorming plus native
paper chat; the Docker quick start does not build or install a macOS app.

## Architecture

```mermaid
flowchart LR
    B[Browser] -->|HTTPS| C[Caddy]
    M[macOS Companion] -->|HTTPS · derived /api| C
    C --> W[Next.js web]
    C --> A[FastAPI API]
    A --> P[(PostgreSQL)]
    A --> D[(Application files)]
    Q[Background worker] --> P
    Q --> D
    X[Browser Capture] -->|pair and save| A
    A --> E[Research engine]
    Q --> E
    E -. operator-enabled .-> O[Metadata, mail, model, speech, and parser providers]
```

```text
src/sixsentences/       Python research engine and CLI
services/api/           Application API, migrations, worker, and tests
apps/web/               Next.js research workspace
apps/browser-extension/ Self-hostable Chromium capture client
apps/companion-macos/   Native Companion source, tests, and local bundle script
deploy/community/       Self-hosting, quick start, preflight, backup, and restore
compose.yaml            PostgreSQL + API + worker + web + Caddy
Makefile                Deployment shortcuts (make help)
```

Outbound services are optional and use only credentials supplied by the
operator. Billing, checkout, subscriptions, hosted-service administration, the
production deployment, and the marketing website are intentionally not part of
this repository.

| Community source | Hosted SixSentences |
| --- | --- |
| Run and modify the research workspace on infrastructure you control | Managed at [app.sixsentences.com](https://app.sixsentences.com) |
| Bring your own mail, metadata, model, speech, and parser providers | Integrated provider and operational setup |
| No payment or subscription code | Commercial plans and billing are operated separately |
| Marketing site is not included | [sixsentences.com](https://sixsentences.com) is deployed separately |

## Development

Use the committed lockfiles and the exact component checks:

```console
# Python engine
uv sync --frozen --group dev
uv run ruff check . && uv run mypy src/sixsentences && uv run pytest -q

# API
uv sync --project services/api --frozen --all-groups
uv run --project services/api pytest services/api/tests

# Web
(cd apps/web && npm ci && npm run typecheck && npm test && npm run build)

# Browser extension
(cd apps/browser-extension && npm ci && npm test && \
  APP_ORIGIN=https://research.example.org \
  API_ORIGIN=https://research.example.org/api \
  node scripts/build.mjs --out /tmp/sixsentences-extension)

# macOS Companion (on macOS with Xcode 16)
bash apps/companion-macos/Scripts/audit-community-source.sh
swift test --package-path apps/companion-macos --disable-sandbox
```

The CI workflow also validates the application contract, self-hosting boundary,
container builds, macOS Companion source, DCO sign-offs, dependency changes,
CodeQL results, and secret and configuration scans. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full local gate and pull-request
expectations.

## Contributing and security

Focused issues and pull requests are welcome. Contributions require per-commit
[Developer Certificate of Origin 1.1](DCO) sign-off and a fresh public
acceptance of the repository [CLA](CLA.md) on each pull request. The local
workflow uses no external CLA service or separately stored secret. Decisions
and maintainer responsibilities are described in
[GOVERNANCE.md](GOVERNANCE.md).

Report vulnerabilities through the repository's
[private advisory form](https://github.com/SixSentences/sixsentences/security/advisories/new),
never through a public issue. See [SECURITY.md](SECURITY.md).

## License

Code and documentation are available under the [Apache License 2.0](LICENSE),
except where a file says otherwise. Dependencies and adapted components retain
their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The
SixSentences name and visual identity are protected marks; see
[TRADEMARKS.md](TRADEMARKS.md).
