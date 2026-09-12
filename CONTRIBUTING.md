# Contributing to SixSentences

Thank you for improving open, inspectable research software. Focused bug fixes,
tests, documentation, accessibility improvements, and well-bounded features are
welcome across the engine, API, web application, and self-hosting stack.

All participation follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report
vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Choose the right starting point

Small fixes can go directly to a pull request. Start with an issue or discussion
for any new public API, database migration, serialized format, dependency,
external service, research-method claim, or substantial UI behavior.

The community edition includes the complete research application and its
reference deployment. These remain out of scope:

- payments, plans, subscriptions, checkout, and hosted-service administration;
- the separately deployed marketing website and commercial operations;
- production credentials, configuration, telemetry, backups, or incident data;
- customer or participant data and non-redistributable research material; and
- provider accounts or credentials operated by SixSentences.

Provider-neutral interfaces, self-hosting controls, and operator-supplied
integrations are in scope when their privacy, cost, failure, and data-egress
boundaries are explicit.

## Workflow

`main` contains reviewed release-ready history; `develop` is the integration
branch. Create a short-lived branch such as `fix/interview-retention` or
`feat/ris-import` from `develop` and open the pull request back to `develop`.
Release pull requests promote `develop` to `main`. Urgent fixes branch from
`main` and are merged back into `develop` after release.

Keep each pull request focused. Use Conventional Commit subjects where
practical: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, or `chore:`. Do not
mix dependency refreshes or unrelated refactoring into a behavioral change.

## Local setup and checks

Use the committed lockfiles. Supported runtimes are Python 3.12–3.14, Node.js
22.9 or newer, `uv`, npm, and Docker Compose 2.33.1 or newer.

### Research engine

```console
uv sync --frozen --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/sixsentences
uv run pytest -q
uv build
```

### Application API and worker

```console
uv sync --project services/api --frozen --all-groups
uv run --project services/api ruff check services/api/src services/api/scripts services/api/tests
uv run --project services/api mypy services/api/src
uv run --project services/api pytest services/api/tests
uv run --project services/api python services/api/scripts/audit_boundary.py
uv run --project services/api python services/api/scripts/check_web_contracts.py --require-complete
```

### Web application

```console
cd apps/web
npm ci
npm run typecheck
npm run test:security
npm run test:ui
npm run build
```

### Self-hosting definition

```console
bash deploy/community/test.sh
bash deploy/community/init-env.sh --local --output /tmp/sixsentences-contribution.env
bash deploy/community/preflight.sh /tmp/sixsentences-contribution.env
```

Use synthetic data only. Never add `.env` files, keys, tokens, database dumps,
logs, user exports, production screenshots, private prompts, full-text articles,
or other material whose redistribution rights are unclear.

## Engineering standards

- Keep code, comments, commits, and public documentation in English.
- Add Python type hints and docstrings to public classes and functions.
- Preserve strict TypeScript, accessible names, keyboard behavior, and clear
  loading, error, and empty states.
- Set deterministic seeds for tests that use randomness; use `42` by default.
- Document important tensor dimensions, for example `# x: (B, S, D)`.
- Keep network calls, cost limits, data retention, and provider egress explicit.
- Surface methodological assumptions and limits. Do not imply that software
  establishes completeness, causality, compliance, or scientific validity.
- Add a migration and rollback note for persistent-schema changes.

## Pull-request evidence

A pull request should state:

- the problem and chosen behavior;
- affected components and public contracts;
- exact validation commands and results;
- migration, rollback, or compatibility impact;
- privacy, security, accessibility, data-license, and network implications; and
- screenshots or a short recording for material UI changes, using synthetic
  content only.

Passing automation is required but does not replace review. Changes affecting
authentication, tenancy, participant data, external providers, uploads,
retention, deletion, or release automation receive an explicit security and
privacy review.

## DCO and contributor agreement

SixSentences requires both the [Developer Certificate of Origin 1.1](DCO) and
the repository's [Contributor License Agreement](CLA.md). Sign every commit
with your own public Git identity:

```console
git commit -s -m "fix: describe the change"
```

The resulting `Signed-off-by` trailer certifies that you may submit the work
under the project license. Every author and declared co-author must add their
own matching sign-off; maintainers and automation must not invent one for
someone else. GitHub's private `noreply` address is acceptable.

The pull-request author must also read `CLA.md` and post its exact acceptance
sentence as a standalone PR comment. A repository-local workflow verifies the
public record; no external CLA application or separately stored token is used.

Review and merge authority is described in [GOVERNANCE.md](GOVERNANCE.md).
