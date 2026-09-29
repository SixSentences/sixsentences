# Personal pinboard

The new-search homepage's background is a quiet, framed felt pinboard: warm stone
in light mode and muted charcoal in dark mode. The original
greeting and composer layout stays in front; there is no separate board section.
Its notes never intercept the search input and are not implicit assistant context.
The existing cursor-wave component remains reusable; the homepage uses the board.

## Interaction

- The board follows the existing application theme preference and its settings
  control. There is no separate board theme switch or reset of a saved/system theme.
- Pin up to 24 personal notes, each with at most 2,000 Unicode code points.
- Choose one of five paper colours and three shapes; adjust the tilt.
- Left- or right-click empty background to open a small creation popup. The new
  note is pinned at that position, clamped to the available canvas edges.
- Click a note to edit it. Drag its paper or handle to move it; a movement
  threshold distinguishes dragging from editing and prevents creation popups.
- Focus a note's handle and use the arrow keys for keyboard positioning.
  Shift + arrow makes a larger move. Positions are normalized to available travel.
- The unobtrusive **Notes** button is also available to keyboard and touch users.
  It exposes every note, including notes behind foreground content on a narrow
  screen, without rearranging or clearing their saved positions.
- Edit a note in the labelled modal, then apply it. Deleting requires a second
  explicit confirmation. The global status indicates when the API has saved it.

## Data and concurrency boundary

`GET /auth/pinboard` and `PUT /auth/pinboard` use the authenticated account.
The payload is `{ revision, notes }`; the server owns the revision and returns
409 for a stale write. See the core service's pinboard route/schema for validation,
account isolation, data export and erasure.

`PinboardProvider` lives above authenticated route content so drafts survive
internal navigation. It debounces and serializes writes. A failed save retains
the in-memory draft and exposes **Retry**. A conflict shows both versions and
requires an explicit choice before either version is replaced. A further
concurrent edit produces another conflict; it is not force-overwritten.

There is no note content in localStorage or sessionStorage. Unsaved drafts exist
only for the current browser session, with a tab-close warning; they are not an
offline backup. Logout or a token change cancels pending requests and immediately
clears the board's state. Late responses cannot restore the previous account's
data. Persisted notes are private to the account, not shared project records and
not sent to model providers by the composer.

## Checks

Run `npm run test:pinboard`, `npm run typecheck`, and the production build.
CI includes the pinboard tests through `node --test tests/*.test.mjs`.
Behavioral tests cover read/write validation, serialized saves, concurrent tabs,
lost receipts, stale requests after logout, revision limits, Unicode text and
keyboard/drag layout geometry. Browser QA uses a synthetic local account and an
isolated local API, never production user notes.
