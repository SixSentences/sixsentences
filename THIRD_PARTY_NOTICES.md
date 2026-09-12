# Third-party notices

SixSentences Engine depends on open-source packages distributed under their
own licenses. Direct runtime dependencies currently include:

| Project | Purpose | License |
| --- | --- | --- |
| [DuckDB](https://github.com/duckdb/duckdb) | Local analytical storage and search | MIT |
| [HTTPX](https://github.com/encode/httpx) | HTTP client for public data connectors | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | Models and validation | MIT |

The release includes `uv.lock`, which records hashes and exact versions for the
runtime and development environments. The isolated build backend is separately
and exactly pinned in `pyproject.toml`. The locked runtime transitive closure at
`0.1.0-alpha.1` is:

| Project | License |
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

Development dependencies are recorded in both `pyproject.toml` and the lockfile;
the build dependency is recorded in `pyproject.toml`. Distributors remain
responsible for reviewing the complete dependency closure and license texts for
the exact artifacts they ship, especially after updating dependency pins.

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

No hosted-service source, website assets, commercial fonts, production data,
or proprietary datasets are included in this repository.
