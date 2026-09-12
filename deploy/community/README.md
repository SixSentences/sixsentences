# Self-hosting SixSentences

This is the small reference deployment for one community installation. It runs
PostgreSQL, one API process, one all-lanes background worker, the open web
client and Caddy. PostgreSQL remains only on an internal backend network. The
API publishes no host port and Caddy reaches it over that backend; the API and
worker also use a separate outbound network for operator-selected providers.
Caddy is the only public service.

The deployment creates no account with any SixSentences-operated service. Mail,
scholarly metadata, model processing, speech processing and optional document
extraction use only endpoints and credentials supplied by the operator.

## Requirements

- Docker Engine with Docker Compose 2.33.1 or newer;
- at least 4 GiB RAM plus space for the database, documents and backups;
- for internet deployment, one DNS name pointing to the host and inbound TCP
  ports 80/443 plus UDP 443;
- an SMTP account before enabling public registration; and
- reviewed processor terms and participant information before enabling any
  external model, web-search or spoken-interview processing.

## Production TLS setup

Generate a private configuration without printing its secrets:

```console
bash deploy/community/init-env.sh --domain research.example.org
chmod 600 .env.selfhost
```

Edit `.env.selfhost` and replace the example sender, legal-document location,
metadata contact and optional provider settings. Keep registration and spoken
interviews disabled until their respective mail and processing prerequisites
are complete. The three generated secrets serve different purposes and must
not be copied between fields.

Run the fail-closed check, then build and start:

```console
bash deploy/community/preflight.sh .env.selfhost
docker compose --env-file .env.selfhost build
docker compose --env-file .env.selfhost up --detach --wait
```

Caddy obtains and renews public certificates automatically. The web client is
built with `SIX_PUBLIC_ORIGIN` and `SIX_PUBLIC_API_URL`; changing either value
requires rebuilding the web image. The public API lives below `/api`, while all
other paths go to the web client.

Inspect state without exposing secret values:

```console
docker compose --env-file .env.selfhost ps
docker compose --env-file .env.selfhost logs --tail 100 api worker web proxy
curl --fail https://research.example.org/api/health/ready
```

## Local HTTP mode

`init-env.sh --local` emits loopback-only HTTP URLs, configures Caddy not to
request certificates and sets the web image's explicit local-build opt-in. The
Dockerfile accepts that exception only for `localhost` and `127.0.0.1`; every
other non-HTTPS origin still fails the image build. TLS deployments must keep
the opt-in disabled.

## External services

All external features are off when their credentials are empty.

- `SIX_SMTP_*` configures transactional mail. Public registration additionally
  requires email verification and a non-example sender.
- `SIX_OPENALEX_*` identifies scholarly-metadata requests.
- `SIX_OPENROUTER_*`, `SIX_WEBSEARCH_*`, `SIX_LLM_ROUTING` and the local budget
  configure optional model/search processing. Use a credential-free HTTPS base
  URL and archive the applicable processor assessment first.
- Spoken interviews require a server-side `SIX_GEMINI_API_KEY` plus both
  explicit confirmation switches. Those switches do not replace participant
  notice, consent, retention and transfer review.
- `SIX_GROBID_URL` optionally selects an operator-controlled document parser.

Never put provider credentials in `NEXT_PUBLIC_*` values. Browser-visible build
arguments contain origins, versions and an optional public OAuth client ID only.

## Browser Capture

Build the extension in `apps/browser-extension` for this deployment's exact
`SIX_PUBLIC_ORIGIN` and `SIX_PUBLIC_API_URL`. Load the generated directory
unpacked, then copy the ID shown by Chrome or Edge. Set
`SIX_BROWSER_CAPTURE_REDIRECT_URIS` to exactly
`https://<extension-id>.chromiumapp.org/` and restart the API before pairing.
The variable is intentionally empty in generated configuration, so an unknown
extension can never pair by default. See the extension README for the stable-ID
option, local HTTP exception, permission boundary, and deterministic build.

## Updates and scaling

Create and verify a backup before changing images. Re-run the preflight, build
the new images and use `docker compose up --detach --wait`. The API image owns
schema migration on startup, so restore with the same source revision before
attempting a version upgrade.

The reference worker uses the `all` lane at low concurrency. Larger deployments
should split worker lanes only after measuring PostgreSQL connections, memory,
provider limits and queue latency. Do not expose PostgreSQL or port 8000 merely
to scale the frontend.

## Security boundary

- Images run read-only and without Linux capabilities where their software
  permits it; persistent writes are limited to named volumes and bounded tmpfs.
- The environment file must be a regular mode-0600/0400 file. Preflight rejects
  placeholder keys, unsafe public URLs, a backup path inside the checkout and
  public registration without verified mail.
- Caddy access logging is intentionally not enabled by this reference config,
  because public participation capabilities may appear in URL paths.
- Docker administrators can inspect container environments. Protect host access
  and use an external secret manager when the installation outgrows one host.
- The compose file does not itself implement firewalling, OS updates, encrypted
  offsite backup, retention deletion or incident response; the operator owns
  those controls.

See [BACKUP-RESTORE.md](BACKUP-RESTORE.md) before accepting user data.
