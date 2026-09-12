# Third-party notices

Except for separately identified marks and third-party material, SixSentences
source code and documentation are licensed under Apache-2.0. Dependencies,
adapted components, and data obtained through connectors retain their own
licenses and terms.

The lockfiles are the canonical inventories for the exact source revision:

- `uv.lock` records the Python runtime and development environment; and
- `apps/web/package-lock.json` records the web runtime, build, development,
  and optional platform dependency graph.

A lockfile entry does not by itself prove that a package or optional native
binary is present in a particular wheel, source distribution, web build,
container, or operating-system installation. Distributors must inspect the
actual artifact they ship and comply with the corresponding license terms.

## Python engine

Direct Python runtime dependencies currently include:

| Project | Purpose | License |
| --- | --- | --- |
| [DuckDB](https://github.com/duckdb/duckdb) | Local analytical storage and search | MIT |
| [HTTPX](https://github.com/encode/httpx) | HTTP client for explicit network connectors | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | Models and validation | MIT |

The locked runtime transitive closure for `v0.1.0-alpha.1` also records:

| Project | License identifier recorded by the project |
| --- | --- |
| annotated-types | MIT |
| AnyIO | MIT |
| certifi | MPL-2.0 |
| h11 | MIT |
| HTTPCore | BSD-3-Clause |
| idna | BSD-3-Clause |
| pydantic-core | MIT |
| typing-extensions | PSF-2.0 |
| typing-inspection | MIT |

Development dependencies are recorded in `pyproject.toml` and `uv.lock`;
the isolated build backend is exactly pinned in `pyproject.toml`.

## Web client

The web client uses the npm packages pinned by
`apps/web/package-lock.json`. Notable direct projects include Next.js and
React, TanStack Query, CodeMirror, React PDF, Radix UI, Motion, Lucide,
Tailwind CSS, TypeScript, and shadcn/ui. Their upstream licenses are not replaced
by the repository's Apache-2.0 license.

### shadcn/ui-generated and adapted components

Portions of `apps/web/src/components/ui` were generated from or adapted from
[shadcn/ui](https://github.com/shadcn-ui/ui). They retain this MIT notice:

> MIT License
>
> Copyright (c) 2023 shadcn
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### Optional native and build packages

The npm lockfile for this release records:

- platform-specific optional `@img/sharp-libvips-*` packages at version
  `1.3.3` with the license identifier `LGPL-3.0-or-later`, reached through
  the optional `sharp` dependency graph; and
- `lightningcss` and platform-specific `lightningcss-*` packages at version
  `1.32.0` with the license identifier `MPL-2.0` (Mozilla Public License
  2.0) in the development/build graph.

These statements describe the committed lockfile, not the contents of every
platform installation or compiled deployment. Before distributing a built
application or container, determine which optional packages and native
libraries it actually contains and include the notices, license texts, and
other materials required for that artifact.

The source repository does not commit `node_modules`, a `.next` build, or
platform-native npm binaries. `npm ci` obtains dependencies described by the
lockfile for the selected platform.

## Browser extension

`apps/browser-extension` has no third-party runtime or development package
dependencies. Its generated unpacked directory contains project source, the
package `LICENSE` and `NOTICE`, deployment-bound configuration, and the four
project-owned PNG icons listed in `TRADEMARKS.md`. Browser software and APIs are
provided by the user's Chromium-based browser and are not redistributed here.

## Public-data connectors

The repository contains connector code for third-party data services. It does
not redistribute either dataset:

- **OpenAlex.** OpenAlex publishes its scholarly metadata under
  [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). OpenAlex asks
  users to cite: Priem, J., Piwowar, H., & Orr, R. (2022). *OpenAlex: A
  fully-open index of scholarly works, authors, venues, institutions, and
  concepts*. arXiv:2205.01833. See the
  [official citation guidance](https://help.openalex.org/how-to/citing-openalex/)
  and [data-license statement](https://help.openalex.org/access/pricing/#free-data-paid-services).
- **Retraction Watch Database via Crossref.** The connector downloads the
  Crossref-hosted daily CSV from the
  [official repository](https://gitlab.com/crossref/retraction-watch-data).
  Crossref lists the Retraction Watch Database as
  [CC0](https://www.crossref.org/documentation/retrieve-metadata/#licensing).
  Retraction Watch requests this citation when the data is used: *The
  Retraction Watch Database [Internet]. New York: The Center for Scientific
  Integrity. 2018. ISSN: 2692-4579. [Cited applicable date]. Available from:
  http://retractiondatabase.org/.* See the
  [official user guide](https://retractionwatch.com/retraction-watch-database-user-guide/)
  and [Crossref access documentation](https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/).

Data downloaded through a connector remains subject to the source's current
terms, attribution requests, schema, and rate limits. Callers should record the
retrieval date and dataset revision used for research outputs.

## Material intentionally not distributed

The repository does not include the marketing site, SaaS backend, production
data, provider credentials, historical application ZIPs, signed companion
binaries, prebuilt browser-store packages, or proprietary corpora. Lottie animations
and locally bundled font files whose exact redistribution provenance was not
established for this release were excluded rather than relicensed.
