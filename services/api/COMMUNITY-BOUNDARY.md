# Community API boundary

The service is a clean community implementation: its schema, tokens, provider
credentials, migrations, queues and public-participation controls are owned by
the deployment. It does not connect to or depend on any hosted SixSentences
account, infrastructure, database or operator system.

Included runtime surfaces:

- organization-isolated local accounts, sessions and API keys;
- projects, research discovery jobs, library records and provenance;
- tabular data profiling and declarative figures;
- surveys and consent-bound public responses;
- public text and spoken interviews with versioned purpose/scope binding,
  participant notices, explicit consent, concurrency/duration/turn limits,
  transcript-only spoken processing and fail-closed provider configuration;
- writer documents, knowledge pages with optimistic revision checks, and
  brainstorming jobs;
- a durable database queue and deployment-owned SMTP delivery; and
- SQLite/PostgreSQL schema migrations, health checks and container packaging.

Always excluded are purchase/account-tier flows, hosted operations and support
tools, deployment credentials, production configuration, customer or
participant data, databases, file stores, logs, analytics, backups, release
evidence, private legal/marketing text and private version-control history.

## Web-client contract status

`scripts/check_web_contracts.py` compares this application with the method/path
contracts used by `apps/web/src/lib/api.ts`. That report, rather than this prose,
is authoritative. A green core test suite proves the implemented routes work;
it does not make unimplemented web-client workflows available.

Until `check_web_contracts.py --require-complete` passes after approved
community exclusions, this service is runnable but **not contract-complete for
the entire open application client**. The remaining routes must be ported with
their domain models and tenant/privacy tests, not replaced by success stubs.

## Security properties

- Opaque credentials are stored only as SHA-256 digests; passwords use scrypt.
- Every private domain query includes the authenticated organization ID.
- Public tokens carry no identity or infrastructure information.
- Interview scope is fingerprinted at publication and checked again for every
  public turn. Any material edit revokes the public token.
- Spoken audio is bounded, passed in memory to an operator-selected processor,
  and never persisted by this service.
- External processors are disabled by default, redirects are not followed, and
  production configuration requires HTTPS endpoints.
- Migrations must be run explicitly in production; automatic table creation is
  rejected there.

Security and data-protection suitability still depend on the operator's actual
deployment, processor agreements, retention operations, access controls and
jurisdiction. This repository cannot by itself certify regulatory compliance.
