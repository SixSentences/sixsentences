# Roadmap

This roadmap communicates direction, not dates or delivery commitments. Each
item still requires a scoped issue, review, and the quality gates in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Current stabilization, 2026-10-01

- Implemented, pending the next reviewed release: responsive personal boards
  with persistent resizing, keyboard controls and recovery of obscured notes.
- Release gates now include a provider-free fresh Compose startup, upgrade from
  `v0.2.0-alpha.1`, and isolated database/file restore with post-backup account
  erasure replay. A green build alone is not runtime acceptance; retain the
  successful workflow receipt. A separate required phase restores the
  pre-upgrade snapshot under the pinned alpha.1 API, retains newer deletions,
  and verifies the prior schema. This does not preserve post-snapshot writes.
  Alpha.1's invalid PostgreSQL Boolean default needs an explicitly recorded,
  hash-bound bootstrap correction; unmodified alpha.1 fresh installation is
  not claimed to work. Fresh candidate migrations remain a separate gate.
- Image publication verifies a signed main-branch release and records immutable
  digests, SPDX SBOMs, build provenance and checksums. Initially Linux amd64 and
  localhost web origins only; multi-architecture/TLS generic images are not
  promised.
- Next: measure representative search, data/participant analysis and writing
  journeys against independent synthetic/public evidence, including cost and
  failure recovery. Do not treat model agreement as correctness.
- Provider expansion remains operator-configured and review-bound. PubMed and
  Jev keep their explicit rollout/evaluation gates; extra model choices do not
  silently change screening or participant-data processing.

This is a community roadmap, not the hosted deployment state. Durable workers
already exist; a queue replacement is not a prerequisite for the next release.

## `v0.2.0-alpha.1`: self-hostable research workspace

- Complete community application: Python engine, FastAPI service, background
  worker, Next.js workspace, PostgreSQL migrations, and reference Caddy/Docker
  Compose deployment.
- Literature discovery, library and review, projects, Data Lab, figures,
  surveys, text and spoken interviews, knowledge, brainstorming, and manuscript
  workflows.
- Operator-supplied mail, scholarly-metadata, model, speech, search, and parser
  integrations with explicit configuration and processing gates.
- Fail-closed local/TLS deployment configuration, bounded resources, backup and
  restore tooling, and authenticated erasure-journal replay.
- Engine, API, web, contract, self-hosting, security, dependency, container,
  DCO, and release automation.
- Apache-2.0 licensing with separate trademark terms. Billing, commercial plans,
  the hosted production deployment, and the marketing site remain separate.

## Stabilize the public contract

- Version the API and event-stream contracts with machine-readable
  compatibility fixtures.
- Add synthetic browser end-to-end tests for onboarding, representative
  research workflows, public participation, retention, and account erasure.
- Publish migration rehearsal fixtures for every supported upgrade path.
- Define portable provenance envelopes for project inputs, provider settings,
  transformations, revisions, and output hashes.
- Expand deterministic research exchange formats, beginning with bounded
  RIS/BibTeX import and export with explicit loss reporting.
- Harden accessibility, CSP, browser storage, rate limits, and capability-link
  handling against an explicit threat model.

## Operate with confidence

- Test encrypted off-site backup and isolated restore recipes against released
  images on a schedule.
- Publish multi-architecture container images only after both architectures pass
  the same smoke and migration gates.
- Add portable object storage without weakening tenant isolation, erasure, or
  backup consistency.
- Add deployment observability with privacy-preserving defaults and no request
  bodies or public capability URLs in logs.
- Define a supported high-availability profile after measuring database,
  storage, worker, and provider failure modes.
- Maintain adversarial provider evaluations for spoken and generated research
  workflows without storing participant content in public evidence.

## Research-method depth

- Add cross-database query translation conformance fixtures that surface syntax
  loss rather than hiding it.
- Improve OpenAlex paging, retry, timeout, provenance, and redaction behavior.
- Extend source-anchored screening, extraction, synthesis, and manuscript
  workflows with deterministic conflict behavior.
- Add reproducible scientific visuals and statistical recipes only with
  explicit assumptions, validation fixtures, and method limits.
- Improve interchange for consented survey and interview evidence using
  synthetic public fixtures.

## Intentionally separate

- Payments, pricing, subscriptions, checkout, and commercial-plan enforcement.
- Hosted-service administration, private production configuration, customer
  data, production evidence, analytics, and provider credentials.
- The SixSentences marketing and landing website.
- Redistribution of proprietary corpora or scholarly full text without rights.
- Any claim that software alone guarantees completeness, compliance, causality,
  or scientific validity.

Track implementation through [GitHub issues](https://github.com/SixSentences/sixsentences/issues)
and use [discussions](https://github.com/SixSentences/sixsentences/discussions)
for ideas that are not yet ready to scope.
