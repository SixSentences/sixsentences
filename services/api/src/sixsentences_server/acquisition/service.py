"""Run-level acquisition: resolve -> fetch -> extract -> store, with an audit ledger.

``acquire_for_run`` runs the acquisition stage over the works a run included: for
each it tries the open-access candidates in order, stores the first that
downloads, extracts text when the type allows, writes a ``DocumentRow`` (the
ledger) and tallies the outcome. A work with no reachable OA copy is recorded as
``not_retrieved`` with an honest reason — the PRISMA "reports not retrieved"
entry. The per-work audit is the ledger table itself; the pipeline emits one
summary event for the stage.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.arxiv import ArxivClient, ArxivFinder, ArxivMatch
from sixsentences_server.acquisition.extract import TextExtractor
from sixsentences_server.acquisition.fetch import DocumentFetcher, HttpxFetcher
from sixsentences_server.acquisition.grobid import GrobidTextExtractor
from sixsentences_server.acquisition.identity import (
    IdentityStatus,
    verify_document_identity,
)
from sixsentences_server.acquisition.landing import pdf_urls_from_html
from sixsentences_server.acquisition.models import (
    AcquisitionCandidate,
    AcquisitionResult,
    AcquisitionStatus,
    AcquisitionSummary,
    DocumentSource,
    FetchedBlob,
    LegalBasis,
    TextStatus,
)
from sixsentences_server.acquisition.pdf import PdfTextExtractor
from sixsentences_server.acquisition.resolver import ARXIV_PDF_URL, OpenAccessResolver
from sixsentences_server.acquisition.store import DocumentStore, LocalDocumentStore
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import DocumentRow, Org, Run
from sixsentences_server.core.entitlements import (
    EntitlementError,
    check_storage_available,
)
from sixsentences_server.core.models import WorkRecord


class Acquirer(Protocol):
    def acquire(self, work: WorkRecord) -> AcquisitionResult: ...


def _is_html(blob: FetchedBlob) -> bool:
    ctype = blob.content_type.lower()
    if "html" in ctype:
        return True
    if "pdf" in ctype or "xml" in ctype:
        return False
    head = blob.content[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


class AcquisitionService:
    """Compose a resolver, fetcher, extractor and store into one acquire() step."""

    def __init__(
        self,
        *,
        resolver: OpenAccessResolver,
        fetcher: DocumentFetcher,
        extractor: TextExtractor,
        store: DocumentStore,
        arxiv: ArxivFinder | None = None,
    ) -> None:
        self.resolver = resolver
        self.fetcher = fetcher
        self.extractor = extractor
        self.store = store
        self.arxiv = arxiv  # optional title-match fallback for closed/no-OA works

    def _fetch(self, cand: AcquisitionCandidate) -> list[FetchedBlob]:
        """Fetch a candidate and enumerate usable documents from a landing page.

        HTML that cannot be resolved to a PDF is deliberately dropped rather
        than stored as if it were the paper. Every discovered PDF is retained
        so an incorrect first metadata link cannot hide a valid repository copy
        from the same landing page.
        """
        blob = self.fetcher.fetch(cand.url)
        if blob is None:
            return []
        if not _is_html(blob):
            return [blob]  # a PDF / XML / text document — the real thing
        documents: list[FetchedBlob] = []
        for pdf in pdf_urls_from_html(blob.content, blob.final_url):
            if pdf == cand.url:
                continue
            pdf_blob = self.fetcher.fetch(pdf)
            if pdf_blob is not None and not _is_html(pdf_blob):
                documents.append(pdf_blob)
        return documents

    def _download(
        self,
        work: WorkRecord,
        cand: AcquisitionCandidate,
        *,
        note: str | None = None,
        rejected: list[str] | None = None,
    ) -> AcquisitionResult | None:
        """Fetch + store + extract one candidate; None if it did not yield a doc."""
        blobs = self._fetch(cand)
        if not blobs:
            if rejected is not None:
                rejected.append(f"{cand.source.value}: download failed")
            return None
        for blob in blobs:
            extracted = self.extractor.extract(blob.content, blob.content_type)
            identity_note = ""
            if extracted.status is TextStatus.PARSED and extracted.text:
                identity = verify_document_identity(work, extracted.text)
                identity_note = (
                    f"identity {identity.status.value}; title coverage "
                    f"{identity.title_coverage:.0%}"
                )
                if identity.status is IdentityStatus.MISMATCH:
                    if rejected is not None:
                        rejected.append(
                            f"{cand.source.value}: {identity.reason} "
                            f"(title coverage {identity.title_coverage:.0%})"
                        )
                    continue
            checksum, path = self.store.put(blob.content)
            if extracted.status is TextStatus.PARSED and extracted.text:
                self.store.put_text(checksum, extracted.text)
            provenance_note = "; ".join(part for part in (note, identity_note) if part)
            return AcquisitionResult(
                work_id=work.id,
                status=AcquisitionStatus.RETRIEVED,
                source=cand.source,
                legal_basis=cand.legal_basis,
                license=cand.license,
                version=cand.version,
                url=blob.final_url or cand.url,
                content_type=blob.content_type,
                checksum=checksum,
                byte_size=blob.byte_size,
                storage_path=path,
                text_status=extracted.status,
                note=provenance_note or None,
            )
        return None

    def acquire(self, work: WorkRecord) -> AcquisitionResult:
        plan = self.resolver.plan(work)
        rejected: list[str] = []
        for cand in plan.candidates:
            result = self._download(work, cand, rejected=rejected)
            if result is not None:
                return result
        # No OA copy from OpenAlex metadata (closed / dead links) — last resort:
        # look for a green-OA arXiv preprint of the same paper by title.
        fallback = self._arxiv_fallback(work, rejected=rejected)
        if fallback is not None:
            return fallback
        reason = plan.reason_if_empty
        if rejected:
            reason = "all open-access candidates failed: " + "; ".join(rejected[:8])
        return AcquisitionResult(
            work_id=work.id,
            status=AcquisitionStatus.NOT_RETRIEVED,
            reason=reason or "all open-access candidates failed to download",
            text_status=TextStatus.NOT_RETRIEVED,
        )

    def _arxiv_fallback(
        self, work: WorkRecord, *, rejected: list[str] | None = None
    ) -> AcquisitionResult | None:
        if self.arxiv is None or not work.title:
            return None
        match = self.arxiv.find(work.title, work.year)
        if match is None:
            # Corpus rows occasionally inherit a much newer repository year.
            # The normal fuzzy fallback must keep its strict year gate, but a
            # near-exact title match is strong enough to recover the original
            # arXiv record independently of that suspicious year.
            exact_resolver = getattr(self.arxiv, "resolve_exact_title", None)
            entry = exact_resolver(work.title) if callable(exact_resolver) else None
            if entry is not None:
                match = ArxivMatch(
                    arxiv_id=entry.arxiv_id,
                    score=1.0,
                    matched_title=entry.title,
                )
        if match is None:
            return None
        cand = AcquisitionCandidate(
            url=ARXIV_PDF_URL.format(arxiv_id=match.arxiv_id),
            source=DocumentSource.ARXIV,
            legal_basis=LegalBasis.OA_GREEN,
            license="arxiv",
            version="submittedVersion",
            content_hint="pdf",
        )
        return self._download(
            work,
            cand,
            note=f"arxiv title-match {match.score} (fallback)",
            rejected=rejected,
        )


def default_acquisition_service() -> AcquisitionService:
    settings = get_settings()
    extractor: TextExtractor = PdfTextExtractor()
    if settings.grobid_url:  # structured TEI when a GROBID service is configured
        extractor = GrobidTextExtractor(grobid_url=settings.grobid_url, fallback=PdfTextExtractor())
    return AcquisitionService(
        resolver=OpenAccessResolver(),
        fetcher=HttpxFetcher(mailto=settings.openalex_mailto),
        extractor=extractor,
        store=LocalDocumentStore(settings.documents_dir),
        arxiv=ArxivClient(),
    )


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def acquire_for_run(
    session: Session,
    run: Run,
    works: Sequence[WorkRecord],
    *,
    service: Acquirer,
    on_progress: Callable[[int, int, AcquisitionSummary], None] | None = None,
) -> AcquisitionSummary:
    """Acquire full text for ``works``, persist the ledger, return the summary.

    ``sought`` always describes the complete title/abstract-included set, even
    after a worker resume. The optional progress callback runs after durable
    ledger rows have been staged, allowing the pipeline to publish checkpoints
    while a long acquisition pass is still in progress.
    """
    target_ids = {work.id for work in works}
    existing_rows = session.scalars(
        select(DocumentRow)
        .where(DocumentRow.run_id == run.id, DocumentRow.work_id.in_(target_ids))
        .order_by(DocumentRow.id)
    ).all()
    existing = {row.work_id: row for row in existing_rows}
    pending = [work for work in works if work.id not in existing]
    summary = AcquisitionSummary(sought=len(works))
    for row in existing.values():
        if row.status != AcquisitionStatus.RETRIEVED.value:
            summary.not_retrieved += 1
            continue
        summary.retrieved += 1
        if row.legal_basis:
            _bump(summary.by_legal_basis, row.legal_basis)
        if row.source:
            _bump(summary.by_source, row.source)
        if row.text_status == TextStatus.PARSED.value:
            summary.parsed += 1
        elif row.text_status == TextStatus.STORED_UNPARSED.value:
            summary.stored_unparsed += 1

    completed = len(existing)
    if on_progress is not None:
        on_progress(completed, len(works), summary)
    org = session.get(Org, run.org_id)
    assert org is not None
    for work in pending:
        try:
            # The real checksum is known after download; this provisional
            # value avoids any network work once the PDF-count cap is full.
            check_storage_available(
                session,
                org,
                0,
                document_checksum=f"pending-{work.id}",
            )
        except EntitlementError as exc:
            session.add(
                DocumentRow(
                    org_id=run.org_id,
                    run_id=run.id,
                    project_id=run.project_id,
                    work_id=work.id,
                    status=AcquisitionStatus.NOT_RETRIEVED.value,
                    byte_size=0,
                    text_status=TextStatus.NOT_RETRIEVED.value,
                    reason=str(exc),
                )
            )
            summary.not_retrieved += 1
            completed += 1
            if on_progress is not None:
                on_progress(completed, len(works), summary)
            continue
        result = service.acquire(work)
        retrieved = result.status is AcquisitionStatus.RETRIEVED
        if retrieved and result.checksum:
            try:
                check_storage_available(
                    session,
                    org,
                    result.byte_size,
                    document_checksum=result.checksum,
                )
            except EntitlementError as exc:
                # The acquirer has already written the content-addressed blob.
                # Remove it only when no workspace references the checksum.
                referenced = session.scalar(
                    select(DocumentRow.id).where(DocumentRow.checksum == result.checksum)
                )
                if referenced is None:
                    settings = get_settings()
                    (
                        settings.documents_dir / "blobs" / result.checksum[:2] / result.checksum
                    ).unlink(missing_ok=True)
                    (settings.documents_dir / "text" / f"{result.checksum}.txt").unlink(
                        missing_ok=True
                    )
                session.add(
                    DocumentRow(
                        org_id=run.org_id,
                        run_id=run.id,
                        project_id=run.project_id,
                        work_id=work.id,
                        status=AcquisitionStatus.NOT_RETRIEVED.value,
                        byte_size=0,
                        text_status=TextStatus.NOT_RETRIEVED.value,
                        reason=str(exc),
                    )
                )
                summary.not_retrieved += 1
                completed += 1
                if on_progress is not None:
                    on_progress(completed, len(works), summary)
                continue
        session.add(
            DocumentRow(
                org_id=run.org_id,
                run_id=run.id,
                project_id=run.project_id,
                work_id=work.id,
                status=result.status.value,
                source=result.source.value if result.source else None,
                legal_basis=result.legal_basis.value if result.legal_basis else None,
                license=result.license,
                version=result.version,
                url=result.url,
                content_type=result.content_type,
                checksum=result.checksum,
                byte_size=result.byte_size,
                storage_path=result.storage_path,
                text_status=result.text_status.value,
                reason=result.reason or result.note,
                retrieved_at=datetime.now(UTC) if retrieved else None,
            )
        )
        if not retrieved:
            summary.not_retrieved += 1
            completed += 1
            if on_progress is not None:
                on_progress(completed, len(works), summary)
            continue
        summary.retrieved += 1
        if result.note and "fallback" in result.note:
            summary.fallback_found += 1
        if result.legal_basis:
            _bump(summary.by_legal_basis, result.legal_basis.value)
        if result.source:
            _bump(summary.by_source, result.source.value)
        if result.text_status is TextStatus.PARSED:
            summary.parsed += 1
        elif result.text_status is TextStatus.STORED_UNPARSED:
            summary.stored_unparsed += 1
        completed += 1
        if on_progress is not None:
            on_progress(completed, len(works), summary)
    session.flush()
    return summary
