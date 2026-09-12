# Security policy

## Supported versions

SixSentences Engine is pre-1.0 software. Security fixes are applied to the
latest alpha release and the `main` branch. Older alpha releases may not receive
backports.

| Version | Security fixes |
| --- | --- |
| Latest `0.1.x` alpha | Supported on a best-effort basis |
| Older snapshots | Not supported |

## Report a vulnerability privately

Do not disclose a suspected vulnerability in a public issue, discussion, pull
request, or test fixture.

Use the repository's **Security** tab and select **Report a vulnerability** to
open a private GitHub security advisory:

<https://github.com/SixSentences/sixsentences/security/advisories/new>

Include, where safe:

- the affected version or commit;
- a description of the impact and attack preconditions;
- minimal reproduction steps or a proof of concept;
- suggested mitigations, if known; and
- whether you plan to publish details and on what timeline.

Remove credentials, personal data, copyrighted research material, and unrelated
production information from the report. If GitHub private vulnerability
reporting is temporarily unavailable, email `legal@sixsentences.com` with the
subject `CONFIDENTIAL: SixSentences Engine security`. Do not disclose the
vulnerability or ask for a contact channel in a public issue.

Maintainers aim to acknowledge reports within five business days, then assess
severity, coordinate a fix, and agree on a disclosure plan. This is a target,
not a service-level guarantee. The project does not currently operate a bug
bounty program.

## Scope

This policy covers code distributed from this repository. The separately
operated hosted service, website, accounts, and deployment infrastructure are
not represented by this source tree; use the security contact published by the
relevant service for those systems.

Good-faith research should use local or explicitly authorized systems, minimize
data access, avoid privacy violations and service disruption, and stop once a
vulnerability is demonstrated. Do not test the production service without
explicit prior authorization.
