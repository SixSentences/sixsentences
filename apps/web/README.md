# SixSentences web application

The open-source Next.js application for the self-hosted SixSentences research
workspace.

It provides the browser interface for literature search and review, the
research library, projects, research data, figures, surveys, interviews,
knowledge, brainstorming, manuscript/LaTeX workflows, and the scoped developer
API reference. It connects to the API shipped in this repository at
[`services/api`](../../services/api).

> [!IMPORTANT]
> Run this application as part of the repository's self-hosted stack. Browser
> requests, Server-Sent Events, public written interviews, and spoken-interview
> WebSockets must all target the same trusted API deployment. The web image never
> falls back to the hosted SixSentences service.

## Community boundary

The community application contains the complete research workspace UI. Its
boundary removes hosted commercial and operator-only surfaces, not research
features.

Included:

- the Next.js 16 and React 19 application;
- typed browser-side API contracts and query state;
- interfaces for the multi-stage research workflow;
- source, citation, review, and provenance presentation;
- authentication, public sharing, written and spoken interview flows;
- the deployment-scoped developer API reference; and
- focused UI, accessibility, privacy, and security tests.

Not included:

- billing, pricing, checkout, subscriptions, administration, or operator tools;
- the separate marketing/landing website;
- the browser-extension runtime, which is built separately from
  `../browser-extension`;
- the macOS Companion source, which lives separately at
  `../companion-macos`; or
- production configuration, credentials, customer data, or historical binary
  downloads.

Optional provider-backed capabilities remain unavailable until the deployment
operator configures them in the self-hosted API. The client handles unavailable
capabilities explicitly and never redirects a community build to an official
production API.

## Requirements

- Node.js 22.9 or newer
- npm with the committed `package-lock.json`
- the repository's self-hosted API for authenticated and research-workflow actions

## Configure

Copy the local development defaults:

```bash
cp .env.example .env.local
```

The application recognizes these build-time variables:

| Variable | Purpose | Development default |
| --- | --- | --- |
| `NEXT_PUBLIC_SIX_API_URL` | Trusted self-hosted API origin | `http://127.0.0.1:8000` |
| `NEXT_PUBLIC_APP_URL` | Canonical client origin used for metadata and links | `http://localhost:3000` |
| `NEXT_PUBLIC_LEGAL_BASE_URL` | Origin serving the deployment's `/terms`, `/privacy`, `/dpa`, and `/imprint` documents | empty |
| `NEXT_PUBLIC_TERMS_VERSION` | Operator-published Terms version accepted during registration | empty |
| `NEXT_PUBLIC_PRIVACY_VERSION` | Operator-published privacy-notice version acknowledged during registration | empty |
| `NEXT_PUBLIC_DPA_VERSION` | Operator-published processing-agreement version sent to the API contract | empty |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | Optional public OAuth client identifier | empty |

The development defaults deliberately do not invent legal documents at the
local app origin. Until an operator configures the legal origin and all three
versions, registration fails closed and legal navigation is omitted. Existing
account, export, and security controls remain available when the API requires a
new acceptance.

Every `NEXT_PUBLIC_*` value is embedded in browser-delivered JavaScript. Never
put a secret, private API key, OAuth client secret, or privileged credential in
one of these variables.

The self-hosted API must explicitly allow the client's origin. Authentication,
authorization, storage, retention, provider policy, and migrations are owned by
the server and deployment configuration. Review [`src/lib/api.ts`](src/lib/api.ts)
and the focused contract tests when changing the browser/server contract.

## Run

```bash
npm ci
npm run dev
```

Open <http://localhost:3000>. Start the repository's API first; authenticated
and research-workflow actions deliberately fail closed when it is unavailable.

For a production-oriented source build:

```bash
npm run typecheck
npm run test:security
npm run test:ui
npm run build
```

The included Dockerfile builds the production web service. Production builds
require explicit HTTPS app, API, and legal-document origins plus operator-owned
legal-document versions; this prevents an image from silently targeting local
development or hosted infrastructure.

## Application map

| Area | Representative source |
| --- | --- |
| Application and authentication routes | `src/app` |
| Literature runs, screening, extraction, and evidence views | `src/components/run` |
| Library, source identity, citations, and sharing | `src/components/library` |
| Research data and scientific figures | `src/app/(app)/data`, `src/app/(app)/figures` |
| Surveys and interviews | `src/app/(app)/surveys`, `src/app/(app)/interviews` |
| Developer API reference | `src/app/(app)/docs` |
| Manuscript and LaTeX workspace | `src/app/(app)/writer`, `src/components/writer` |
| Knowledge and brainstorming | `src/app/(app)/knowledge`, `src/app/(app)/brainstorming` |
| API types, transport, and error contracts | `src/lib/api.ts`, `src/lib/types.ts` |
| Streaming run progress | `src/hooks/use-run-stream.ts` |
| Reusable interface components | `src/components/ui` |

## Security and privacy notes

- Treat the self-hosted API origin as a security boundary. The browser sends
  authenticated requests and research content to that origin.
- The current client keeps its opaque bearer token in browser local storage.
  A deployment must account for that model when reviewing XSS defenses, CSP,
  extensions, and third-party scripts.
- The client does not ship a product-analytics tracker. Server-side logging and
  data handling, if any, are properties of the connected API and deployment.
- Test fixtures must remain synthetic or clearly redistributable. Do not add
  production logs, user exports, research datasets, credentials, or `.env.local`.

Report vulnerabilities privately as described in the repository
[`SECURITY.md`](../../SECURITY.md).

## Contributing and license

Run the web checks relevant to a change and explain any API-contract,
accessibility, privacy, security, or dependency impact in the pull request.
Repository contributions require a per-commit
[Developer Certificate of Origin](../../DCO) sign-off; see
[`CONTRIBUTING.md`](../../CONTRIBUTING.md).

Except for separately identified marks and third-party material, the client is
licensed under [Apache-2.0](../../LICENSE). Generated or adapted shadcn/ui
components retain their MIT notice, and package dependencies retain their own
licenses; see [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) and
[`TRADEMARKS.md`](../../TRADEMARKS.md).
