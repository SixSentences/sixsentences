## Summary

<!-- What problem does this solve, and why is this the smallest useful change? -->

## Behavior and compatibility

<!-- Describe observable behavior, public API/schema changes, and migration needs. -->

## Validation

<!-- List the exact checks run and any intentionally omitted checks. -->

### Python engine (when affected)

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy src/sixsentences`
- [ ] `uv run pytest -q`
- [ ] `uv build`

### Web application (when affected)

- [ ] `cd apps/web && npm ci`
- [ ] `cd apps/web && npm run typecheck`
- [ ] `cd apps/web && node --test tests/*.test.mjs`
- [ ] `cd apps/web && npm run build`

## Data, network, and security review

<!-- Note new dependencies, network calls, external data terms, or security/privacy effects. -->

- [ ] No secrets, hosted-production artifacts, personal data, customer data, or non-redistributable research content are included.
- [ ] New network behavior and data-license implications are documented, or this change adds none.
- [ ] New dependencies and bundled assets are justified, locked, and license-compatible, or this change adds none.
- [ ] Changes to authentication, tenancy, provider access, uploads, or browser storage include a threat-boundary review, or this change affects none of them.

## Checklist

- [ ] The change belongs in the community source tree; hosted billing, operations, customer configuration, and production evidence remain excluded.
- [ ] Tests cover changed behavior, or the omission is explained above.
- [ ] Public behavior and limitations are documented.
- [ ] User-visible changes are recorded under `Unreleased` in `CHANGELOG.md`,
      or the omission is explained above.
- [ ] Every commit carries my DCO `Signed-off-by` trailer.
- [ ] I have read and will follow the Code of Conduct.
