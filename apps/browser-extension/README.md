# SixSentences Browser Capture

This directory contains the source for a self-hostable Manifest V3 browser
extension. It connects only to the SixSentences application and API selected at
build time. The repository does not ship a browser-store archive, an official
hosted-service extension identity, or signing material.

The extension saves bounded bibliographic metadata, the active source URL,
text explicitly selected by the user, and—only after strict verification—an
eligible PDF of at most 50 MiB. It uses `activeTab` instead of persistent access
to browsing activity and never requests wildcard access to every site.

## Build for a deployment

Use Node.js 22.9 or newer. A normal deployment requires HTTPS and explicit
application and API values:

```console
cd apps/browser-extension
npm ci
APP_ORIGIN=https://research.example.org \
API_ORIGIN=https://research.example.org/api \
node scripts/build.mjs --out build/unpacked
```

`APP_ORIGIN` must be a pathless origin. `API_ORIGIN` may include one fixed path
prefix, such as `/api`, because the reference Caddy deployment exposes the API
there. Credentials, query strings, fragments, encoded path separators, dot
segments, and trailing path separators are rejected. There is no production or
hosted-service fallback.

For an explicit loopback-only development build:

```console
SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK=true \
APP_ORIGIN=http://localhost:3000 \
API_ORIGIN=http://127.0.0.1:8000 \
node scripts/build.mjs --out build/local-unpacked
```

Plain HTTP is rejected unless that switch is exactly `true` and both values use
`localhost`, `127.0.0.1`, or `[::1]`. The build refuses to overwrite an existing
output directory. Given the same source and environment, generated runtime
files are byte-for-byte deterministic.

Load the output directory through **Load unpacked** in Chrome or Edge. The
generated manifest grants API host access only for the configured scheme and
host. Chromium match patterns cannot narrow a host permission by port or path;
the service worker nevertheless sends API calls only to the exact configured
base URL.

## Pairing callback and extension identity

The source template deliberately has no manifest `key`, so a normal unpacked
installation receives an ID derived by the browser. After loading it, copy the
ID shown on the browser's extensions page and allow exactly this callback in
the self-hosted API:

```text
https://<extension-id>.chromiumapp.org/
```

For the reference stack, set that complete value in
`SIX_BROWSER_CAPTURE_REDIRECT_URIS` and restart the API before selecting
**Connect account**. Moving or reinstalling an unpacked extension can change its
ID and therefore requires a callback update.

Operators who need a stable ID may pass their own base64-encoded RSA public key
in SubjectPublicKeyInfo DER form as `SIX_EXTENSION_PUBLIC_KEY`. The build writes
that public value to the generated manifest. Keep all private key and signing
material outside the repository and outside the build environment. This source
tree does not reuse the identity of any hosted or store-distributed build.

## Trust boundary

- Pairing uses `chrome.identity.launchWebAuthFlow`, PKCE S256, a random state,
  and the browser-generated callback.
- The revocable API key is stored only in privileged extension storage. Content
  scripts cannot read it or send privileged worker messages.
- Ordinary pages use a metadata-only endpoint. The extension never sends the
  DOM, page body, cookies, browsing history, or request headers.
- PDF transfer is limited to a direct active PDF or an exact same-origin,
  user-visible scholarly attachment. Redirects and non-PDF bytes fail closed.
- Captured URLs remove common tracking, authentication, and high-entropy secret
  parameters before they are persisted as provenance.

The API owns authentication, authorization, retention, deletion, and storage.
Review the deployment's privacy notice and source-access rights before capturing
real material. A technically accessible PDF is not automatically licensed for
redistribution.

## Validate

```console
npm test
APP_ORIGIN=https://research.example.org \
API_ORIGIN=https://research.example.org/api \
node scripts/build.mjs --out /tmp/sixsentences-extension
```

The tests cover configuration validation, deterministic output, least-privilege
permissions, pairing, capture routes, bounded PDF handling, secret isolation,
and source-release hygiene. See the repository `SECURITY.md`,
`CONTRIBUTING.md`, `LICENSE`, `NOTICE`, and `TRADEMARKS.md` for project-wide
policies.
