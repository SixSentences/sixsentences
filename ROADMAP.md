# Roadmap

SixSentences is growing as open research software, not only as a
systematic-literature-review toolkit. The project currently publishes a portable
Python engine and a sanitized research-workspace web client. This roadmap
communicates direction, not dates or delivery commitments.

## `v0.1.0-alpha.1`: open foundation

### Python research engine

- Typed models for protocols, works, screening decisions, search records, and
  explicit PRISMA counts.
- Boolean query parsing plus transparent DuckDB, OpenAlex, PubMed, Scopus, Web
  of Science, and IEEE Xplore translation.
- Builder and verifier for caller-owned, checksummed DuckDB/Parquet corpora.
- Conservative DOI deduplication, citation snowballing, decomposed ranking,
  retraction metadata, and assumption-gated Chao2 coverage estimation.
- Screening calibration, exploratory reviewer overlap, source-quote matching,
  method profiles, and PRISMA/PRISMA-S-style reporting primitives.
- Bounded CSV, TSV, JSON, and ordinary XLSX import with fail-closed archive and
  resource limits.
- Complete deterministic profiles over every accepted row; no silent sampling
  or truncation.
- Typed recipes for missingness, descriptive statistics, grouped summaries,
  Pearson correlation, and classic DerSimonian–Laird random-effects pooling.

### Research-workspace web client

- Sanitized Next.js source for literature runs and review, library, projects,
  Data Lab, Visual Lab, surveys, interviews, knowledge, brainstorming, and
  manuscript/LaTeX workflows.
- Typed browser-side API calls, streaming progress, source and provenance
  presentation, responsive workspace layouts, and focused UI tests.
- Local, fail-closed development defaults for API and application origins.
- No billing, pricing, checkout, subscription, administration, operator,
  production, marketing, extension, or companion source in the snapshot.

The web client requires a separately supplied compatible API. The Python engine
does not implement that server contract, and this release is not a complete
self-hosting stack.

### Project foundation

- Python 3.12–3.14 package and CLI with a hash-locked environment.
- Node.js 22.9+ client with an npm lockfile.
- Automated tests, static analysis, dependency review, DCO verification,
  release smoke tests, checksums, an SBOM, and artifact attestations where
  configured by the release workflow.
- Apache-2.0 project code and documentation, third-party notices, and separate
  trademark protection for the SixSentences name and visual identity.

## Next: stabilize the public contract

### Engine and research methods

- Publish a fully offline, synthetic end-to-end workflow exercised in CI.
- Version public JSON and serialization contracts with golden round trips and
  documented migration behavior.
- Add a cross-database query-translation conformance corpus that records syntax
  loss instead of hiding it.
- Harden and document OpenAlex paging, retry, timeout, and redaction behavior.
- Define a portable artifact-provenance envelope for inputs, parameters,
  versions, revisions, and output hashes.
- Add bounded RIS/BibTeX exchange with explicit loss and injection tests.
- Expose PRISMA-S-style search records through the CLI.

### Web client

- Define and version a minimal compatible-API profile for authentication,
  capability discovery, errors, streaming events, and public sharing.
- Add a synthetic API fixture or contract harness so core interface journeys can
  be tested without the hosted service.
- Add browser end-to-end coverage for login boundaries, navigation, loading,
  unavailable capabilities, and representative research workflows.
- Make production builds reproducible without ambient network downloads and
  publish a documented software bill of materials for distributed web
  artifacts.
- Continue accessibility, Content Security Policy, dependency, and
  browser-storage hardening.

## Later candidates

- Deterministic SVG charts with provenance sidecars, beginning with bar,
  scatter, and forest plots rather than a general rendering service.
- A portable manuscript model with source anchors, preconditioned edits, and
  deterministic conflict results.
- Privacy-conscious interchange contracts for survey responses and anchored
  interview evidence, using synthetic fixtures only.
- Additional transparent query targets, public metadata adapters, and
  standards-oriented exports where loss and network behavior can be surfaced.
- A deliberately minimal reference API for selected local workflows, if its
  security and maintenance boundary can be made credible.
- Independent source-release reviews for the browser extension and macOS
  companion before deciding whether they belong in this repository or in
  separate projects.

## Intentionally outside this repository

- The SixSentences marketing and landing website.
- The hosted SaaS backend, production databases, workers, provider
  orchestration, private prompts, and model credentials.
- Billing, pricing, checkout, subscriptions, payments, administration, and
  operator tooling.
- Production deployment, backup, recovery, monitoring, and analytics
  infrastructure.
- Browser-extension and macOS-companion source or signed binaries in the
  current release.
- Customer data, production configuration, proprietary corpora, or
  redistribution of scholarly full text.
- Claims that software alone guarantees an exhaustive search, a compliant
  method, or a scientifically valid conclusion.

The hosted product may implement integrated versions of roadmap areas before a
portable component is ready for open source. That does not make hosted code
part of this repository by implication.

Propose changes through a focused GitHub issue. A roadmap entry is not an
acceptance decision; implementation still follows
[GOVERNANCE.md](GOVERNANCE.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
