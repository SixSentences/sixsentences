# Release checklist

Use this checklist in the release pull request. Link evidence that contains no
credentials, personal data, participant content, customer data, private prompts,
or production configuration.

## Source and scope

- [ ] The tagged tree contains the engine, complete application API, worker,
      migrations, web workspace, self-hosting definition, and tests.
- [ ] Web method/path contracts are complete and the contract gate passes.
- [ ] Billing, checkout, plans, subscriptions, hosted administration, production
      operations, the marketing site, production data, and credentials are absent.
- [ ] Every bundled asset and fixture is synthetic, original, or documented with
      redistribution-compatible provenance.
- [ ] Current files and the complete public Git history have been scanned for
      secrets, key material, credentials, personal data, database dumps, logs,
      user uploads, and production artifacts.
- [ ] Dependency and source-license notices are current.

## Version and review

- [ ] Engine, API, web, citation, changelog, release-note, and tag versions agree.
- [ ] `CITATION.cff` uses the tagged commit's UTC date.
- [ ] The release commit is reviewed, DCO-signed, CLA-accepted, contained in
      `main`, and clean.
- [ ] Required CI, CodeQL, dependency-review, security, and container checks pass.
- [ ] Release-note claims match tested community behavior and list breaking or
      migration changes.

## Application and deployment

- [ ] A clean database migrates to head and the API/worker test suites pass.
- [ ] The web application type-checks, passes all tests, and makes a production
      build against explicit non-production origins.
- [ ] A fresh Compose deployment starts from generated local configuration and
      supports initial-account creation plus a synthetic end-to-end workflow.
- [ ] Registration, participant links, text interviews, spoken-interview gates,
      retention, and account erasure fail closed under missing prerequisites.
- [ ] Backup and isolated restore succeed at the release revision, including
      authenticated erasure-journal verification and replay.
- [ ] Upgrade and rollback instructions were rehearsed for the supported path.

## Artifacts and publication

- [ ] Python sdist and wheel install in isolated environments and match checksums.
- [ ] API and web images are rebuilt and scanned from the tag. The
      origin-specific web image is not published as a generic artifact.
- [ ] No alpha image is published under a mutable `latest` tag.
- [ ] The 16:9 product GIF was reviewed for rights and sensitive content, contains
      no audio, matches `docs/assets/sixsentences-overview.sha256`, and is the
      only asset in the draft before automation uploads the build artifacts.
- [ ] The GitHub release is a prerelease, its links and preview resolve, and its
      downloadable assets exactly match the release notes.
- [ ] A maintainer independently verifies checksums, attestations, image digests,
      fresh startup, and the private vulnerability-reporting link.

## Repository controls

- [ ] `main` and `develop` reject force pushes and require review, conversation
      resolution, DCO, CLA, CI, CodeQL, dependency review, and security checks.
- [ ] `v*` tags reject update and deletion and creation is restricted.
- [ ] Secret scanning, push protection, Dependabot alerts, private vulnerability
      reporting, issues, and discussions are enabled.
- [ ] Workflow token permissions are read-only by default and elevated only in
      the individual release jobs that publish or attest artifacts.
