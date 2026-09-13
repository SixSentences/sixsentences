"""Strong-identity, fill-only Library paper enrichment."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import sixsentences_server.jobs as jobs
from sixsentences_server.api.app import _execute_paper_enrichment, create_app
from sixsentences_server.config import get_settings
from sixsentences_server.core.auth import add_member, authenticate
from sixsentences_server.core.db import (
    BackgroundJobRow,
    BrowserCapturedPaperMetadataRow,
    DocumentRow,
    PaperEnrichmentRow,
    User,
    WorkRow,
    db_session,
    init_db,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.documents.enrichment import (
    PaperEnrichmentError,
    StrongPaperIdentity,
    discover_missing_metadata,
    metadata_value_is_missing,
)


class _OpenAlexStub:
    calls = 0

    def __init__(self, **_: object) -> None:
        pass

    def get_work(self, external_id: str) -> WorkRecord | None:
        type(self).calls += 1
        assert external_id == "doi:10.5555/exact-paper"
        return WorkRecord(
            id="W123456",
            doi="10.5555/exact-paper",
            title="Exact Paper",
            authors=["Ada Researcher", "Grace Reviewer"],
            abstract="A verified abstract from the exact public record.",
            year=2025,
            publication_date="2025-04-03",
            venue="Journal of Exact Records",
            volume="12",
            issue="3",
            pages="40-52",
            language="en",
            work_type="journal-article",
            oa_status="green",
            oa_landing_url="https://repository.example.org/exact-paper",
            pdf_url="https://repository.example.org/exact-paper.pdf",
            oa_license="cc-by",
        )


class _CrossrefStub:
    calls = 0

    def __init__(self, **_: object) -> None:
        pass

    def get_work(self, doi: str) -> dict[str, Any] | None:
        type(self).calls += 1
        assert doi == "10.5555/exact-paper"
        return {
            "DOI": doi,
            "title": ["Exact Paper"],
            "container-title": ["Journal of Exact Records"],
            "publisher": "Exact Society",
            "volume": "12",
            "issue": "3",
            "page": "40-52",
            "published-online": {"date-parts": [[2025, 4, 3]]},
            "type": "journal-article",
            "URL": f"https://doi.org/{doi}",
        }


def _register(client: TestClient, email: str, org: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "StrongPass123!",
            "org_name": org,
            "name": "Researcher",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def _seed_paper(
    email: str,
    *,
    doi: str = "10.5555/exact-paper",
    work_id: str = "W123456",
    manual_fields: list[str] | None = None,
) -> int:
    source_url = f"https://example.org/papers/{work_id.casefold()}"
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        work = WorkRow(
            id=work_id,
            doi=doi or None,
            title="Exact Paper",
            year=2025,
            payload={"authors": []},
        )
        session.add(work)
        session.flush()
        document = DocumentRow(
            org_id=user.org_id,
            work_id=work.id,
            status="not_retrieved",
            source="browser_capture",
            legal_basis="user_supplied_link",
            url=source_url,
            byte_size=0,
            text_status="not_retrieved",
            reason="Citation only",
        )
        session.add(document)
        session.flush()
        session.add(
            BrowserCapturedPaperMetadataRow(
                org_id=user.org_id,
                user_id=user.id,
                document_id=document.id,
                title="Exact Paper",
                authors=[],
                doi=doi,
                doi_identity=doi or None,
                published_at=None,
                description="",
                source_url=source_url,
                canonical_url=source_url,
                canonical_url_hash=hashlib.sha256(source_url.encode()).hexdigest(),
                provenance={"manual_fields": manual_fields or []},
            )
        )
        session.flush()
        return document.id


def test_exact_identity_job_previews_then_explicitly_applies_fill_only_metadata(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    init_db()
    _OpenAlexStub.calls = 0
    _CrossrefStub.calls = 0
    monkeypatch.setattr("sixsentences_server.api.app.OpenAlexClient", _OpenAlexStub)
    monkeypatch.setattr("sixsentences_server.api.app.CrossrefClient", _CrossrefStub)
    app = create_app()
    client = TestClient(app)
    token = _register(client, "enrich@example.org", "Enrichment Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper("enrich@example.org")

    started = client.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:stable-request-1"},
    )
    assert started.status_code == 202, started.text
    receipt_id = started.json()["id"]
    latest = client.get(f"/documents/{document_id}/enrichments/latest")
    assert latest.status_code == 200
    job = latest.json()["job"]
    assert job["id"] == receipt_id
    assert job["status"] == "completed"
    fields = {proposal["field"] for proposal in job["suggestions"]}
    assert {"abstract", "authors", "publisher"} <= fields
    assert "title" not in fields and "doi" not in fields
    assert {source["provider"] for source in job["sources"]} == {
        "crossref",
        "openalex",
    }
    assert all(proposal["current"] is None for proposal in job["suggestions"])

    # A transport retry with the same key replays the receipt before consuming
    # another provider/rate-limit slot.
    replay = client.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:stable-request-1"},
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == receipt_id
    assert _OpenAlexStub.calls == 1 and _CrossrefStub.calls == 1

    applied = client.post(
        f"/documents/{document_id}/enrichments/{receipt_id}/apply",
        json={"expected_revision": job["source_revision"]},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["job"]["status"] == "applied"
    assert "abstract" in applied.json()["changed_fields"]
    library = client.get("/documents").json()
    paper = next(item for item in library if item["id"] == document_id)
    assert paper["metadata"]["abstract"].startswith("A verified abstract")
    assert paper["metadata_provenance"]["abstract"]["source"] == "paper_enrichment"
    assert paper["metadata"]["title"] == "Exact Paper"


def test_manual_clear_tombstone_blocks_a_provider_proposal(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    init_db()
    monkeypatch.setattr("sixsentences_server.api.app.OpenAlexClient", _OpenAlexStub)
    monkeypatch.setattr("sixsentences_server.api.app.CrossrefClient", _CrossrefStub)
    client = TestClient(create_app())
    token = _register(client, "tombstone@example.org", "Tombstone Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper(
        "tombstone@example.org",
        manual_fields=["abstract"],
    )

    response = client.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:tombstone-1"},
    )
    assert response.status_code == 202
    job = client.get(f"/documents/{document_id}/enrichments/latest").json()["job"]
    assert "abstract" not in {item["field"] for item in job["suggestions"]}


def test_title_only_paper_is_rejected_and_receipt_is_creator_org_private(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    init_db()
    monkeypatch.setattr("sixsentences_server.api.app.OpenAlexClient", _OpenAlexStub)
    monkeypatch.setattr("sixsentences_server.api.app.CrossrefClient", _CrossrefStub)
    app = create_app()
    owner = TestClient(app)
    owner_token = _register(owner, "owner@example.org", "Owner Lab")
    owner.headers["Authorization"] = f"Bearer {owner_token}"
    title_only_id = _seed_paper(
        "owner@example.org",
        doi="",
        work_id="W000000000001",
    )
    rejected = owner.post(
        f"/documents/{title_only_id}/enrichments",
        json={"request_id": "enrich:title-only"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "paper_enrichment_strong_identity_required"

    exact_id = _seed_paper("owner@example.org", work_id="W654321")
    started = owner.post(
        f"/documents/{exact_id}/enrichments",
        json={"request_id": "enrich:private-1"},
    )
    receipt_id = started.json()["id"]
    outsider = TestClient(app)
    outsider_token = _register(outsider, "outside@example.org", "Outside Lab")
    outsider.headers["Authorization"] = f"Bearer {outsider_token}"
    hidden = outsider.get(f"/documents/{exact_id}/enrichments/{receipt_id}")
    assert hidden.status_code == 404


def test_receipt_is_private_from_another_user_in_the_same_org(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    init_db()
    monkeypatch.setattr("sixsentences_server.api.app.OpenAlexClient", _OpenAlexStub)
    monkeypatch.setattr("sixsentences_server.api.app.CrossrefClient", _CrossrefStub)
    app = create_app()
    owner = TestClient(app)
    token = _register(owner, "workspace-owner@example.org", "Shared Lab")
    owner.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper("workspace-owner@example.org")
    started = owner.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:same-org-private"},
    )
    assert started.status_code == 202
    receipt_id = started.json()["id"]

    with db_session() as session:
        owner_row = session.scalar(select(User).where(User.email == "workspace-owner@example.org"))
        assert owner_row is not None
        add_member(
            session,
            owner_row.org_id,
            "workspace-member@example.org",
            "StrongPass123!",
            "member",
        )
        member_token = authenticate(
            session,
            "workspace-member@example.org",
            "StrongPass123!",
        )
    member = TestClient(app)
    member.headers["Authorization"] = f"Bearer {member_token}"
    assert member.get(f"/documents/{document_id}/enrichments/{receipt_id}").status_code == 404
    latest = member.get(f"/documents/{document_id}/enrichments/latest")
    assert latest.status_code == 200 and latest.json() == {"job": None}


def test_provider_identity_mismatch_never_produces_suggestions() -> None:
    class WrongPaperOpenAlex:
        def get_work(self, _external_id: str) -> WorkRecord:
            return WorkRecord(
                id="W999999",
                doi="10.5555/a-different-paper",
                title="A different paper",
            )

    with pytest.raises(PaperEnrichmentError) as error:
        discover_missing_metadata(
            client=WrongPaperOpenAlex(),  # type: ignore[arg-type]
            identity=StrongPaperIdentity(
                "doi",
                "10.5555/exact-paper",
                "doi:10.5555/exact-paper",
            ),
            current_metadata={},
            protected_fields=set(),
        )
    assert error.value.code == "paper_enrichment_identity_mismatch"


def test_apply_rejects_a_changed_library_revision(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    init_db()
    monkeypatch.setattr("sixsentences_server.api.app.OpenAlexClient", _OpenAlexStub)
    monkeypatch.setattr("sixsentences_server.api.app.CrossrefClient", _CrossrefStub)
    client = TestClient(create_app())
    token = _register(client, "revision@example.org", "Revision Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper("revision@example.org")
    started = client.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:revision-conflict"},
    )
    assert started.status_code == 202
    receipt_id = started.json()["id"]
    job = client.get(f"/documents/{document_id}/enrichments/latest").json()["job"]
    changed = client.patch(
        f"/documents/{document_id}/metadata",
        json={
            "expected_revision": job["source_revision"],
            "mode": "edit",
            "metadata": {"title": "A manually corrected title"},
        },
    )
    assert changed.status_code == 200, changed.text

    apply = client.post(
        f"/documents/{document_id}/enrichments/{receipt_id}/apply",
        json={"expected_revision": job["source_revision"]},
    )
    assert apply.status_code == 409
    assert apply.json()["detail"]["code"] == "library_metadata_revision_conflict"


def test_provider_payloads_are_bounded_before_receipt_persistence() -> None:
    class OversizeOpenAlex:
        def get_work(self, _external_id: str) -> WorkRecord:
            return WorkRecord(
                id="W999999",
                doi="10.5555/oversize",
                title="T" * 50_000,
                abstract="A" * 200_000,
                authors=[f"{index}-" + "N" * 10_000 for index in range(300)],
                oa_status="green",
                pdf_url="https://example.org/" + "p" * 10_000,
            )

    suggestions, sources = discover_missing_metadata(
        client=OversizeOpenAlex(),  # type: ignore[arg-type]
        identity=StrongPaperIdentity("doi", "10.5555/oversize", "doi:10.5555/oversize"),
        current_metadata={},
        protected_fields=set(),
    )
    by_field = {item["field"]: item["value"] for item in suggestions}
    assert len(by_field["title"]) == 500
    assert len(by_field["abstract"]) == 20_000
    assert len(by_field["authors"]) == 100
    assert max(len(author) for author in by_field["authors"]) == 300
    assert len(json.dumps({"suggestions": suggestions, "sources": sources})) < 60_000


def test_only_known_system_titles_are_semantically_missing() -> None:
    assert metadata_value_is_missing("title", "Uploaded document")
    assert metadata_value_is_missing("title", "W000000000123")
    assert metadata_value_is_missing("title", "W123456")
    assert not metadata_value_is_missing("title", "W3")
    assert not metadata_value_is_missing("title", "A real non-empty paper title")
    assert not metadata_value_is_missing("abstract", "Uploaded document")


def test_deleting_a_paper_cancels_queued_lookup_and_removes_private_receipt(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    init_db()
    client = TestClient(create_app())
    token = _register(client, "delete-race@example.org", "Delete Race Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper("delete-race@example.org")
    started = client.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:delete-race"},
    )
    assert started.status_code == 202
    receipt_public_id = started.json()["id"]
    with db_session() as session:
        receipt = session.scalar(
            select(PaperEnrichmentRow).where(PaperEnrichmentRow.public_id == receipt_public_id)
        )
        assert receipt is not None and receipt.background_job_id is not None
        job_id = receipt.background_job_id

    deleted = client.delete(f"/documents/{document_id}")
    assert deleted.status_code == 204
    with db_session() as session:
        assert (
            session.scalar(
                select(PaperEnrichmentRow).where(PaperEnrichmentRow.public_id == receipt_public_id)
            )
            is None
        )
        job = session.get(BackgroundJobRow, job_id)
        assert job is not None and job.status == "cancelled"


def test_cancel_retry_generation_rejects_a_late_result_from_the_old_attempt(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    init_db()
    client = TestClient(create_app())
    token = _register(client, "generation@example.org", "Generation Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper("generation@example.org")
    paper = next(item for item in client.get("/documents").json() if item["id"] == document_id)
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "generation@example.org"))
        assert user is not None
        receipt = PaperEnrichmentRow(
            org_id=user.org_id,
            user_id=user.id,
            document_id=document_id,
            request_id="enrich:generation-race",
            identity_kind="doi",
            identity_value="10.5555/exact-paper",
            source_revision=paper["metadata_revision"],
            status="queued",
        )
        session.add(receipt)
        session.flush()
        receipt_id = receipt.id

    first_provider = threading.Event()
    second_provider = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    def controlled_discovery(**_: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_provider.set()
            assert release_first.wait(5)
            value = "old-attempt"
        else:
            second_provider.set()
            assert release_second.wait(5)
            value = "new-attempt"
        return (
            [
                {
                    "field": "abstract",
                    "current": None,
                    "value": value,
                    "confidence": 0.99,
                    "source": {
                        "provider": "openalex",
                        "url": "https://openalex.org/W123456",
                        "matched_identifier": {
                            "kind": "doi",
                            "value": "10.5555/exact-paper",
                        },
                    },
                }
            ],
            [],
        )

    monkeypatch.setattr(
        "sixsentences_server.api.app.discover_missing_metadata",
        controlled_discovery,
    )
    first = threading.Thread(target=_execute_paper_enrichment, args=(receipt_id,))
    first.start()
    assert first_provider.wait(5)
    with db_session() as session:
        receipt = session.get(PaperEnrichmentRow, receipt_id)
        assert receipt is not None and receipt.attempt_count == 1
        receipt.status = "queued"
    second = threading.Thread(target=_execute_paper_enrichment, args=(receipt_id,))
    second.start()
    assert second_provider.wait(5)
    release_first.set()
    first.join(5)
    with db_session() as session:
        receipt = session.get(PaperEnrichmentRow, receipt_id)
        assert receipt is not None
        assert receipt.status == "running"
        assert receipt.attempt_count == 2
        assert receipt.suggestions == []
    release_second.set()
    second.join(5)
    with db_session() as session:
        receipt = session.get(PaperEnrichmentRow, receipt_id)
        assert receipt is not None and receipt.status == "completed"
        assert receipt.suggestions[0]["value"] == "new-attempt"


def test_retry_obeys_the_same_active_user_capacity_as_a_new_start(
    settings: object,
    monkeypatch: Any,
) -> None:
    del settings
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    init_db()
    client = TestClient(create_app())
    token = _register(client, "capacity@example.org", "Capacity Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    first_id = _seed_paper("capacity@example.org", doi="10.5555/capacity-one", work_id="W111111")
    second_id = _seed_paper("capacity@example.org", doi="10.5555/capacity-two", work_id="W222222")
    third_id = _seed_paper("capacity@example.org", doi="10.5555/capacity-three", work_id="W333333")
    assert (
        client.post(
            f"/documents/{first_id}/enrichments",
            json={"request_id": "enrich:capacity-one"},
        ).status_code
        == 202
    )
    assert (
        client.post(
            f"/documents/{second_id}/enrichments",
            json={"request_id": "enrich:capacity-two"},
        ).status_code
        == 202
    )
    third = next(item for item in client.get("/documents").json() if item["id"] == third_id)
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "capacity@example.org"))
        assert user is not None
        failed = PaperEnrichmentRow(
            org_id=user.org_id,
            user_id=user.id,
            document_id=third_id,
            request_id="enrich:capacity-retry",
            identity_kind="doi",
            identity_value="10.5555/capacity-three",
            source_revision=third["metadata_revision"],
            status="failed",
            attempt_count=1,
        )
        session.add(failed)
        session.flush()
        public_id = failed.public_id

    retry = client.post(f"/documents/{third_id}/enrichments/{public_id}/retry")
    assert retry.status_code == 503
    assert retry.json()["detail"]["code"] == "paper_enrichment_capacity_busy"


def test_parallel_start_intents_share_one_active_receipt(
    settings: object,
    monkeypatch: Any,
) -> None:
    """The transaction fence and DB invariant prevent capacity bypasses."""

    del settings
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    init_db()
    app = create_app()
    client = TestClient(app)
    token = _register(client, "parallel@example.org", "Parallel Lab")
    document_id = _seed_paper("parallel@example.org")
    barrier = threading.Barrier(2)
    responses: list[tuple[int, dict[str, Any]]] = []
    response_lock = threading.Lock()

    def start(request_id: str) -> None:
        caller = TestClient(app)
        caller.headers["Authorization"] = f"Bearer {token}"
        barrier.wait(timeout=5)
        response = caller.post(
            f"/documents/{document_id}/enrichments",
            json={"request_id": request_id},
        )
        with response_lock:
            responses.append((response.status_code, response.json()))

    first = threading.Thread(target=start, args=("enrich:parallel-one",))
    second = threading.Thread(target=start, args=("enrich:parallel-two",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert [status for status, _ in responses] == [202, 202]
    assert len({payload["id"] for _, payload in responses}) == 1
    with db_session() as session:
        receipts = list(
            session.scalars(
                select(PaperEnrichmentRow).where(
                    PaperEnrichmentRow.document_id == document_id,
                    PaperEnrichmentRow.status.in_(("queued", "running")),
                )
            ).all()
        )
        assert len(receipts) == 1
        assert (
            session.scalar(
                select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_paper_enrichment")
            )
            is not None
        )


def test_pre_execute_queue_crashes_have_a_durable_retry_budget(
    settings: object,
    monkeypatch: Any,
) -> None:
    """A crash before worker entry must not create unlimited retry receipts."""

    del settings
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    init_db()
    client = TestClient(create_app())
    token = _register(client, "retry-budget@example.org", "Retry Budget Lab")
    client.headers["Authorization"] = f"Bearer {token}"
    document_id = _seed_paper("retry-budget@example.org")
    started = client.post(
        f"/documents/{document_id}/enrichments",
        json={"request_id": "enrich:retry-budget"},
    )
    assert started.status_code == 202
    public_id = started.json()["id"]

    def crash_before_task(_receipt_id: int) -> None:
        raise RuntimeError("subprocess failed before provider entry")

    monkeypatch.setattr(jobs, "_resolve_task", lambda _name: crash_before_task)
    worker = jobs.DatabaseWorker(run_in_subprocess=False, lane="documents")
    assert worker.run_once()
    for expected_retry_count in range(1, 4):
        retried = client.post(f"/documents/{document_id}/enrichments/{public_id}/retry")
        assert retried.status_code == 202, retried.text
        assert retried.json()["retry_count"] == expected_retry_count
        assert worker.run_once()

    rejected = client.post(f"/documents/{document_id}/enrichments/{public_id}/retry")
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "paper_enrichment_retry_limit"
