## Summary

<!-- What problem does this solve, and why is this the smallest useful change? -->

## Behavior and compatibility

<!--
List affected components, observable behavior, API/event/schema changes,
migrations, rollback, and compatibility impact. Use "None" where appropriate.
-->

## Validation

<!-- List exact commands and results. Explain any intentionally omitted gate. -->

- [ ] Engine checks pass, or the engine is unaffected.
- [ ] API, migration, worker, and web-contract checks pass, or they are unaffected.
- [ ] Web type-check, tests, and production build pass, or the web app is unaffected.
- [ ] Browser-extension contracts and a deployment-bound build pass, or the
      extension is unaffected.
- [ ] macOS Companion boundary check, locked resolution, tests, and release build
      pass on the pinned Xcode toolchains—or the Companion is unaffected.
- [ ] Self-hosting tests and container builds pass, or deployment is unaffected.
- [ ] User-facing behavior has a focused test or the omission is explained.

## Review boundaries

<!-- Describe concrete effects; do not check a box without reviewing it. -->

- [ ] Security and privacy effects were reviewed, including authentication,
      tenancy, capability URLs, uploads, retention, deletion, browser state, and
      logs as applicable.
- [ ] New network calls and processors are operator-configurable, fail closed,
      and document data egress, cost, retention, and failure behavior—or none
      were added.
- [ ] Dependencies and bundled assets are justified, locked, and
      redistribution-compatible—or none were added.
- [ ] Native-client changes include explicit origin, local-retention/deletion,
      permission, signing, update, and binary-distribution implications.
- [ ] Research-method assumptions, limitations, and provenance remain visible—or
      no research-facing behavior changed.
- [ ] Accessibility and keyboard behavior were reviewed for UI changes—or no UI
      changed.
- [ ] Browser permissions, capture bounds, pairing callbacks, extension storage,
      and generated host access were reviewed—or the extension is unaffected.

## Source-release hygiene

- [ ] No secret, private key, production configuration, customer/participant
      data, user upload, database dump, log, private prompt, or non-redistributable
      research content is included.
- [ ] The change belongs in the community stack; payment, subscription,
      commercial-plan, hosted-administration, and marketing-site code remains
      separate.
- [ ] Public behavior and limitations are documented.
- [ ] `CHANGELOG.md` is updated for user-visible changes, or the omission is
      explained above.
- [ ] Every commit carries my own matching DCO `Signed-off-by` trailer.
- [ ] I have read `CLA.md` and posted its exact acceptance sentence as a
      standalone pull-request comment.
- [ ] I have read and will follow the Code of Conduct.

## Visual evidence

<!-- Add sanitized screenshots/recordings for material UI changes. No real data. -->
