# Releasing SixSentences

Releases are built from immutable prerelease tags by GitHub Actions. The
workflow publishes a GitHub prerelease only. It never publishes to PyPI and
does not use a package-registry token.

## Release authority and review

Only a maintainer may create a release tag. The release commit must reach
`main` through the normal pull-request and required-check process. Automation
validates existing DCO sign-offs but never creates a sign-off or any other
personal legal attestation.

Protect `main` with the checks documented in `GOVERNANCE.md`. Protect tags that
match `v*` against update, force-push, and deletion. Restrict tag creation to
release maintainers. A release tag is immutable even if its workflow fails.

## Prepare the release pull request

1. Set the canonical PEP 440 version in `pyproject.toml`.
2. Set the equivalent public version and UTC release date in `CITATION.cff`.
3. Move the relevant entries from `Unreleased` into a dated section in
   `CHANGELOG.md`.
4. Add `docs/releases/<tag>.md`, for example
   `docs/releases/v0.1.0-alpha.1.md`.
5. Run the complete local validation suite:

   ```bash
   uv sync --frozen --group dev
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src/sixsentences
   uv run pytest -q
   uv build
   ```

The supported mapping is explicit: `0.1.0a1` in Python metadata corresponds to
`0.1.0-alpha.1` in CFF and tag `v0.1.0-alpha.1`. Beta and release-candidate
suffixes map to `-beta.N` and `-rc.N` respectively.

## Create the tag

After the release pull request is merged and every required check is green,
create an annotated tag at that exact `main` commit and push only the tag:

```bash
git switch main
git pull --ff-only
git tag -a v0.1.0-alpha.1 -m "SixSentences v0.1.0-alpha.1"
git push origin v0.1.0-alpha.1
```

The tag-triggered workflow verifies that the tag resolves to its checked-out
commit, is contained in `origin/main`, matches `pyproject.toml` and
`CITATION.cff`, has the tagged commit's UTC date, and has matching changelog and
release-note entries.

It then runs the quality suite, builds one source distribution and one wheel
from that source distribution, installs both in isolated environments, creates
SHA-256 checksums and a CycloneDX 1.5 runtime SBOM, records GitHub artifact and
SBOM attestations, and creates the GitHub prerelease.

If validation fails, fix the problem through another pull request and increment
the prerelease number. Never move or reuse the failed tag.

## Verify published assets

Download the assets into an empty directory, then verify their checksums and
GitHub provenance:

```bash
gh release download v0.1.0-alpha.1 --repo SixSentences/sixsentences
sha256sum --check SHA256SUMS
gh attestation verify sixsentences_engine-0.1.0a1-py3-none-any.whl \
  --repo SixSentences/sixsentences
gh attestation verify sixsentences_engine-0.1.0a1.tar.gz \
  --repo SixSentences/sixsentences
```

Review the release page, install both distributions once more from the
downloaded assets, and confirm that the release remains marked as a prerelease.
