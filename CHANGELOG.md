# Changelog

All notable changes to the SixSentences open-source project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/) with PEP 440
equivalents for Python package metadata.

## [Unreleased]

### Added

- `sixsentences --version` and `six-community --version` report the installed
  version of each command-line tool.
- Every `sixsentences` subcommand accepts `--output PATH`. A file receives
  exactly the bytes stdout would have received, and is written only after the
  command succeeds.
- `six-community doctor --json` emits one object per check — `name`, `state` and
  `detail` — as the whole of standard output, so a deployment can be monitored
  rather than read. The exit code is unchanged in both modes.

### Changed

- The server package reads its version from the installed distribution metadata
  instead of a second literal in `sixsentences_server/__init__.py`.
- `sixsentences prisma --format svg --output PATH` now ends the file with a
  newline, like every other written result.

## [0.2.0-alpha.1] - 2026-09-14

### Added

- A self-hostable FastAPI application service with database migrations,
  background workers, authentication, mail delivery, and the API contracts used
  by the open web workspace.
- Community workflows for projects, literature discovery and review, research
  data, figures, surveys, text and spoken interviews, knowledge, brainstorming,
  and manuscript writing.
- Self-hostable browser-capture and macOS Companion source with deployment-bound
  origins, contract tests, and explicit distribution boundaries.
- Source and tests for the macOS Companion, including local Apple Speech for
  spoken interviews and brainstorming, native paper chat, explicit self-hosted
  origin binding, and account-data purge on disconnect.
- A PostgreSQL, API, worker, web, and Caddy reference deployment with generated
  local secrets, fail-closed preflight validation, backup, restore, and
  authenticated erasure-journal replay.
- Operator-configurable scholarly-metadata, model, web-search, speech, mail, and
  document-parser integrations with explicit processing and budget controls.
- Full application CI, source and configuration security scanning, container
  build validation, and release provenance/SBOM automation.

### Changed

- Reframed the repository from an engine plus inspectable client snapshot into
  the complete SixSentences community application.
- Updated contribution, governance, security, self-hosting, and release policy
  for the full application stack.

### Boundaries

- Billing, checkout, subscriptions, commercial-plan enforcement, hosted-service
  administration, the production deployment, and the marketing website remain
  separately operated and are not included.
- No provider credential, production configuration, customer data, participant
  data, backup, private prompt, proprietary corpus, signed native binary,
  signing identity, notarization credential, or update feed is distributed.

## [0.1.0-alpha.1] - 2026-09-12

### Added

- Boolean query parsing and transparent compilation for DuckDB, OpenAlex,
  PubMed, Scopus, Web of Science, and IEEE Xplore.
- A builder and verifier for caller-owned, checksummed DuckDB/Parquet corpora.
- Conservative DOI deduplication and non-destructive companion-report links.
- Decomposed ranking, citation snowballing, query-expansion validation, and
  capture-frequency coverage estimation.
- Screening calibration, reviewer-overlap assessment, source-quote matching,
  and method profiles.
- Caller-supplied PRISMA flow and PRISMA-S-style search-record rendering.
- Bounded CSV, TSV, JSON, and XLSX import with complete deterministic profiles.
- Typed missingness, descriptive, grouped-summary, Pearson-correlation, and
  DerSimonian-Laird random-effects analysis recipes.
- Typed Python library and command-line interfaces for Python 3.12 and newer.
- A sanitized Next.js research-workspace client covering literature, library,
  projects, data, figures, surveys, interviews, knowledge, brainstorming, and
  manuscript interfaces.
- Typed browser-side API contracts, streaming progress, provenance displays,
  responsive workspace layouts, and focused web-client tests.

### Boundaries

- The web client requires a separately supplied compatible API; the Python
  package is not that API and the release is not a complete self-hosting stack.
- The marketing site, hosted SaaS backend, billing, pricing, administration,
  operations, browser extension, and macOS companion are not included.

### Security

- Distribution boundaries exclude backend and operator code, credentials,
  production artifacts, private configuration, customer data, and proprietary
  datasets.

[Unreleased]: https://github.com/SixSentences/sixsentences/compare/v0.2.0-alpha.1...HEAD
[0.2.0-alpha.1]: https://github.com/SixSentences/sixsentences/releases/tag/v0.2.0-alpha.1
[0.1.0-alpha.1]: https://github.com/SixSentences/sixsentences/releases/tag/v0.1.0-alpha.1
