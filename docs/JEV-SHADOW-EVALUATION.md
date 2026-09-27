# TypeSafe Jev shadow evaluation

Status: **evaluation-only, disabled by default** (reviewed 2026-09-26).

Jev 1.13 is a hosted, proprietary typed-decision model. It can return a
probability distribution for atomic screening criteria, which makes it useful
as an inexpensive experimental signal. It does not return a rationale or a
verifiable quote, so it cannot satisfy the SixSentences evidence contract for a
screening decision.

## Product boundary

The Jev adapter is deliberately separate from `LLMPool` and the authoritative
screening ensemble. It is available only to the operator-run shadow evaluator.
Its output:

- never writes a `ScreeningDecision`;
- never changes include/exclude/unsure, PRISMA counts, ranking or the human
  review queue;
- is marked advisory and records the exact `jev-1.13.0` model, question-schema
  hash, input hash, probability vectors, confidence, usage, request ID, latency
  and retry count; and
- fails without interrupting the normal screening pipeline.

Each inclusion criterion is evaluated as `met`, `not_met` or `unclear`; each
exclusion criterion as `applies`, `does_not_apply` or `unclear`. Low-confidence
or unclear answers resolve to an advisory `unsure`. An advisory `exclude` is
never permission to exclude a study.

## Data and legal boundary

The adapter sends only the public title and abstract as state and the individual
criterion text as typed questions. The broader research question and Boolean
query are not sent. Work IDs, DOIs, authors, venues, tenant/user/project
identifiers, notes, uploads and full texts are excluded structurally. The
operator must explicitly confirm the exact egress scope and that the source
licence permits this third-party processing. Public accessibility alone is not
a redistribution or processing licence.

Do not use the standard service for private or unpublished material, interview
or survey content, reviewer notes, patient-level information, special-category
personal data, or full text. Before any self-hosted or hosted deployment pilot, complete and
archive all of the following:

1. provider agreement and DPA acceptance by the correct legal entity;
2. written retention or Zero Data Retention terms covering this use;
3. SCC transfer-impact and current subprocessor/security review;
4. data-subject and data-category coverage appropriate to bibliographic text;
5. privacy-notice, provider-inventory and records-of-processing updates; and
6. an explicit user opt-in bound to the visible protocol and egress scope.

TypeSafe states that customer inputs are not used to train or fine-tune model
weights without consent, but the published standard retention boundary is not
fixed and the service is US-hosted. Its current DPA schedule says sensitive data
is not applicable. Those statements are not sufficient for private or
biomedical production traffic. This document is an engineering control record,
not legal advice.

## Quality gate

The existing release gates remain authoritative: at least 95% aggregate title
and abstract sensitivity, at most 5% false-exclusion rate, and the documented
worst-domain floors. Jev must be evaluated against frozen, independently human-
adjudicated labels; model output must never become ground truth.

The only screening-specific community benchmark found during review is new,
single-commit and not peer-reviewed. It reports about 90% pooled recall over
16,015 Cohen-2006 records, with per-review recall as low as 53.8%. Even its
`p(exclude) >= 0.99` route reports 95.5% recall. This evidence is useful for
experimentation but is not adequate for automated exclusion.

Promotion beyond shadow mode requires a predeclared, reproducible multi-domain
evaluation, calibration by language/domain, adversarial and missing-abstract
tests, and renewed validation for every model or question-schema change.

## Running the isolated evaluator

Prepare one immutable JSON artifact. Unknown fields fail validation; the input
schema deliberately has no author, DOI, full-text, note or tenant field:

```json
{
  "schema_version": 1,
  "metadata": {
    "dataset_id": "public-screening-fixture",
    "version": "2026-09-26",
    "source_uri": "https://example.org/pinned-public-dataset",
    "license": "CC-BY-4.0",
    "license_verified": true
  },
  "protocol": {
    "question": "Locally retained protocol question",
    "inclusion_criteria": ["Adults with the condition are studied"],
    "exclusion_criteria": ["The publication is an editorial"],
    "query_string": "condition AND adults"
  },
  "records": [
    {
      "record_id": "fixture-1",
      "title": "Synthetic public title",
      "abstract": "Synthetic public abstract.",
      "domain": "medicine",
      "gold": "include"
    }
  ]
}
```

The research question and Boolean query remain local. After setting the
server-side `SIX_TYPESAFE_API_KEY`, run only from a clean, committed revision:

```bash
cd services/api
uv run six-community quality jev-shadow \
  --dataset evidence/jev-public.json \
  --out evidence/jev-shadow-report.json \
  --limit 1000 \
  --budget-usd 2.69 \
  --minimum-confidence 0.80 \
  --confirm-provider-spend \
  --confirm-public-bibliographic-data
```

The command reserves the full documented 64k-token context price for every
selected request and disables retries, so the declared amount is a hard
pre-call ceiling. The output omits raw titles, abstracts and criteria. It is an
immutable evaluation artifact, not a screening-decision export. By default the
command exits non-zero unless every aggregate, coverage and worst-domain gate
passes; the artifact is still retained for audit.

## Reviewed primary sources

- [TypeSafe API reference](https://docs.typesafe.ai/api)
- [Models, pricing and rate limits](https://docs.typesafe.ai/models)
- [Jev 1.13 known limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- [Confidence guidance](https://docs.typesafe.ai/confidence)
- [Master Customer Agreement](https://typesafe.ai/legal/mca)
- [Data Processing Addendum](https://typesafe.ai/legal/data-processing)
- [Privacy Policy](https://typesafe.ai/legal/privacy-policy)
- [Community screening benchmark](https://github.com/Saeedabdf/jev-screening-benchmark)
