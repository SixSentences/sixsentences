# Security policy

## Supported versions

SixSentences is pre-1.0 software. Security fixes are applied to the latest alpha
release and the `main` branch. Older snapshots may not receive backports.

| Version | Security fixes |
| --- | --- |
| Latest `0.1.x` alpha | Supported on a best-effort basis |
| `main` | Supported; may contain unreleased changes |
| Older snapshots | Not supported |

## Report a vulnerability privately

Do not disclose a suspected vulnerability in a public issue, discussion, pull
request, test fixture, or demonstration deployment.

Use the repository's **Security** tab and select **Report a vulnerability** to
open a private GitHub security advisory:

<https://github.com/SixSentences/sixsentences/security/advisories/new>

Include, where safe:

- the affected component, version, or commit;
- a description of the impact and attack preconditions;
- minimal reproduction steps or a proof of concept;
- suggested mitigations, if known; and
- whether you plan to publish details and on what timeline.

Remove credentials, personal data, copyrighted research material, and unrelated
production information from the report. If GitHub private vulnerability
reporting is temporarily unavailable, email `legal@sixsentences.com` with the
subject `CONFIDENTIAL: SixSentences open-source security`. Do not disclose the
vulnerability or request a contact channel in a public issue.

Maintainers aim to acknowledge reports within five business days, then assess
severity, coordinate a fix, and agree on a disclosure plan. This is a target,
not a service-level guarantee. The project does not operate a bug bounty
program.

## Repository scope

This policy covers security defects in the code distributed from this
repository:

- the Python research engine and CLI under `src/sixsentences`;
- the Next.js web client under `apps/web`; and
- repository build, test, and release automation.

The marketing site, hosted SaaS backend, production identity and storage
services, model/provider operations, billing and administration systems,
deployment infrastructure, browser extension, and macOS companion are not
distributed from this repository. A vulnerability that exists only in one of
those separately operated systems must be reported through the private security
contact for that service.

Do not test `sixsentences.com`, `app.sixsentences.com`, an official API, or
any other production deployment without explicit prior authorization. A public
source release is not authorization to probe a hosted service.

## Security boundaries

### Python engine

Most engine operations are local. Networked connectors are named explicitly.
Inputs such as XLSX archives and tabular files are untrusted: the importers
apply bounded, fail-closed limits, but callers should still process untrusted
material with least privilege and without unrelated secrets in the environment.

The engine does not bundle a scholarly corpus, model-provider key, or hidden
agent loop. An output is not a security or methodological guarantee; callers
remain responsible for reviewing sources and generated artifacts.

### Web client

The web client is not a backend. Its configured API origin is a trust boundary:
authenticated requests and research content are sent to that origin. Inspect
and control `NEXT_PUBLIC_SIX_API_URL` in every build. A self-hosted build must
not fall back silently to the official production API.

All `NEXT_PUBLIC_*` values are delivered to browsers and therefore public.
Never use them for API secrets, OAuth client secrets, signing keys, database
credentials, or privileged tokens.

The current client keeps an opaque bearer token in browser local storage. When
deploying or changing the client, review cross-site scripting defenses, Content
Security Policy, third-party scripts, browser extensions, and the complete API
origin. Do not use real credentials or research data in test fixtures,
screenshots, issue reports, or public preview deployments.

## Good-faith research

Security research should use local or explicitly authorized systems, minimize
data access, avoid privacy violations and service disruption, and stop once a
vulnerability is demonstrated. Coordinate disclosure privately so users can be
protected before technical details are published.
