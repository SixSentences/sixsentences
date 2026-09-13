"""OA-first acquisition resolver: a work's OA metadata -> a ranked candidate plan.

Only open-access locations are ever emitted. There is deliberately no parameter,
branch, or fallback for paywalled fetch, credential replay (EZproxy/Shibboleth),
or scraping — principle 7 is enforced by construction, not by a downstream check.
A work with no reachable OA copy yields an empty plan with an honest reason,
which becomes a PRISMA "report not retrieved" entry.
"""

from urllib.parse import urlparse

from sixsentences_server.acquisition.models import (
    AcquisitionCandidate,
    AcquisitionPlan,
    DocumentSource,
    LegalBasis,
)
from sixsentences_server.core.models import WorkRecord

# Europe-PMC serves the OA subset's full text as JATS XML (parses with the
# stdlib extractor — no PDF backend needed).
_PMC_OA_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
# Official arXiv export host: same canonical PDF, but much more reliable for
# server-side transfers of large figure-heavy papers than the browser host.
ARXIV_PDF_URL = "https://export.arxiv.org/pdf/{arxiv_id}"

_STATUS_BASIS = {
    "gold": LegalBasis.OA_GOLD,
    "diamond": LegalBasis.OA_DIAMOND,
    "hybrid": LegalBasis.OA_HYBRID,
    "bronze": LegalBasis.OA_BRONZE,
    "green": LegalBasis.OA_GREEN,
}


def _is_arxiv(url: str) -> bool:
    """True only when the *host* is arXiv, not when the string appears anywhere.

    ``https://evil.example/arxiv.org/paper.pdf`` contains the substring but is
    not arXiv, and treating it as such would attach the wrong legal basis and
    rewrite the candidate to the arXiv export host.
    """
    host = (urlparse(url).hostname or "").lower()
    return host == "arxiv.org" or host.endswith(".arxiv.org")


def _basis_for_status(oa_status: str | None) -> LegalBasis:
    return _STATUS_BASIS.get((oa_status or "").lower(), LegalBasis.OA_GREEN)


class OpenAccessResolver:
    """Turn a work's open-access metadata into legally-groundable download candidates."""

    def plan(self, work: WorkRecord) -> AcquisitionPlan:
        candidates: list[AcquisitionCandidate] = []
        seen_urls: set[str] = set()

        def add(candidate: AcquisitionCandidate) -> None:
            url = candidate.url.strip()
            if not url or url in seen_urls:
                return
            seen_urls.add(url)
            candidates.append(candidate)

        # 1. PMC / Europe-PMC OA: published-version full text as JATS XML — the
        #    cleanest to parse, so it is tried first when present.
        if work.pmcid:
            add(
                AcquisitionCandidate(
                    url=_PMC_OA_XML.format(pmcid=work.pmcid),
                    source=DocumentSource.PMC,
                    legal_basis=LegalBasis.OA_GREEN,
                    license=work.oa_license,
                    version="publishedVersion",
                    content_hint="xml",
                )
            )

        # 2. OpenAlex/Unpaywall best OA location (publisher or repository PDF).
        #    Skip when it is just the arXiv PDF — candidate 3 covers that.
        if work.pdf_url and not _is_arxiv(work.pdf_url):
            add(
                AcquisitionCandidate(
                    url=work.pdf_url,
                    source=DocumentSource.UNPAYWALL,
                    legal_basis=_basis_for_status(work.oa_status),
                    license=work.oa_license,
                    version=work.oa_version,
                    content_hint="pdf",
                )
            )

        # 3. arXiv: the workhorse green-OA source for the CS/ML beachhead.
        if work.arxiv_id:
            add(
                AcquisitionCandidate(
                    url=ARXIV_PDF_URL.format(arxiv_id=work.arxiv_id),
                    source=DocumentSource.ARXIV,
                    legal_basis=LegalBasis.OA_GREEN,
                    license=work.oa_license or "arxiv",
                    version=work.oa_version or "submittedVersion",
                    content_hint="pdf",
                )
            )

        # 4. OA landing page as a last resort: the fetcher follows its
        #    citation_pdf_url to the actual PDF (recovers OA works OpenAlex has a
        #    landing for but no direct PDF link).
        if (
            work.oa_landing_url
            and not _is_arxiv(work.oa_landing_url)
            and work.oa_landing_url != work.pdf_url
        ):
            add(
                AcquisitionCandidate(
                    url=work.oa_landing_url,
                    source=DocumentSource.UNPAYWALL,
                    legal_basis=_basis_for_status(work.oa_status),
                    license=work.oa_license,
                    version=work.oa_version,
                    content_hint="landing",
                )
            )

        # 5. Remaining OpenAlex/Unpaywall OA locations. ``best_oa_location``
        # is often stale or blocks server traffic while a repository copy in
        # the same metadata record remains available.
        for location in work.oa_locations:
            pdf_url = str(location.get("pdf_url") or "")
            landing_url = str(location.get("landing_page_url") or "")
            license_name = location.get("license") or work.oa_license
            version = location.get("version") or work.oa_version
            if pdf_url and not _is_arxiv(pdf_url):
                add(
                    AcquisitionCandidate(
                        url=pdf_url,
                        source=DocumentSource.UNPAYWALL,
                        legal_basis=_basis_for_status(work.oa_status),
                        license=license_name,
                        version=version,
                        content_hint="pdf",
                    )
                )
            if landing_url and not _is_arxiv(landing_url):
                add(
                    AcquisitionCandidate(
                        url=landing_url,
                        source=DocumentSource.REPOSITORY,
                        legal_basis=_basis_for_status(work.oa_status),
                        license=license_name,
                        version=version,
                        content_hint="landing",
                    )
                )

        if candidates:
            return AcquisitionPlan(work_id=work.id, candidates=candidates)
        return AcquisitionPlan(
            work_id=work.id, candidates=[], reason_if_empty=self._empty_reason(work)
        )

    @staticmethod
    def _empty_reason(work: WorkRecord) -> str:
        if (work.oa_status or "").lower() == "closed":
            return "no open-access full text available (closed access)"
        if work.oa_status is None and not (work.arxiv_id or work.pmcid or work.pdf_url):
            return "no open-access metadata on this work (re-sync the corpus to populate it)"
        return "an open-access status is recorded but no direct full-text link was found"
