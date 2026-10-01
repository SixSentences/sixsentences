# Releasing SixSentences

Releases come from immutable tags on reviewed `main` commits. GitHub Actions
validates the complete source tree—including the macOS Companion on a pinned
Xcode toolchain—prepares Python-engine distributions and container evidence in
one orchestrated workflow. It uploads and verifies the complete draft before
publishing an immutable GitHub prerelease. It does not publish to PyPI or npm,
does not build a distributable macOS binary, and uses no long-lived registry or
Apple credential.

## Authority and protection

Only a release maintainer may create `v*` tags or publish a release. Releases
are cut from reviewed `main` commits, with `main` protected by the checks
described in [GOVERNANCE.md](../GOVERNANCE.md). Protect
release tags against force-push, update, and deletion, and restrict tag creation
to release maintainers.

A tag is immutable even when automation fails. Fix failures through a reviewed
pull request and use a new prerelease version; never move or reuse the tag.

[GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
lock release assets after publication. Attach every asset while the release is
a draft, verify the remote bytes, and publish last. Do not disable immutability
or try to append image evidence after publication.

## Prepare the release pull request

1. Set the PEP 440 engine version in `pyproject.toml` and `uv.lock`.
2. Set the same PEP 440 version in `services/api/pyproject.toml`, pin its
   `sixsentences-engine` dependency with `==` to that version, and regenerate
   `services/api/uv.lock`.
3. Set the equivalent public version in
   `apps/web/package.json`, `apps/web/package-lock.json`,
   `apps/browser-extension/package.json`,
   `apps/browser-extension/package-lock.json`, and `CITATION.cff`.
4. Set `CITATION.cff`'s UTC date to the release commit date.
5. Move entries from `Unreleased` to a dated `CHANGELOG.md` section.
6. Add `docs/releases/<tag>.md` and update user-facing deployment examples.
7. Confirm the Companion's independent client/build version remains intentional,
   that `Info.plist` exactly matches `CompanionRelease.swift`, and that its exact
   `Package.resolved` graph is unchanged after resolution.
8. Complete every item in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

For this release, `0.2.0a4` in Python metadata maps to public version
`0.2.0-alpha.4` and tag `v0.2.0-alpha.4`. Beta and release-candidate suffixes
map to `-beta.N` and `-rc.N`.

Alpha.4 corrects publication order and updates the API's pypdf runtime dependency
from 6.18.1 to 6.19.0 for security fixes. Application logic, migrations and other
canonical third-party dependency versions are unchanged; see
[dependency triage](DEPENDENCY-TRIAGE-2026-10-01.md).
Alpha.3's source/Python release was published on
2026-10-01; its image builds, scans, isolated drill and metadata validation also
passed. The later evidence upload was rejected with HTTP 422 because the release
was already immutable. Those image receipts are absent from alpha.3's release;
its valid source/Python artifacts and completed test results remain unchanged.
Do not replace earlier images, move old tags or retrofit old release notes.
Alpha.4 must complete its own unified workflow and final artifact verification
before being described as fully published. Publication recovery is tracked in #185.

The release workflow verifies the tag as Git data using the release verifier and
maintainer keys from protected `main`. Only after signature, syntax, ancestry,
and metadata validation do jobs check out the exact verified commit SHA. The
tag must be contained in `origin/main`, match all canonical metadata, and have a
release note and changelog entry for the tagged commit's UTC date.
Metadata validation fails closed on missing, malformed, duplicated, or drifting
Python, npm, citation, and Companion version declarations.

## Create the tag and stage the reviewed preview

After the release pull request is merged and all required checks are green:

```console
git switch main
git pull --ff-only
git tag -s v0.2.0-alpha.4 -m "SixSentences v0.2.0-alpha.4"
git push origin v0.2.0-alpha.4
gh release create v0.2.0-alpha.4 \
  /absolute/path/to/sixsentences-overview.gif \
  --repo SixSentences/sixsentences \
  --verify-tag \
  --draft \
  --prerelease \
  --latest=false \
  --title "SixSentences v0.2.0-alpha.4 · release candidate" \
  --notes "Release automation will replace these draft notes after every gate passes."
gh workflow run release.yml \
  --repo SixSentences/sixsentences \
  --ref main \
  -f tag=v0.2.0-alpha.4
```

Run the three publication commands together in one supervised release session.
For alpha.4, stage an unchanged copy of the checked-in
`docs/assets/sixsentences-thesis-overview.gif` under the release filename
`sixsentences-overview.gif`; its bytes must match
`docs/assets/sixsentences-overview.sha256`. The owner-supplied thesis-film preview
entered the public tree in reviewed PR #171; this release does not import new
media or reuse the older alpha.1 preview checksum.
The explicit workflow dispatch occurs only after the signed tag and draft both
exist, so there is no tag-push/draft-creation race. The workflow verifies the
tag before executing its source and fails closed unless the draft contains only
`sixsentences-overview.gif` with the checksum committed at that tag. A failed
workflow never publishes the draft. A draft with unexpected or partially
uploaded artifacts is not clobbered or automatically cleaned. Investigate the
failure and use a new reviewed version when existing images or assets prevent
a clean run; never move or reuse a release tag.

The workflow rebuilds and retests from the tag. Python wheels are built from the
source distribution and installed in isolation. API and web sources are tested
from locked environments. Fresh public Alembic migrations, JSON/sequence behavior
and concurrent queue claims run explicitly against an empty, pinned, disposable
runner-local PostgreSQL service in both pull-request and tag CI; the regular
SQLite suite's skip is not accepted as that evidence. This does not advertise
the private legacy SQLite-to-PostgreSQL cutover utility as a community feature.
Browser-extension contracts and an origin-bound
unpacked build are validated without a store identity. The macOS Companion's
boundary, exact dependency resolution, tests, and release compilation run on
pinned Xcode 26.1.1 without signing or notarization credentials. The exact tag
is scanned again for current and historical secrets, critical dependency
vulnerabilities, and deployment misconfiguration. The self-hosting definition
and both container builds are validated from the tag. Pinned Trivy image scans
of both local candidate images reject fixable critical vulnerabilities before
runtime acceptance and before publishing the source release; pull-request CI
applies the same image policy. A provider-free rehearsal
starts the published `v0.2.0-alpha.1` API in isolated volumes, seeds a synthetic
workspace, upgrades it, verifies saved notes/files after backup/restore, and
separately starts the candidate against an empty database. Its sanitized
image/revision receipt is a required artifact. This is an API/Compose runtime
test, not a browser interaction test or proof of research-output quality.
For alpha.4, this required upgrade/rollback pair is the pinned alpha.1 baseline
and the alpha.4 candidate, with the compatibility bootstrap below. It is not a
dedicated exact-image alpha.3-to-alpha.4 upgrade or rollback rehearsal.
The original alpha.1 PostgreSQL installer has an invalid `BOOLEAN DEFAULT 0`
in the baseline migration; the real PostgreSQL regression confirms SQLSTATE
`42804`. The candidate changes exactly that literal to SQLAlchemy `sa.false()`.
Before running the unchanged pinned alpha.1 API, the rehearsal uses the
candidate migrator to apply only the original `20260912_0001` revision to an
empty database. The original and corrected migration SHA-256 hashes, source
revision, immutable old API reference and this bootstrap mode are committed in
`deploy/community/alpha1-baseline-compatibility.json` and copied into the receipt.
Reconstructing the original bytes by reverting only that literal must match
the original hash; any other migration change stops the drill. There is no
`stamp`, metadata `create_all`, replacement old image or waived migration.
This proves the corrected original-schema upgrade path, not successful fresh
installation with the unmodified alpha.1 installer. The candidate separately
has to migrate a completely empty database through its full migration chain.
The legacy API also requires a corpus for readiness. Only its baseline receives
a real, verified one-record synthetic Parquet corpus, recorded as
`legacy_corpus_fixture` and `synthetic_corpus_bootstrap: true`. The fresh alpha.4
run must instead prove `fresh_candidate_without_corpus: true`: the optional
literature corpus does not block the wider workspace, while database, storage,
queue and live-worker health remain mandatory. Corpus searches still fail
closed without an imported corpus. API and worker resume together after backup
or authenticated erasure replay; the public proxy waits for healthy services.
Journal verification runs as the normal API user, with host-owned candidate
bytes streamed through stdin into private temporary files. No extra container
capability or root recovery process is needed. The isolated drill also requires
an unprivileged UID-0 read of the synthetic private journal to fail before the
real restore succeeds as its owning user. Restore diagnostics expose only fixed
phase labels; failed writer shutdown prevents journal selection and state replacement.
The drill additionally creates a second synthetic owner before backup, deletes
that account through the authenticated API afterwards, and requires its user,
workspace, sessions, notes and dataset files to remain absent after restore.
The exact authenticated deletion-journal signature must survive the restore.
Only the successful runtime receipt proves this scenario; source/unit checks
alone do not. A separate rollback phase restores the snapshot taken before the
upgrade under the pinned alpha.1 API. It verifies the original migration
revision, surviving owner and files, and replays the newer authenticated
erasure journal so the subsequently deleted owner remains absent. The receipt
must explicitly record `preupgrade_snapshot_rollback: true`. This tests only
snapshot rollback for that supported version pair, not a schema downgrade or
preservation of writes made after the snapshot. Broader recovery work remains
tracked in #29.

The preview is checked by a separate job before the environment gate, so a wrong
or missing preview fails the run before a maintainer is asked to approve
anything. That job carries `contents: write` for one reason: GitHub shows draft
releases only to callers with push access, so a read-only token cannot see the
draft it is meant to check and answers "release not found". It reads and never
mutates, and a test asserts that it calls no release-mutating command.

After every source build, test, attestation, self-hosting, security and preview
dependency passes, `release.yml` calls the protected reusable image-preparation
workflow. Its exact-digest scans, drill and evidence checks must pass before it
exports a sanitized ten-file artifact to the same workflow run. It does not
upload to the GitHub release or publish the draft.

The final publisher then pauses at the protected `community-release` environment.
A release maintainer must inspect the completed evidence and approve publication.
That job downloads only this run's engine and image artifacts, revalidates them,
and requires the still-editable draft to contain only the checksum-matched GIF.
It uploads the fourteen remaining files without overwriting anything, downloads
the resulting assets again, and requires both exact inventory and byte/checksum
equality. Only after those checks pass may it publish the draft as a prerelease.

The exact fifteen uploaded release assets are:

| Group | Count | Files |
| --- | ---: | --- |
| Reviewed preview | 1 | `sixsentences-overview.gif` |
| Python engine | 4 | Versioned wheel, sdist, CycloneDX SBOM, `SHA256SUMS` |
| Image evidence | 10 | `IMAGE_DIGESTS`, `images.json`, API/web SPDX SBOMs, API/web provenance exports, API/web OCI identity records, `runtime-rehearsal.json`, `IMAGE_SHA256SUMS` |

GitHub's automatically generated source archives are not uploaded assets in this
inventory. Release notes are the release body, not a sixteenth asset. A missing,
extra or changed file leaves the draft unpublished; no post-publication upload
step is permitted.

### Repository settings the release depends on

Two settings live outside this repository and will silently stop a release if
they drift. Check them before tagging, because both fail in ways that look like
a broken workflow.

The `community-release` environment's **deployment branch policy must allow the
`main` branch**. The release workflow refuses to run unless it is dispatched
from `refs/heads/main`, so the run's ref is always `refs/heads/main`; a policy
naming only `v*` tag patterns can never match, and the publish job then fails
after two seconds with no steps and no log. The binding of a release to a signed
tag is not enforced here — it is enforced in the `validate` job, which verifies
the tag signature against `.github/release-maintainers.allowed_signers` and
requires the tagged commit to be contained in `main`.

The environment's **required reviewer** must be a current release maintainer.
This is the approval the checklist means.

The GitHub release remains a prerelease while the project is in alpha. The
product-overview GIF is a presentation asset, not an executable artifact;
review it for personal, customer, participant, and non-redistributable content,
render it without audio, and verify its committed checksum before staging it as
`sixsentences-overview.gif`.

The tagged Companion directory is source, not an official native artifact.
Developer ID signing, Apple notarization, Sparkle feed/key management, appcast
publication, update provenance, and ZIP/DMG/App Store distribution require a
separate reviewed pipeline before any binary can be advertised as supported.

## Prepare container evidence inside the release workflow

Do not dispatch `publish-images.yml` after publication. It is a reusable workflow
called by `release.yml` only after all prerequisite gates pass. It independently
verifies the SSH-signed tag, main ancestry, canonical metadata, the caller's
verified source revision, and the still-unpublished draft. Registry writes
require the protected `community-release` approval, separately from the final
GitHub publisher's approval but within the same orchestrated run.

The workflow checks out the verified commit and publishes Linux amd64 images
`community-api:<tag>` and `community-web:<tag>-localhost`. It refuses to replace
either existing tag, and registry errors other than confirmed absence stop it.
No `latest` tag or untested ARM64 image is created. Only
localhost is reusable because the web client bakes its public origin at build
time; TLS operators must build their own.

BuildKit records SBOM and provenance in the registry. The workflow retrieves
them by digest, validates nonempty SPDX package inventories and build
provenance, and checks the pulled images' OCI revision/version against the
verified source and tag. It then runs the startup, upgrade and restore rehearsal
against those exact published digests, not merely the earlier candidate builds.
Pinned Trivy image scans also check those pulled API and web digests, including
their system packages. Fixable `CRITICAL` vulnerabilities fail publication of
the evidence, matching the current dependency-gate policy; this is not a claim
that lesser-severity or currently unfixable findings are absent. Scanner output
remains in the workflow log and does not replace the full SPDX inventory.
The provenance validator accepts exact BuildKit schema/build-type pairs:
SLSA v1 uses `buildDefinition` and `runDetails`, with build type
`https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md`;
the legacy SLSA v0.2 form uses `https://mobyproject.org/buildkit@v1` with its
top-level builder, invocation, metadata and materials. Similar URI prefixes or
mixed schemas are not accepted. This format support does not weaken the exact
OCI revision/version, build-digest or runtime-receipt bindings and does not turn
a provenance VCS hint into independent source verification.
Only after those checks succeed does it export `IMAGE_DIGESTS`, `images.json`,
the two SBOMs, two provenance exports, two OCI identity records,
`runtime-rehearsal.json` and `IMAGE_SHA256SUMS` as the ten-file same-run artifact.
Raw build metadata and diagnostic files are not part of that artifact. The final
publisher verifies and attaches this evidence together with the engine assets
while the release is still a draft; this workflow never adds files to a published
release. Existing image references and release files are never overwritten.
Keep the engine's `SHA256SUMS` separate. Verify image files with
`sha256sum --check IMAGE_SHA256SUMS`, then use the `@sha256:` references from
`IMAGE_DIGESTS`. BuildKit provenance records build inputs; it is not independent
audit or a promise of byte-identical reproducibility. System package mirrors
and build/scanner tooling may change. Checksums are not signatures.

If publication partially succeeds, do not replace or move the tag to retry it.
Inspect the digest evidence and prepare a new reviewed prerelease. No pipeline
step deletes previously published packages or release files.

New packages are private on first publish. Make each one public once, in the
organization's package settings, or an operator's `docker pull` fails with an
authentication error rather than a missing-image error.

## Verify published artifacts

Download release files into an empty directory:

```console
gh release download v0.2.0-alpha.4 --repo SixSentences/sixsentences
sha256sum --check SHA256SUMS
sha256sum --check IMAGE_SHA256SUMS
gh attestation verify sixsentences_engine-0.2.0a4-py3-none-any.whl --repo SixSentences/sixsentences
gh attestation verify sixsentences_engine-0.2.0a4.tar.gz --repo SixSentences/sixsentences
```

Build both images from the tag, record their local digests, start a fresh local
stack, exercise onboarding and one synthetic workflow, inspect health, and run
a backup/isolated restore. Confirm the release page is marked as a prerelease,
the preview loads, and all downloadable assets are expected.
