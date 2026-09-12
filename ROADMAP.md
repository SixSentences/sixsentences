# Roadmap

SixSentences is growing toward a portable set of auditable research-workflow
primitives. Literature discovery is the first deep vertical, not the limit of
the project. This roadmap communicates direction rather than dates or delivery
commitments.

## `v0.1.0-alpha.1`: portable foundation

### Literature and evidence

- Typed models for protocols, works, screening decisions, search records, and
  explicit PRISMA counts.
- Boolean query parsing plus transparent DuckDB, OpenAlex, PubMed, Scopus, Web
  of Science, and IEEE Xplore translation.
- Builder and verifier for caller-owned, checksummed DuckDB/Parquet corpora.
- Conservative DOI deduplication, citation snowballing, decomposed ranking,
  retraction metadata, and Chao2 coverage estimation.
- Screening calibration, exploratory reviewer overlap, source-quote matching,
  method profiles, and PRISMA/PRISMA-S reporting primitives.

### Research data

- Bounded CSV, TSV, JSON, and ordinary XLSX import with fail-closed archive and
  resource limits.
- Complete deterministic profiles over every accepted row; no silent sampling
  or truncation.
- Typed recipes for missingness, descriptive statistics, grouped summaries,
  Pearson correlation, and classic DerSimonian–Laird random-effects pooling.
- Explicit method and interpretation limits returned with analysis results.

### Project foundation

- Typed Python 3.12–3.14 library and CLI with pinned dependencies.
- Tests, CodeQL, dependency review, DCO verification, release smoke tests,
  checksums, an SBOM, and artifact attestations.
- Apache-2.0 code and documentation with separate trademark protection for the
  SixSentences name and visual identity.

## `v0.2.0-alpha`: contract and conformance candidates

- Publish a fully offline, synthetic end-to-end workflow exercised in CI.
- Version public JSON and serialization contracts with golden round trips and
  documented migration behavior.
- Add a cross-database query-translation conformance corpus that records syntax
  loss rather than hiding it.
- Harden and document OpenAlex paging, retry, timeout, and redaction behavior.
- Define a portable artifact-provenance envelope for inputs, parameters,
  versions, revisions, and output hashes.
- Add bounded RIS/BibTeX exchange with explicit loss and injection tests.
- Expose PRISMA-S-style search records through the CLI.

## Later candidates

- Deterministic SVG charts with provenance sidecars, starting with bar, scatter,
  and forest plots rather than a general rendering service.
- A portable manuscript model with source anchors, preconditioned edits, and
  deterministic conflict results.
- Privacy-conscious interchange contracts for survey responses and anchored
  interview evidence, using synthetic fixtures only.
- Additional transparent query targets, public metadata adapters, and
  standards-oriented exports where loss and network behavior can be surfaced.
- Public, redistributable benchmark suites and performance work backed by
  reproducible measurements.

## Intentionally outside this repository

- The SixSentences website and hosted workspace UI.
- Accounts, organizations, authentication, collaboration servers, billing,
  subscriptions, or payments.
- Hosted AI orchestration, private provider configuration, production
  deployment, backup, monitoring, or analytics.
- Customer data, proprietary corpora, or redistribution of scholarly full text.
- Claims that software alone guarantees an exhaustive search, a compliant
  method, or a scientifically valid conclusion.

The hosted product may implement integrated versions of roadmap areas before a
portable primitive is suitable for open source. That does not make hosted code
part of this repository by implication.

Propose changes through a focused GitHub issue. A roadmap entry is not an
acceptance decision; implementation still follows [GOVERNANCE.md](GOVERNANCE.md)
and [CONTRIBUTING.md](CONTRIBUTING.md).
