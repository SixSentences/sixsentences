# Releasing SixSentences

Releases come from immutable tags on reviewed `main` commits. GitHub Actions
validates the complete source tree—including the macOS Companion on a pinned
Xcode toolchain—publishes Python-engine distributions, records checksums and
available SBOM/provenance attestations, and creates a GitHub prerelease. It does
not publish to PyPI or npm, does not build a distributable macOS binary, and
uses no long-lived registry or Apple credential.

## Authority and protection

Only a release maintainer may create `v*` tags or publish a release. Promote a
reviewed release commit from `develop` to `main`, with both branches protected
by the checks described in [GOVERNANCE.md](../GOVERNANCE.md). Protect
release tags against force-push, update, and deletion, and restrict tag creation
to release maintainers.

A tag is immutable even when automation fails. Fix failures through a reviewed
pull request and use a new prerelease version; never move or reuse the tag.

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

For this release, `0.2.0a1` in Python metadata maps to public version
`0.2.0-alpha.1` and tag `v0.2.0-alpha.1`. Beta and release-candidate suffixes
map to `-beta.N` and `-rc.N`.

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
git tag -s v0.2.0-alpha.1 -m "SixSentences v0.2.0-alpha.1"
git push origin v0.2.0-alpha.1
gh release create v0.2.0-alpha.1 \
  /absolute/path/to/sixsentences-overview.gif \
  --repo SixSentences/sixsentences \
  --verify-tag \
  --draft \
  --prerelease \
  --latest=false \
  --title "SixSentences v0.2.0-alpha.1 · release candidate" \
  --notes "Release automation will replace these draft notes after every gate passes."
gh workflow run release.yml \
  --repo SixSentences/sixsentences \
  --ref main \
  -f tag=v0.2.0-alpha.1
```

Run the three publication commands together in one supervised release session.
The explicit workflow dispatch occurs only after the signed tag and draft both
exist, so there is no tag-push/draft-creation race. The workflow verifies the
tag before executing its source and fails closed unless the draft contains only
`sixsentences-overview.gif` with the checksum committed at that tag. A failed
workflow never publishes the draft. Correct the draft and rerun the workflow
without moving or reusing the tag.

The workflow rebuilds and retests from the tag. Python wheels are built from the
source distribution and installed in isolation. API and web sources are tested
from locked environments. Browser-extension contracts and an origin-bound
unpacked build are validated without a store identity. The macOS Companion's
boundary, exact dependency resolution, tests, and release compilation run on
pinned Xcode 26.1.1 without signing or notarization credentials. The exact tag
is scanned again for current and historical secrets, critical dependency
vulnerabilities, and deployment misconfiguration. The self-hosting definition
and both container builds are validated from the tag. Images remain
deployment-built during this alpha; in particular, public origins and legal
versions are compile-time browser configuration, so a generic web image would
be misleading.

The preview is checked by a separate job before the environment gate, so a wrong
or missing preview fails the run before a maintainer is asked to approve
anything. That job carries `contents: write` for one reason: GitHub shows draft
releases only to callers with push access, so a read-only token cannot see the
draft it is meant to check and answers "release not found". It reads and never
mutates, and a test asserts that it calls no release-mutating command.

After every build, test, attestation, self-hosting, and preview dependency
passes, the publish job pauses at the protected `community-release` environment.
A release maintainer must inspect the workflow evidence and explicitly approve
that job. The write-scoped job then rechecks the draft and checksum immediately
before publication.

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

## Publish the container images

`publish-images.yml` declares `on: release: published`, and that trigger will
not fire for a release this automation publishes: GitHub deliberately does not
start a workflow from an event caused by `GITHUB_TOKEN`, which is what the
publish job uses. Dispatch it explicitly after the release is verified:

```console
gh workflow run publish-images.yml \
  --repo SixSentences/sixsentences \
  --ref main \
  -f tag=v0.2.0-alpha.1
```

The workflow checks out the exact tag, refuses anything that is not a version
tag, and publishes `community-api:<tag>` and `community-web:<tag>-localhost`.
Only the localhost origin is publishable as a reusable web image, because the
web client bakes its public origin at build time.

New packages are private on first publish. Make each one public once, in the
organization's package settings, or an operator's `docker pull` fails with an
authentication error rather than a missing-image error.

## Verify published artifacts

Download release files into an empty directory:

```console
gh release download v0.2.0-alpha.1 --repo SixSentences/sixsentences
sha256sum --check SHA256SUMS
gh attestation verify sixsentences_engine-0.2.0a1-py3-none-any.whl --repo SixSentences/sixsentences
gh attestation verify sixsentences_engine-0.2.0a1.tar.gz --repo SixSentences/sixsentences
```

Build both images from the tag, record their local digests, start a fresh local
stack, exercise onboarding and one synthetic workflow, inspect health, and run
a backup/isolated restore. Confirm the release page is marked as a prerelease,
the preview loads, and all downloadable assets are expected.
