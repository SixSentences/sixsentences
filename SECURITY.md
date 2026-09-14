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
- the self-hostable browser extension source and build tooling;
- the macOS Companion source and its local recovery boundary;
- the reference Docker Compose deployment and backup/restore tooling; and
- repository build, test, security, and release automation.

The marketing website, official hosted deployment, provider accounts, billing,
commercial administration, browser-store distribution, signed/notarized
Companion binaries, update feeds, and production infrastructure are operated separately.
Do not probe `sixsentences.com`,
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

### How credentials are stored

Four kinds of secret reach this service, and each is stored the way its own
threat model requires rather than the way the others are.

**Account passwords** are the only low-entropy secret a person chooses, so they
are the only ones behind a deliberately expensive function: PBKDF2-HMAC-SHA256
with a per-password random salt and 600,000 iterations, stored as
`pbkdf2_sha256$iterations$salt$hash` so the cost can be raised later without
invalidating existing accounts.

**Bearer tokens** — API keys, sessions, e-mail verification, MFA challenges,
stream tickets, Companion and capture codes — are not chosen by anyone. Each is
`secrets.token_urlsafe(32)`: 32 bytes from the operating system's CSPRNG, 256
bits of entropy, stored as a SHA-256 digest. A slow function here would add no
strength, because there is no guessable structure to slow an attacker down
against, and it would run on every authenticated request. The digest exists so a
database copy does not hand over usable tokens.

**Two-factor recovery codes** carry 80 bits from the same CSPRNG and are stored
as SHA-256 digests of their normalized form, for the same reason.

**Third-party connector credentials** are the exception that cannot be hashed:
they have to be replayed to the provider that issued them. They are stored under
authenticated encryption with the deployment's own key, so a database copy
without that key yields nothing, and a tampered record fails to decrypt instead
of decrypting to something else. TOTP secrets are stored the same way.

The token design depends on one assumption: that issued tokens really are
CSPRNG output of the stated length. `test_credential_storage.py` asserts exactly
that, so the assumption cannot quietly stop holding. A static analyser that
classifies any hashed credential as a password will flag the SHA-256 in
`_token_hash`; the answer is this section, not a slow hash over a random 256-bit
value.

### Authentication and browser state

Treat the configured API origin as a trust boundary. Protect bearer tokens and
session material from cross-site scripting, logs, analytics, screenshots, and
third-party scripts. Deploy behind HTTPS, preserve Content Security Policy and
security headers, and review every change to CORS, OAuth redirects, tenancy,
authorization, public capability URLs, or browser storage.

The browser extension stores a revocable API key in trusted extension storage.
Its build requires deployment-specific app and API URLs, and its pairing
callback must be allowlisted exactly by the API. Do not add broad persistent
site permissions, expose the key to content scripts, or reuse another
distribution's extension identity.

### macOS Companion

Build the Companion with one explicit deployment origin. Release builds require
HTTPS; only debug builds accept HTTP on loopback. The API root is derived as
`/api`, redirects are refused, and browser handoffs must match the configured
scheme, host, and effective port exactly. Do not introduce hosted fallbacks,
hostname/device-name collection, raw server-detail display, or credentials in
URLs and logs.

The bearer credential and short-lived PKCE verifier are Keychain items. Four
bounded recovery stores can contain finalized transcript text, questions,
session identifiers, URLs, or synthesis settings. They request complete file
protection and use owner-only permissions, but operators should still require
FileVault for encrypted storage at rest. Explicit disconnect purges all stores,
account-bound preferences, private in-memory state, pairing state, and the
credential. Filesystem deletion is not a forensic secure-erase guarantee on
APFS/SSD media. The client has no server-push account-deletion hook; deployment
operators must invalidate credentials and document that users should disconnect
the Mac for immediate local deletion.

Pull-request automation builds and tests source without signing credentials.
Developer ID signing, notarization, Sparkle appcast publication, and official
binary distribution require a separate reviewed release process.

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

Lockfiles, CodeQL, dependency review, current-tree and full-history secret
scanning, configuration scanning, checksums, SBOMs, and provenance attestations
are defense in depth. The history scanner does not submit candidate credentials
to provider APIs for online verification. These controls do not prove that an
artifact is vulnerability-free. Verify immutable tags, image digests, checksums,
and attestations before deploying, and review changes to pinned GitHub Actions
like any other executable dependency.

## Good-faith research

Use local or explicitly authorized systems, minimize data access, avoid privacy
violations and disruption, and stop once a vulnerability is demonstrated.
Coordinate disclosure privately so users can be protected before technical
details are published.
