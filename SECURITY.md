# Security policy

## Supported versions

SixSentences is pre-`1.0` software. Security fixes are applied to the latest
alpha and to `main`; older snapshots do not receive guaranteed backports.

| Version | Security fixes |
| --- | --- |
| Latest `0.2.x` alpha | Best-effort support |
| `main` | Supported, potentially unreleased |
| Older releases and commits | Not supported |

## Report a vulnerability privately

Do not disclose a suspected vulnerability in an issue, discussion, pull
request, public fork, test fixture, or demonstration deployment.

Use GitHub's
[private vulnerability report](https://github.com/SixSentences/sixsentences/security/advisories/new).
If that flow is unavailable, email `legal@sixsentences.com` with the subject
`CONFIDENTIAL: SixSentences open-source security`.

Include, where safe:

- the affected component, release, or full commit;
- impact, attack preconditions, and the smallest reproduction;
- suggested mitigation, if known; and
- your intended disclosure timeline.

Remove credentials, personal data, participant content, copyrighted research
material, and unrelated production information. Maintainers aim to acknowledge
reports within five business days, assess severity, coordinate a fix, and agree
on disclosure. This is a target, not an SLA, and the project has no bug bounty.

## Scope and authorization

This policy covers code distributed from this repository:

- the Python research engine and CLI;
- the application API, database migrations, and worker;
- the Next.js web application;
- the reference Docker Compose deployment and backup/restore tooling; and
- repository build, test, security, and release automation.

The marketing website, official hosted deployment, provider accounts, billing,
commercial administration, browser extension, macOS companion, and production
infrastructure are operated separately. Do not probe `sixsentences.com`,
`app.sixsentences.com`, official APIs, or any other live system without explicit
prior authorization. Publication of source code is not authorization to test a
hosted service.

## Deployment boundaries

### Secrets and configuration

Generate `.env.selfhost` locally and keep it out of version control. Browser
variables prefixed with `NEXT_PUBLIC_` are public by definition and must never
contain API keys, signing keys, OAuth client secrets, database credentials, or
privileged tokens. Docker administrators can inspect container environments;
protect host access and move to an external secret manager when appropriate.

Enable registration only after verified transactional mail and public legal
notices are configured. Enable model, web-search, speech, or parsing providers
only after reviewing their data flow, terms, retention, transfer, security, and
cost behavior. An operator confirmation switch is a safety gate, not a legal or
regulatory certification.

### Authentication and browser state

Treat the configured API origin as a trust boundary. Protect bearer tokens and
session material from cross-site scripting, logs, analytics, screenshots, and
third-party scripts. Deploy behind HTTPS, preserve Content Security Policy and
security headers, and review every change to CORS, OAuth redirects, tenancy,
authorization, public capability URLs, or browser storage.

### Data and research content

Research uploads, interview responses, survey responses, generated artifacts,
and public participation links are sensitive. Apply least privilege, keep
backups encrypted and access-controlled, test restoration, enforce retention
and erasure procedures, and avoid logging request bodies or capability URLs.
The reference Compose file does not replace host hardening, off-site backup,
monitoring, incident response, or a deployment-specific privacy assessment.

Input parsers and importers apply bounded, fail-closed limits, but untrusted
documents and archives should still be processed without unrelated secrets and
with minimal host privileges.

### Dependencies and artifacts

Lockfiles, CodeQL, dependency review, secret/configuration scanning, checksums,
SBOMs, and provenance attestations are defense in depth. They do not prove that
an artifact is vulnerability-free. Verify immutable tags, image digests,
checksums, and attestations before deploying, and review changes to pinned
GitHub Actions like any other executable dependency.

## Good-faith research

Use local or explicitly authorized systems, minimize data access, avoid privacy
violations and disruption, and stop once a vulnerability is demonstrated.
Coordinate disclosure privately so users can be protected before technical
details are published.
