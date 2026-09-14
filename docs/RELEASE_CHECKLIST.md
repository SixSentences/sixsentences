# Release checklist

Use this checklist in the release pull request. Link evidence that contains no
credentials, personal data, participant content, customer data, private prompts,
or production configuration.

## Source and scope

- [ ] The tagged tree contains the engine, complete application API, worker,
      migrations, web workspace, browser-extension source, macOS Companion
      source, self-hosting definition, and tests.
- [ ] Web method/path contracts are complete and the contract gate passes.
- [ ] Billing, checkout, plans, subscriptions, hosted administration, production
      operations, the marketing site, production data, and credentials are absent.
- [ ] Every bundled asset and fixture is synthetic, original, or documented with
      redistribution-compatible provenance.
- [ ] Current files and the complete public Git history have been scanned for
      secrets, key material, credentials, personal data, database dumps, logs,
      user uploads, and production artifacts.
- [ ] The current-tree and no-verification history scanners pass at the exact
      release commit; findings are investigated rather than broadly ignored.
- [ ] Dependency and source-license notices are current.
- [ ] The Companion tree contains no `.app`, ZIP, DMG, package, archive,
      certificate, provisioning profile, private key, log, or update feed.

## Version and review

- [ ] Engine and API PEP 440 versions, the API's exact engine pin, web and
      browser-extension npm metadata/locks, citation, changelog, release note,
      and tag versions agree; Companion plist and source versions agree with
      each other.
- [ ] `CITATION.cff` uses the tagged commit's UTC date.
- [ ] The release commit is reviewed, DCO-signed, CLA-accepted, contained in
      `main`, and clean; the annotated release tag has a trusted SSH signature.
- [ ] Required CI, CodeQL, dependency-review, security, and container checks pass.
- [ ] Companion boundary, exact Swift resolution, tests, release compilation,
      plist validation, and shell validation pass on the pinned Xcode
      toolchains, including the macOS 26 SpeechAnalyzer path.
- [ ] Browser-extension permissions, exact origins, contracts, tests, and
      deterministic unpacked build pass without a store identity or credential.
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
- [ ] Companion `/interviews/live/*` and `/companion/paper-chats/*` contracts,
      exact-origin browser handoffs, local-only speech, and complete disconnect
      purge were reviewed with synthetic fixtures.

## Artifacts and publication

- [ ] Python sdist and wheel install in isolated environments and match checksums.
- [ ] API and web images are rebuilt and scanned from the tag. The
      origin-specific web image is not published as a generic artifact.
- [ ] The GitHub release describes the Companion as source-only. Developer ID,
      notarization, Sparkle appcast, and native binary publication remain a
      separate reviewed follow-up.
- [ ] No alpha image is published under a mutable `latest` tag.
- [ ] The 16:9 product GIF was reviewed for rights and sensitive content, contains
      no audio, matches `docs/assets/sixsentences-overview.sha256`, and is the
      only asset in the draft before automation uploads the build artifacts.
- [ ] The GitHub release is a prerelease, its links and preview resolve, and its
      downloadable assets exactly match the release notes.
- [ ] A release maintainer reviewed the completed workflow evidence and approved
      the protected `community-release` environment before publication.
- [ ] A maintainer independently verifies checksums, attestations, image digests,
      fresh startup, and the private vulnerability-reporting link.

## Repository controls

- [ ] `main` rejects force pushes and requires review, conversation
      resolution, DCO, CLA, CI, CodeQL, dependency review, and security checks.
- [ ] While the documented founding single-maintainer exception applies, the
      release pull request contains a line-by-line self-review record. Once a
      second maintainer is active, rules require a non-author approval and
      CODEOWNER review instead.
- [ ] `v*` tags reject update and deletion and creation is restricted.
- [ ] Secret scanning, push protection, Dependabot alerts, private vulnerability
      reporting, issues, and discussions are enabled.
- [ ] Workflow token permissions are read-only by default and elevated only in
      the individual release jobs that publish or attest artifacts.
