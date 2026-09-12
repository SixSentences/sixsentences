# Contributing

Thank you for helping improve SixSentences. This project welcomes bug fixes,
tests, documentation, and focused proposals that fit the portable open-source
toolkit's scope.

## Before you start

For a small, well-bounded fix, open a pull request directly. For a new public
API, file format, dependency, or substantial behavior change, open an issue
first so the design and maintenance cost can be discussed before implementation.

Please do not submit hosted-service code, billing, authentication, production
deployment, private provider configuration, or website assets here. Those are
outside this repository's scope.

## Development setup

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

Before opening a pull request, run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/sixsentences
uv run pytest -q
uv build
```

Keep code, comments, commit messages, and public documentation in English.
Python changes should use type hints. Public functions and classes should have
docstrings. Tests that depend on randomness must set `random`, NumPy, and
PyTorch seeds as applicable; use `42` unless the test requires another fixed
value. Tensor code should document important dimensions, for example
`# x: (B, S, D)`.

## Pull requests

Keep a pull request focused on one concern. Include:

- the problem and the chosen behavior;
- user-visible or compatibility effects;
- tests for the change, or a short explanation when tests are not applicable;
- documentation updates for public behavior; and
- any privacy, data-license, network, or dependency implications.

Use Conventional Commit subjects where practical, such as `fix:`, `feat:`,
`docs:`, `refactor:`, or `test:`. Do not add generated corpora, scholarly full
text, credentials, `.env` files, production logs, or personal data. Fixtures
must be synthetic or clearly redistributable and should be as small as possible.

Maintainers may ask that a large pull request be split or that a proposal be
narrowed to preserve the toolkit's portable boundary.

## Developer Certificate of Origin

The project uses the [Developer Certificate of Origin 1.1](DCO), not a separate
contributor license agreement. The root file is an unchanged copy of
the text published at
[developercertificate.org](https://developercertificate.org/). By signing off
a commit, you certify that you have the right to submit the contribution under
the project's license and agree that it is public.

This choice is deliberate: Apache-2.0 plus per-commit DCO certification is the
project's inbound contribution mechanism. A CLA would add a separate legal and
personal-data process that the project does not currently need. Adopting one in
the future would require a public governance proposal and appropriate legal
review; it will not be imposed retroactively without that process.

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

Reviews assess correctness, tests, type safety, compatibility, research-method
claims, data provenance, and scope. Passing automation is necessary but not a
guarantee of acceptance. Maintainers make final merge decisions under
[GOVERNANCE.md](GOVERNANCE.md).

All participants must follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security concerns according to [SECURITY.md](SECURITY.md).
