# Governance

SixSentences is an open-source project stewarded through the
[SixSentences GitHub organization](https://github.com/SixSentences). This file
governs the community source tree. The separately operated hosted service,
marketing site, and commercial terms are not governed by this repository.

## Roles

- **Contributors** report issues, improve documentation, review proposals, and
  submit work under the DCO.
- **Reviewers** are trusted contributors who provide substantive review but may
  not have merge or release access.
- **Maintainers** have repository write access and are accountable for scope,
  review quality, releases, security response, and moderation.

Repository permissions and `CODEOWNERS` are the source of truth for current
authority. The project begins in a founding-maintainer phase. Maintainers should
distribute review and release access as sustained contributors emerge, reducing
single-person dependency without weakening review.

## Decisions

Routine, compatible changes use lazy consensus through issues and pull
requests. Maintainers consider technical evidence, user impact, maintenance
cost, and unresolved objections, then record the merge decision.

Open a proposal before work on:

- a breaking API, schema, migration, or serialized-format change;
- a new runtime dependency, network integration, or data processor;
- a research-method or compliance claim;
- removal of a documented capability;
- licensing, governance, or contribution-policy changes; or
- movement of functionality across the community/hosted boundary.

The proposal should describe the problem, alternatives, compatibility and
migration impact, privacy/security effects, and a practical rollback. Consensus
is sought but does not require unanimity. If agreement is not possible,
maintainers document the decision and rationale. Security discussions may stay
private until coordinated disclosure is safe.

## Branches, review, and merge

`main` is the only long-lived branch and holds reviewed, release-ready history.
Ordinary work uses short-lived branches into `main`, which are deleted after the
merge. Releases are tagged from reviewed `main` commits.

A separate integration branch is deliberately not used. With the protection
`main` carries — required review, the full check suite, code scanning, DCO, CLA,
signed commits and linear history — staging adds no safety, while a second
long-lived branch has to be kept in step by hand and drifts silently when it is
not.
Force pushes to either protected branch and movement or deletion of release
tags are prohibited.

Pull requests must pass the applicable engine, API, web, browser-extension,
macOS Companion, self-hosting, dependency, CodeQL, secret/configuration, and DCO
checks. Required checks are a floor: a maintainer may request focused tests,
threat analysis, migration evidence, or a release rehearsal proportional to risk.

Once two active maintainers exist, a substantive change requires approval from
at least one non-author maintainer. Authors do not approve their own work.
During the founding single-maintainer phase, the founding maintainer may merge
after all required checks pass, a line-by-line self-review is recorded on the
pull request, and security-sensitive changes have a focused test or documented
rehearsal. This exception ends when a second active maintainer joins.

Maintainers may decline a correct contribution when its long-term maintenance
cost, risk, or scope does not fit the project. The decision should include a
concise explanation.

## Release authority

Only maintainers with release permission may create `v*` tags, publish GitHub
releases, or push community container images. A release must:

1. come from a reviewed commit contained in `main`;
2. pass the complete source, application, self-hosting, and security gates;
3. have aligned versions, changelog, citation metadata, and release notes;
4. publish checksums and available SBOM/provenance attestations;
5. be smoke-tested from its immutable artifacts; and
6. follow [docs/RELEASING.md](docs/RELEASING.md) and the release checklist.

Release tags are immutable. A failed release is replaced by a new version, not
by moving a tag. Pre-`1.0` releases may contain breaking changes, but those
changes require migration notes and an explicit changelog entry.

## Community and hosted boundary

The community repository contains the research engine, application API,
background worker, web workspace, browser-extension source, macOS Companion
source, database migrations, self-hosting definition, and tests needed to run
the research application.
Provider connections use operator-supplied accounts and explicit processing
controls. Official signed/notarized native binaries and update feeds require
separate release authority and are not distributed by the source-release
pipeline.

Billing, checkout, subscriptions, commercial-plan enforcement, hosted-service
administration, the production deployment, the marketing site, customer data,
and production credentials remain separate. Apache-2.0 permits commercial use
of community code; separate operation does not grant the hosted service special
rights over outside contributions.

## Becoming or leaving a maintainer

A contributor may be invited after sustained constructive work, sound review
judgment, responsible handling of security and privacy, respect for research
claims, and adherence to the Code of Conduct. Existing maintainers decide and
record invitations publicly.

Maintainers may step down at any time. Access may be removed for compromised
credentials, prolonged inactivity, repeated policy violations, or conduct
enforcement. Except for urgent security cases, the affected person should be
notified before access changes.

## Conflicts of interest

Reviewers and maintainers disclose material interests and recuse themselves
where appropriate. Commercial use of Apache-licensed code is allowed and is not
itself a conflict. Undisclosed influence over technical, security, privacy, or
research-method claims is a conflict.

## Contributions and amendments

Contributions are accepted under Apache-2.0 through per-commit certification
under the [Developer Certificate of Origin 1.1](DCO) and public acceptance of
the repository [Contributor License Agreement](CLA.md). The CLA grants a
license, not copyright ownership, and its verifier sends no record to an
external service. The project will not claim rights it has not received or
relicense contributions incompatibly without the permissions required to do so.

Governance changes require a public issue and pull request describing their
impact. Material changes should remain open long enough for active contributors
to comment, except when urgent security or legal risk requires prompt action.
