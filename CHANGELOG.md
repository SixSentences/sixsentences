# Changelog

All notable changes to the SixSentences open-source project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/) with PEP 440
equivalents for Python package metadata.

## [Unreleased]

### Fixed

- Keep files dropped in the Data Hub import dialog staged until confirmation;
  modal drops no longer trigger the page's immediate import action. Direct
  page and project-card imports remain available when no dialog is open.
- Preserve library search results matched by the server against authors, DOI,
  abstracts and grouped source metadata, while retaining collection filters
  and sorting in the web client.

## [0.2.0-alpha.4] - 2026-10-01

### Fixed

- Orchestrate source and image evidence in one release workflow: prepare and
  validate the complete draft inventory before publishing the immutable release.
  This addresses #185 without overwriting images, clobbering assets or disabling
  release protection. Application logic and migrations are unchanged.
- Update the API's runtime PDF parser from pypdf 6.18.1 to 6.19.0 for three
  denial-of-service advisories. The specific vulnerable features are not used
  by the current API, but it processes untrusted PDFs. No other canonical
  third-party dependency versions change; see the dated dependency triage.
- Preserve the published alpha.3 source/Python release and its successful image
  builds, scans and isolated drill. GitHub rejected its later image-evidence
  upload because the release was already immutable; those assets remain absent.
  Alpha.4 must pass its own complete publication and verification gates.

## [0.2.0-alpha.3] - 2026-10-01

### Fixed

- Accept BuildKit's exact SLSA v1 provenance format when validating published
  container evidence, while preserving strict schema, digest, OCI source/tag
  and runtime-receipt checks. No application behavior, migration or third-party
  dependency version changes are included.
- Keep the published alpha.2 source release and Python artifacts valid and
  unchanged. Its image builds, scans and isolated drill passed, but provenance
  metadata validation prevented image-evidence publication; existing image tags
  are not overwritten. Alpha.3 uses new immutable tags and requires fresh evidence.

## [0.2.0-alpha.2] - 2026-10-01

### Added

- Persistent pinboard resizing, compact mobile editing and a no-write action
  to recover notes obscured by the composer. Old clients preserve saved sizes;
  downgrade compatibility is documented in the self-hosting guide.
- Required provider-free Compose startup, released-image upgrade and isolated
  restore rehearsal, including authenticated post-backup account-erasure replay
  and pre-upgrade snapshot rollback under the pinned prior API. Published image
  SBOM/provenance/checksum receipts require the same runtime checks by digest.

- A personal research pinboard on the workspace homepage: coloured notes, cards
  and circles with editable text, draggable or keyboard-controlled positions,
  responsive layouts and revision-bound autosave. Notes are creator-private,
  participate in account export/erasure, and are never implicit AI context.
  Self-hosted upgrades add the `20260928_0003` database migration; see the API
  README for its additive upgrade and destructive downgrade boundary.
- `sixsentences --version` and `six-community --version` report the installed
  version of each command-line tool.
- Every `sixsentences` subcommand accepts `--output PATH`. A file receives
  exactly the bytes stdout would have received, and is written only after the
  command succeeds.
- `six-community doctor --json` emits one object per check — `name`, `state` and
  `detail` — as the whole of standard output, so a deployment can be monitored
  rather than read. The exit code is unchanged in both modes.
- `sixsentences query --target central` translates a query for Cochrane CENTRAL,
  as searched in the Cochrane Library Search Manager. Every term carries a field
  label (`:ti,ab,kw` when the query names no field), `NOT` is written between its
  operands, and a literal the Search Manager would read as syntax is refused
  rather than rewritten. A query CENTRAL cannot express still translates for the
  other targets.

### Changed

- Update Next.js to 16.3.8 and repair an embedded Python syntax error in the
  self-hosted erasure-journal restore selector, covered by a compile regression.
- Replace the baseline migration's PostgreSQL-invalid Boolean default `0` with
  `sa.false()`. The alpha.1-schema upgrade rehearsal records this single-literal
  compatibility bootstrap and its original/corrected hashes; it does not
  misrepresent the unmodified historical installer as functional.
- Remove the API/worker readiness cycle during fresh startup and backup/restore.
  The optional literature corpus no longer blocks the wider workspace; actual
  database, storage, queue and live-worker health remain required. The immutable
  prior API receives an explicitly recorded synthetic corpus in upgrade tests;
  the fresh candidate is checked without one.
- Verify and select recovery journals as the ordinary API user, streaming
  host-owned staging bytes into private temporary files instead of inaccessible
  root-only container reads. Shutdown failures now stop restore before state
  replacement; authenticated journal and permission checks remain fail-closed.

- The server package reads its version from the installed distribution metadata
  instead of a second literal in `sixsentences_server/__init__.py`.
- `sixsentences prisma --format svg --output PATH` now ends the file with a
  newline, like every other written result.

### Fixed

- Participant-facing screens now declare the language they are written in: the
  participant information, the talk page, the voice and text interview screens,
  and the terms re-acceptance and privacy-update notices carry `lang="de"` or
  `lang="en"` from the study's or account's language. The document itself stays
  `lang="en"`, so a German screen is no longer read aloud with English
  pronunciation or offered for translation from English.

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

[Unreleased]: https://github.com/SixSentences/sixsentences/compare/v0.2.0-alpha.4...HEAD
[0.2.0-alpha.4]: https://github.com/SixSentences/sixsentences/releases/tag/v0.2.0-alpha.4
[0.2.0-alpha.3]: https://github.com/SixSentences/sixsentences/releases/tag/v0.2.0-alpha.3
[0.2.0-alpha.2]: https://github.com/SixSentences/sixsentences/releases/tag/v0.2.0-alpha.2
[0.2.0-alpha.1]: https://github.com/SixSentences/sixsentences/releases/tag/v0.2.0-alpha.1
[0.1.0-alpha.1]: https://github.com/SixSentences/sixsentences/releases/tag/v0.1.0-alpha.1
