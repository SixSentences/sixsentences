# Releasing SixSentences

Releases come from immutable tags on reviewed `main` commits. GitHub Actions
validates the complete source tree, publishes Python-engine distributions,
records checksums and available SBOM/provenance
attestations, and creates a GitHub prerelease. It does not publish to PyPI or
npm and uses no long-lived registry credential.

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
2. Set the equivalent public version in `services/api/pyproject.toml`,
   `apps/web/package.json`, `apps/web/package-lock.json`, and `CITATION.cff`.
3. Set `CITATION.cff`'s UTC date to the release commit date.
4. Move entries from `Unreleased` to a dated `CHANGELOG.md` section.
5. Add `docs/releases/<tag>.md` and update user-facing deployment examples.
6. Complete every item in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

For this release, `0.2.0a1` in Python metadata maps to public version
`0.2.0-alpha.1` and tag `v0.2.0-alpha.1`. Beta and release-candidate suffixes
map to `-beta.N` and `-rc.N`.

The release workflow checks that the tag resolves to the checked-out commit, is
contained in `origin/main`, matches all canonical metadata, and has a release
note and changelog entry for the tagged commit's UTC date.

## Create the tag and stage the reviewed preview

After the release pull request is merged and all required checks are green:

```console
git switch main
git pull --ff-only
git tag -a v0.2.0-alpha.1 -m "SixSentences v0.2.0-alpha.1"
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
```

Run the two publication commands together in one supervised release session.
The tag starts the workflow; the workflow will fail closed unless the draft
exists, contains only `sixsentences-overview.gif`, and that asset matches
`docs/assets/sixsentences-overview.sha256`. A failed workflow never publishes
the draft. Correct the draft and rerun the failed workflow without moving or
reusing the tag.

The workflow rebuilds and retests from the tag. Python wheels are built from the
source distribution and installed in isolation. API and web sources are tested
from locked environments. The self-hosting definition and both container builds
are validated from the tag. Images remain deployment-built during this alpha;
in particular, public origins and legal versions are compile-time browser
configuration, so a generic web image would be misleading.

The GitHub release remains a prerelease while the project is in alpha. The
product-overview GIF is a presentation asset, not an executable artifact;
review it for personal, customer, participant, and non-redistributable content,
render it without audio, and verify its committed checksum before staging it as
`sixsentences-overview.gif`.

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
