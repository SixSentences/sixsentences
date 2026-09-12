# Contributing

Thank you for helping improve SixSentences. This repository welcomes focused
contributions to both open surfaces:

- the typed Python research engine and CLI under `src/sixsentences`; and
- the sanitized Next.js research-workspace client under `apps/web`.

## Before you start

For a small, well-bounded fix, open a pull request directly. Open an issue first
for a new public API, serialized format, dependency, network integration,
research-method claim, or substantial interface behavior so its compatibility
and maintenance cost can be discussed.

The following remain outside this repository's contribution scope:

- the marketing and landing website;
- the hosted SaaS backend and private provider orchestration;
- billing, pricing, checkout, subscriptions, administration, and operator code;
- production deployment, monitoring, backup, analytics, or private
  configuration;
- the browser extension and macOS companion until they have their own reviewed
  source-release boundary; and
- customer data, proprietary corpora, or scholarly full text without explicit
  redistribution rights.

Client-side contracts for authentication and research services are in scope;
the corresponding hosted implementations are not. Do not present the Python
package as a compatible server for the web client.

## Development setup

### Python engine

Requirements:

- Python 3.12 or newer;
- `uv`; and
- Git.

```bash
git clone https://github.com/SixSentences/sixsentences.git
cd sixsentences
uv sync --frozen --group dev
uv run pytest -q
```

Before opening a Python pull request, run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/sixsentences
uv run pytest -q
uv build
```

### Web client

Requirements:

- Node.js 22.9 or newer;
- npm; and
- a compatible API only when exercising API-backed flows.

```bash
cd apps/web
npm ci
cp .env.example .env.local
npm run dev
```

Before opening a web pull request, run:

```bash
npm run typecheck
npm run test:security
npm run test:ui
npm run build
```

Use the committed lockfiles. Do not update unrelated dependencies as part of a
feature or bug fix.

## Code and research standards

Keep code, comments, commit messages, and public documentation in English.

Python changes should use type hints. Public functions and classes should have
docstrings. Tests that depend on randomness must set `random`, NumPy, and
PyTorch seeds as applicable; use `42` unless a test requires another fixed
value. Tensor code should document important dimensions, for example
`# x: (B, S, D)`.

Web changes should preserve strict TypeScript, accessible names and keyboard
behavior, explicit loading/error/empty states, and the established responsive
layouts. Keep API calls in the typed client boundary rather than scattering
ad-hoc requests across components. A browser-visible `NEXT_PUBLIC_*` variable
must never contain a secret.

Research-facing changes must make transformations, assumptions, exclusions,
and method limits visible. Tests and examples must not overstate completeness,
compliance, causality, model reliability, or scientific validity.

## Pull requests

Keep a pull request focused on one concern. Include:

- the problem and the chosen behavior;
- user-visible, API-contract, serialization, or compatibility effects;
- tests for the change, or a short explanation when tests are not applicable;
- documentation updates for public behavior;
- any privacy, accessibility, data-license, network, or dependency implications;
  and
- screenshots or a short recording for material interface changes, with no
  personal, production, or copyrighted research data.

Use Conventional Commit subjects where practical, such as `fix:`, `feat:`,
`docs:`, `refactor:`, or `test:`. Do not add generated corpora, scholarly
full text, credentials, `.env` files, production logs, user exports, or
personal data. Fixtures must be synthetic or clearly redistributable and as
small as practical.

Maintainers may ask that a large pull request be split or narrowed to preserve
the open-source boundary.

## Developer Certificate of Origin

The project uses the [Developer Certificate of Origin 1.1](DCO), not a separate
contributor license agreement. The root [`DCO`](DCO) file is an unchanged copy
of the text published at
[developercertificate.org](https://developercertificate.org/). By signing off a
commit, you certify that you have the right to submit the contribution under
the project's license and agree that it is public.

Apache-2.0 plus per-commit DCO certification is the project's inbound
contribution mechanism. A CLA would add a separate legal and personal-data
process that the project does not currently need. Adopting one later would
require a public governance proposal and appropriate legal review; it would not
be imposed retroactively without that process.

Sign every commit with Git's `-s` flag:

```bash
git commit -s -m "fix: describe the change"
```

This adds a trailer like:

```text
Signed-off-by: Your Name <your-email@example.com>
```

Use your own identity and an email address you are comfortable making public in
Git history. GitHub's private `noreply` address is acceptable. Each human
contributor must add their own sign-off. Automation and other people must not
invent or append that personal certification on a contributor's behalf.

## Review expectations

Reviews assess correctness, tests, typing, compatibility, accessibility,
research-method claims, data provenance, security, dependency licensing, and
scope. Passing automation is necessary but does not guarantee acceptance.
Maintainers make final merge decisions under [GOVERNANCE.md](GOVERNANCE.md).

All participants must follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security concerns according to [SECURITY.md](SECURITY.md).
