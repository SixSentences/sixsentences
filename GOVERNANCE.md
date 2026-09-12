# Governance

SixSentences Engine is an open-source project stewarded through the
[SixSentences GitHub organization](https://github.com/SixSentences). This file
describes how decisions are made for the community engine. It does not govern
the separately operated hosted service.

## Roles

- **Contributors** report issues, improve documentation, review proposals, or
  submit code under the DCO.
- **Reviewers** are trusted contributors who provide substantive review but may
  not have merge access.
- **Maintainers** have repository write access and are responsible for scope,
  release quality, security response, moderation, and merge decisions.

The repository's GitHub permissions are the source of truth for current
maintainers. The project begins in a founding-maintainer phase. As sustained
contributors emerge, maintainers should distribute review and release access to
reduce single-person dependency.

## Decisions

Routine fixes and compatible improvements use lazy consensus through issues and
pull-request review: maintainers consider technical evidence, user impact, and
raised objections, then make a documented merge decision.

Open an issue before work on:

- a breaking public-API or serialized-format change;
- a new runtime dependency or network integration;
- a change to licensing, governance, or the engine/hosted boundary;
- removal of a documented capability; or
- a substantial research-method claim.

The issue should state the problem, alternatives, compatibility impact, and a
migration path. Maintainers seek consensus, but consensus is not unanimity. If
agreement cannot be reached in reasonable time, maintainers record the decision
and rationale in the issue. Security decisions may remain confidential until a
coordinated disclosure is safe.

## Changes and review

Once two active maintainers exist, at least one non-author maintainer approval
is required before merge and authors must not be the sole reviewer of a
substantive change. During the founding single-maintainer phase, the founding
maintainer may merge after passing CI, focused self-review, and a documented
issue; this exception ends when a second active maintainer joins.

Contributions must pass required checks and carry DCO sign-offs. Maintainers may
decline a correct contribution when its long-term maintenance cost or scope does
not fit the project. A decline should include a concise explanation.

## Becoming a maintainer

A contributor may be invited after demonstrating sustained, constructive work,
sound review judgment, respect for scope and research claims, responsible
handling of security and privacy, and adherence to the Code of Conduct. Existing
maintainers decide invitations and record the change publicly.

Maintainers may step down at any time. Access may be removed for prolonged
inactivity, compromised credentials, repeated policy violations, or conduct
enforcement. Except for urgent security cases, the person should be notified
before access changes.

## Conflicts of interest

Reviewers and maintainers disclose material interests that could affect a
decision and recuse themselves where appropriate. Commercial use of the Apache-
licensed engine is allowed and is not itself a conflict; undisclosed influence
over technical or research-method claims is.

## Releases and compatibility

Releases use semantic versioning, with PEP 440 equivalents for Python package
versions. While the project is below `1.0`, breaking changes may occur in minor
releases, but they should be announced and accompanied by practical migration
notes. A release must come from a reviewed commit with passing required checks.

Project contributions are accepted under Apache-2.0 through the DCO. The
project will not claim rights it has not received or relicense contributed code
incompatibly without the permissions required to do so.

## Amendments

Governance changes use a pull request with an issue describing the reason and
impact. Material amendments should remain open long enough for active
contributors to comment before merge, except when an urgent security or legal
issue requires prompt action.
