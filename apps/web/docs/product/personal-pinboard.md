# Personal pinboard

The new-search homepage has a functional paper board beneath the composer.
Its notes never overlap the search input and are not implicit assistant context.
The existing cursor-wave component remains reusable; the homepage uses the board.

## Interaction

- Pin up to 24 personal notes, each with at most 2,000 Unicode code points.
- Choose one of five paper colours and three shapes; adjust the tilt.
- On wide screens, drag a note's handle or focus it and use the arrow keys.
  Shift + arrow makes a larger move. Positions are normalized to available travel.
- Small screens use a stacked layout; desktop positions are preserved.
- **Tidy board** arranges notes without changing their content or appearance.
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
