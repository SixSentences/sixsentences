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

Snapshot: twelve open dependency PRs; seven had failed checks and five had no failed
checks in their reported rollups. A past green rollup does not prove compatibility
with today's head or required-check completion.

## Alpha.4 release follow-up: pypdf runtime advisories

A later 2026-10-01 GitHub advisory refresh added six High alerts: three issues
duplicated across the API manifest and lockfile. The maintainer disclosures
predate this refresh; these are not six distinct or newly discovered bugs.
All affect pypdf versions before 6.19.0:

- [GHSA-w23x-9jrw-r45c](https://github.com/py-pdf/pypdf/security/advisories/GHSA-w23x-9jrw-r45c):
  excessive memory use in alphabetical page labels.
- [GHSA-php9-fj8v-98fj](https://github.com/py-pdf/pypdf/security/advisories/GHSA-php9-fj8v-98fj):
  excessive CPU use in appearance streams/form-field flattening.
- [GHSA-v247-6f48-mgcj](https://github.com/py-pdf/pypdf/security/advisories/GHSA-v247-6f48-mgcj):
  excessive CPU use in dictionary-based embedded-file access.

The API installs pypdf in its production image and parses untrusted uploaded or
linked PDFs. Source review found no production calls to page labels, PdfWriter
form flattening, or attachment dictionaries; it did not demonstrate a reachable
path to these specific vulnerable features. This is not a claim that the parser
is risk-free. Alpha.4 pins the
[patched 6.19.0 release](https://github.com/py-pdf/pypdf/releases/tag/6.19.0).

Only pypdf's package records and direct requirement change in the canonical
API `uv.lock`; all other 59 package records remain unchanged. The BSD-3-Clause
license is unchanged. The legacy direct-pin `services/api/requirements.lock`
is not used by the canonical Docker/uv installation; only its pypdf and engine
pins are synchronized here. Other historical snapshot drift is not silently
treated as canonical or broadly updated in this fix.

Local validation passed 136 existing PDF, acquisition, capture and security
tests, frozen environment synchronization, offline lock checking, and the
422-file API export audit. The web runtime-only npm audit still reports zero
findings at this snapshot; eleven other open alerts concern development tools.
Full new-head CI and exact-release image scans remain mandatory. These scoped
checks do not certify the whole system free of vulnerabilities or update an
already-running hosted deployment.
