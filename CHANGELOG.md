# Changelog

All notable changes to the SixSentences open-source project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/) with PEP 440
equivalents for Python package metadata.

## [Unreleased]

### Added

- Nothing yet.

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

[Unreleased]: https://github.com/SixSentences/sixsentences/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/SixSentences/sixsentences/releases/tag/v0.1.0-alpha.1
