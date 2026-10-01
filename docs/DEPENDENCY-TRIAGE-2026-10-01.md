# Dependency triage — 2026-10-01

This is a point-in-time review, not merge permission or a substitute for checks
on the current base commit. No dependency PR was bulk-merged.

## Security patch included in the stabilization branch

- Web Next.js `16.3.4` → `16.3.8`, with its matching locked Next packages.
  `npm audit --omit=dev` reports zero runtime findings after this update. Keep
  the full web test/type/build gates before release; audit results can change.
- Neither public Python lockfile contains `urllib3` or `requests`. A dependency
  issue in a separately deployed service is not automatically present here.
- Remaining development-tool findings are not evidence of a vulnerable runtime,
  but still need scoped updates and CI review. Do not run a broad force-fix.

## Open pull requests reviewed

| PR | Scope | Next action |
| --- | --- | --- |
| #175 | `ip-address` patch, web lockfile only | Review against the new Next lock and rerun; do not discard the security patch. |
| #172 | Grouped web minor/patch changes | Rebase/resolve after stabilization, then full web tests and runtime audit. |
| #157, #158 | Engine Ruff / build backend patches | Small separate reviews with package and formatting checks. |
| #159–#163 | API runtime/development changes | Preserve the strict export audit and refresh its manifest after all source-boundary checks pass. #163's failed log explicitly reports a stale `pyproject.toml` manifest digest, not a tested SQLAlchemy incompatibility. Check each PR rather than assuming the same cause. |
| #173 | Pinned GitHub Action updates | Diagnose the three failed engine-test checks; workflow changes also require matching automation-contract tests. |
| #164 | Sparkle update | Keep blocked on both native toolchain checks; do not bypass source-only or update-signature boundaries. |
| #166 | Motion major update | Separate behavior/accessibility review, despite currently green checks; do not fold a major animation change into recovery hardening. |

Snapshot: twelve open dependency PRs; six had failed checks and six had no failed
checks in their reported rollups. A past green rollup does not prove compatibility
with today's head or required-check completion.
