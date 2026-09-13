# SixSentences Companion for macOS

The Companion is the native macOS client for spoken live interviews, private
brainstorm capture, and paper chat in a self-hosted SixSentences deployment. It
uses local Apple Speech for transcription and sends finalized text—not raw
audio—to the API at the deployment origin selected at build time.

> **Alpha source release.** This directory contains reviewable source and tests.
> It does not contain a signed or notarized application, an update feed, store
> package, certificate, provisioning profile, private key, or operator secret.

## Capabilities

- captures microphone and ScreenCaptureKit system audio only after an explicit
  participant-notification confirmation;
- transcribes both channels with on-device Apple Speech and never records video;
- keeps a bounded, idempotent local queue for finalized transcript segments;
- supports grounded live questions and private microphone-only brainstorms;
- opens local PDFs, uploads them to the configured deployment, and streams
  grounded paper-chat answers;
- pairs through a browser with PKCE S256 and stores the resulting scoped bearer
  credential in macOS Keychain; and
- presents an ordinary Dock app, menu-bar control, and visible recording state.

Speaker labels represent capture channels (`You` and `Others`), not speaker
identification. Protected media can be unavailable to ScreenCaptureKit, and
on-device language support depends on the installed macOS speech assets.

## Requirements

- macOS 14 or later;
- Xcode 16.4 / Swift 5.10 or later (Xcode 26.1.1 additionally enables the
  macOS 26 SpeechAnalyzer path);
- a checked-out copy of this repository;
- a self-hosted SixSentences origin whose API exposes the Companion contracts;
- `rsvg-convert` from librsvg, unless you supply the optional original
  `AppBundle/AppIcon-1024.png` fallback locally; and
- on-device Apple Speech support for the chosen recognition language.

Sparkle is the only Swift package dependency. Both `Package.swift` and
`Package.resolved` pin version `2.9.6` and revision
`ac2def288cbff5cfc7df3ffef6abdf45b72bcb0a` exactly. Its complete notices are
bundled in `AppBundle/ThirdPartyNotices.txt` and summarized in the repository
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).

## Build and test

Run package tests and the community boundary check from the repository root:

```console
swift package resolve --package-path apps/companion-macos
swift test --package-path apps/companion-macos --disable-sandbox
bash apps/companion-macos/Scripts/audit-community-source.sh
```

Build an ad-hoc application bundle for an HTTPS deployment:

```console
SIX_COMMUNITY_ORIGIN=https://research.example.org \
  apps/companion-macos/Scripts/build-app.sh
```

The output is ignored at
`apps/companion-macos/dist/SixSentences Companion.app`. The build copies the
public mark from `docs/assets/sixsentences-mark.svg` and injects one
`SixSentencesCommunityOrigin` into the bundle. The API URL is always derived as
`<origin>/api`; the web and API origins cannot be mixed. Credentials, paths,
queries, and fragments are rejected. Release builds require HTTPS. Debug builds
additionally allow `http://localhost`, `http://127.0.0.1`, and `http://[::1]`.
There is no production or hosted-service fallback.

`Scripts/install-local.sh` builds and copies the ad-hoc app into the current
user's `~/Applications` directory. Pass `SIX_COMMUNITY_ORIGIN` through to that
script as shown above. Ad-hoc builds are for local development and are not an
official binary release.

## Pairing and API boundary

The app opens `<origin>/interviews` with a random PKCE challenge and state. It
does not send the Mac hostname, account name, or a device name in the URL,
request history, or logs. The custom callback contains only a one-use code and
state; the verifier stays in Keychain and is accepted for ten minutes. Browser
links returned for interviews, brainstorms, and paper chats are opened only if
their scheme, host, and effective port exactly match the configured origin.

The API client calls these deployment-owned route families under the derived
`/api` prefix:

- `/interviews/live/config`, `/interviews/live/pair/exchange`;
- `/interviews/live/sessions/*` for creation, finalized segments, questions,
  completion, cancellation, and brainstorm receipts; and
- `/companion/paper-chats/*` for PDF creation, history, turns, cancellation,
  and server-sent-event streaming.

Requests use an ephemeral URL session without cookies, credential storage, or
cache. Redirects are refused so authorization cannot cross origins. Response
sizes are bounded. Server diagnostics are parsed only for stable error codes;
raw server detail is never rendered in the UI.

## Local data, retention, and deletion

Raw audio and PDF bytes exist only in process memory. PDF bytes are uploaded
directly to the configured deployment and are not written by this client.
Paper history, current answers, selections, and brainstorm results are also
memory-only.

The app writes four crash-recovery files below
`~/Library/Application Support/SixSentencesCompanion`:

| Store | Content | Normal lifetime |
|---|---|---|
| `finalized-segment-outbox-v1.json` | finalized transcript text and event IDs | until acknowledged, cancelled, or disconnected |
| `session-recovery-v1.json` | session IDs, web URL, and completion state | until completion, cancellation, or disconnect |
| `pending-asks-v1.json` | question, request ID, and transcript cutoff | until a terminal receipt or disconnect |
| `pending-brainstorms-v2.json` | account-bound IDs and synthesis settings; no transcript text | until a terminal receipt or disconnect |

The directory is set to POSIX `0700`, files to `0600`, and atomic writes request
Foundation's complete-file-protection option. These controls are not a claim of
hardware-backed encryption on every Mac; enable FileVault for encrypted storage
at rest. Deletion removes filesystem entries but cannot promise forensic secure
erase on APFS or SSD media.

**Disconnect this Mac** cancels client work, waits for queued persistence, then
purges all four stores—including unreadable/corrupt files—clears account-bound
preferences and in-memory transcript, paper, and brainstorm state, deletes the
pending PKCE item, and deletes the bearer credential. If any deletion fails,
the UI reports failure instead of claiming success. The current API has no
authenticated account-deletion push callback; operators must invalidate the
credential server-side and users must disconnect the Mac to request immediate
local deletion. Server-side PDF, transcript, interview, and account retention
is configured by the self-hosted deployment, not this client.

Non-account preferences such as interface and transcription language remain in
`UserDefaults` after disconnect. Window position is managed by macOS window
restoration.

## Security and release boundary

The app requests microphone, speech-recognition, and screen/system-audio
permissions. It does not request camera or Accessibility access, and the audio
capture path registers no screen/video output. The overlay deliberately remains
visible during screen sharing.

Run `Scripts/audit-community-source.sh` before review. It rejects tracked build
artifacts and signing material, hosted network origins, hostname/device-name
collection, commercial-service coupling, missing contract coverage, unpinned
Sparkle, missing notices, and invalid shell/plist files.

The first public release is source-only. Developer ID signing, notarization,
Sparkle appcast publication, release ZIP/DMG generation, and update provenance
are a separate reviewed follow-up. No signing or notarization secret is needed
by pull-request CI, and no binary produced by `build-app.sh` is a supported
distribution artifact.

CI exercises both the legacy on-device recognizer with Xcode 16.4 and the
macOS 26 SpeechAnalyzer path with Xcode 26.1.1. Swift CodeQL and the tagged
source-release gate use Xcode 26.1.1 so the newer path is included in analysis.

See the repository [security policy](../../SECURITY.md),
[contribution guide](../../CONTRIBUTING.md), and
[trademark policy](../../TRADEMARKS.md) before redistributing a modified build.
