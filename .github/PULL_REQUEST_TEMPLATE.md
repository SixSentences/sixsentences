## Summary

<!-- What problem does this solve, and why is this the smallest useful change? -->

## Behavior and compatibility

<!-- Describe observable behavior, public API/schema changes, and migration needs. -->

## Validation

<!-- List the exact checks run and any intentionally omitted checks. -->

- [ ] `uv run ruff check .`
- [ ] `uv run mypy src/sixsentences`
- [ ] `uv run pytest -q`
- [ ] `uv build`

## Data, network, and security review

<!-- Note new dependencies, network calls, external data terms, or security/privacy effects. -->

- [ ] No secrets, production artifacts, personal data, or non-redistributable research content are included.
- [ ] New network behavior and data-license implications are documented, or this change adds none.
- [ ] New dependencies are justified and exactly pinned, or this change adds none.

## Checklist

- [ ] The change stays within the portable community-engine scope.
- [ ] Tests cover changed behavior, or the omission is explained above.
- [ ] Public behavior and limitations are documented.
- [ ] Every commit carries my DCO `Signed-off-by` trailer.
- [ ] I have read and will follow the Code of Conduct.
