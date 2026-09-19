# Native multi-source retrieval

## Architecture decision

SixSentences adopts the useful architectural ideas from
[`local-deep-research`](https://github.com/LearningCircuit/local-deep-research)
without embedding its application, database, authentication, queue, UI, agent
runtime or dependency graph. No upstream source code is copied and no LDR
runtime is required.

The integration stays native to the existing SixSentences boundaries:

- the canonical Boolean query and approved protocol remain authoritative;
- every database is a bounded connector returning the shared work model;
- all candidates share one deduplication, integrity, ranking and screening path;
- each database arm produces its own PRISMA-S execution and explicit failure;
- tenancy, capacity, cancellation, event logging and provider gates remain in
  the existing API and worker runtime.

This avoids a second identity, persistence, retention and observability system.

## PubMed database arm

The first additional source is PubMed through the official
[NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/):

1. The canonical query is translated to PubMed field syntax.
2. ESearch returns a bounded, deterministic PMID sequence.
3. EFetch retrieves metadata in bounded batches.
4. The connector normalizes DOI, PMID, PMCID, title, abstract, date, authors,
   venue, book metadata and publication type.
5. Results merge by DOI, provider identity, then conservative normalized title
   and year matching.
6. The canonical candidates continue through the ordinary review pipeline.

The connector has fixed NCBI endpoints, bounded request bodies, timeouts,
retries, request pacing and result counts. Search and fetch requests use POST so
the translated query, contact email and optional API key do not enter request
URLs. Errors do not include queries, response bodies, identifiers or secrets.

## Runtime and privacy boundary

PubMed is off by default. An operator must deliberately configure:

```env
SIX_PUBMED_ENABLED=true
SIX_PUBMED_EMAIL=operator@example.org
SIX_PUBMED_API_KEY=
```

The email is the NCBI client contact identity. The optional API key is a secret
and must remain in the deployment environment or secret store. The browser
receives only `runtime_capabilities.pubmed` from `/models`; neither credential
is exposed to the client.

Enabling PubMed sends only the translated scholarly search query, including
the selected year filters, to NCBI. SixSentences account, workspace, project
and protocol identifiers are not sent. Operators must disclose this external
query transfer in their privacy information and make NCBI's
[Disclaimer and Copyright notice](https://www.ncbi.nlm.nih.gov/home/about/policies/)
visible to users. Search input must not contain secrets or unnecessary personal
data. PubMed abstracts remain third-party source material.

The UI offers PubMed only when the runtime capability is true. The API rejects
unavailable requests before reserving capacity, and the worker checks the gate
again immediately before provider egress. A paused or approval-gated run stays
resumable if the operator disables PubMed; completed runs remain readable.

## Audit contract

A PubMed execution records:

- source arm and provider;
- the effective translated query, including date constraints;
- result limit, provider total, retained count and truncation state;
- an explicit success or sanitized failure state; and
- append-only source and corpus-version provenance for every final record.

After cross-source deduplication, source records describe only the final unique
screening set while retaining every provider/version path that found it.

## Research planning

Deep Quick Answer turns use a deterministic server-owned research plan with
three to five complementary subquestions, stable IDs and explicit coverage
criteria. The existing agent uses those angles to diversify bounded searches;
there is no second agent or additional model call. Exact user-approved single
web queries remain outside this expansion path.

Coverage counts only successful search passes that returned at least one
traceable scholarly ID or public source URL. Failed and empty calls cannot
satisfy it. Semantic proof that every criterion has independent support is not
claimed by the current implementation.

## Operator rollout checklist

Do not enable PubMed merely because the code is present. Before rollout:

- update the deployment privacy information for NCBI query disclosure;
- display or link the NCBI disclaimer and copyright policy;
- run public or synthetic retrieval and deterministic deduplication fixtures;
- verify complete PRISMA-S provenance and cancellation behavior;
- check logs and errors for secret, query and personal-data leakage; and
- document the measured retrieval benefit for the deployment's subject areas.
