# SixSentences web client

The open-source Next.js client for the SixSentences research workspace.

It provides the browser interface for literature search and review, the
research library, projects, research data, figures, surveys, interviews,
knowledge, brainstorming, and manuscript/LaTeX workflows. The client is useful
for inspecting, testing, and extending those interfaces, but it does not contain
the services that execute them.

> [!IMPORTANT]
> This directory is **not a complete self-hosted SixSentences deployment**. It
> requires a separately supplied API that implements the HTTP, Server-Sent
> Events, and WebSocket contracts consumed by the client. The Python package in
> the repository root is not that API server.

## Distribution boundary

This snapshot is intentionally narrower than the hosted product.

Included:

- the Next.js 16 and React 19 application;
- typed browser-side API contracts and query state;
- interfaces for the multi-stage research workflow;
- source, citation, review, and provenance presentation;
- authentication and public-sharing client flows; and
- focused UI, accessibility, privacy, and security tests.

Not included:

- the marketing and landing website;
- the hosted SaaS backend, databases, workers, model providers, or prompts;
- server-side identity, email, collaboration, and storage services;
- billing, pricing, checkout, subscriptions, administration, or operator tools;
- deployment, monitoring, analytics, backup, and recovery infrastructure;
- the browser extension or macOS companion; or
- production configuration, credentials, customer data, or historical binary
  downloads.

Some screens describe API-backed capabilities that a compatible API may choose
not to implement. The client must handle those capabilities as unavailable; it
must not silently redirect a self-hosted build to the official production API.

## Requirements

- Node.js 22.9 or newer
- npm with the committed `package-lock.json`
- a compatible API for authenticated and research-workflow actions

## Configure

Copy the public development defaults:

```bash
cp .env.example .env.local
```

The client recognizes these build-time variables:

| Variable | Purpose | Development default |
| --- | --- | --- |
| `NEXT_PUBLIC_SIX_API_URL` | Trusted compatible API origin | `http://127.0.0.1:8000` |
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

The API must explicitly allow the client's origin and implement its own
authentication, authorization, storage, retention, and provider policies.
Review [`src/lib/api.ts`](src/lib/api.ts) and the focused contract tests before
claiming compatibility.

## Run

```bash
npm ci
npm run dev
```

Open <http://localhost:3000>. Static and unauthenticated surfaces can render
without an API; API-backed actions will fail or remain unavailable until a
compatible service is running.

For a production-oriented source build:

```bash
npm run typecheck
npm run test:security
npm run test:ui
npm run build
```

The repository does not publish a preconfigured production web image. A
distributor is responsible for pinning the client revision, selecting a trusted
API origin, reviewing the resulting dependency closure, and testing the exact
artifact it deploys.

## Application map

| Area | Representative source |
| --- | --- |
| Application and authentication routes | `src/app` |
| Literature runs, screening, extraction, and evidence views | `src/components/run` |
| Library, source identity, citations, and sharing | `src/components/library` |
| Research data and scientific figures | `src/app/(app)/data`, `src/app/(app)/figures` |
| Surveys and interviews | `src/app/(app)/surveys`, `src/app/(app)/interviews` |
| Manuscript and LaTeX workspace | `src/app/(app)/writer`, `src/components/writer` |
| Knowledge and brainstorming | `src/app/(app)/knowledge`, `src/app/(app)/brainstorming` |
| API types, transport, and error contracts | `src/lib/api.ts`, `src/lib/types.ts` |
| Streaming run progress | `src/hooks/use-run-stream.ts` |
| Reusable interface components | `src/components/ui` |

## Security and privacy notes

- Treat the configured API origin as a security boundary. The browser sends
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
