# Roadmap

This roadmap communicates direction, not delivery dates or commitments. Work is
accepted based on evidence, maintenance cost, and fit with the portable engine.

## `v0.1.0-alpha`: portable foundation

- Typed domain models for protocols, works, screening decisions, search records,
  and explicit PRISMA counts.
- Boolean query parsing and transparent DuckDB, OpenAlex, PubMed, Scopus, Web
  of Science, and IEEE Xplore translation.
- Checksummed local DuckDB/Parquet corpus snapshots.
- Conservative deduplication, citation snowballing, decomposed ranking, and
  Chao2 coverage estimation.
- Screening calibration and exploratory INCLUDE-vote overlap assessment, source-quote matching, method
  profiles, and PRISMA/PRISMA-S rendering.
- Small CLI and library API with pinned direct dependencies.

## Near-term candidates

- Stabilize public model and serialization boundaries through real downstream
  feedback.
- Add concise, synthetic end-to-end examples for local-corpus workflows.
- Expand malformed-input and adversarial-query coverage.
- Document statistical assumptions and appropriate interpretations alongside
  estimators and calibration helpers.
- Improve machine-readable provenance records without taking ownership of a
  caller's audit log.
- Establish reproducible release artifacts and changelog conventions.

## Later candidates

- Additional transparent query targets where translation loss can be surfaced.
- Pluggable, caller-owned metadata adapters with explicit network behavior.
- Larger public benchmark suites built only from redistributable fixtures.
- Additional standards-oriented exports with conformance tests.
- Performance work driven by published, reproducible measurements.

## Intentionally out of scope

- The SixSentences website and hosted application.
- Authentication, organizations, billing, subscriptions, or payments.
- Production deployment, backup, monitoring, or private provider configuration.
- Bundled proprietary corpora or redistribution of scholarly full text.
- Claims that software alone can guarantee an exhaustive search, a compliant
  systematic review, or a scientifically valid conclusion.

Propose roadmap changes through a focused GitHub issue. A roadmap entry is not
an acceptance decision; implementation still follows [GOVERNANCE.md](GOVERNANCE.md)
and [CONTRIBUTING.md](CONTRIBUTING.md).
