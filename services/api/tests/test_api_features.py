"""Password change, PRISMA SVG export, and the ideas board."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.core.db import (
    Org,
    WriterContributionRow,
    WriterDocumentRow,
    WriterMessageRow,
    db_session,
)
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus


def _authed(app: FastAPI, email: str = "owner@example.org", org: str = "Acme") -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"},
    )
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return client


def test_change_password_keeps_own_session(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app)
    other = TestClient(app)
    login = other.post(
        "/auth/login", json={"email": "owner@example.org", "password": "StrongPass123!"}
    )
    other.headers["Authorization"] = f"Bearer {login.json()['token']}"
    assert other.get("/auth/me").status_code == 200
    wrong = client.post(
        "/auth/password", json={"current_password": "nope-nope", "new_password": "brandnewsecret"}
    )
    assert wrong.status_code == 400
    assert "current password" in wrong.json()["detail"]
    ok = client.post(
        "/auth/password",
        json={"current_password": "StrongPass123!", "new_password": "brandnewsecret"},
    )
    assert ok.status_code == 200
    assert client.get("/auth/me").status_code == 200
    assert other.get("/auth/me").status_code == 401
    relogin = TestClient(app)
    assert (
        relogin.post(
            "/auth/login", json={"email": "owner@example.org", "password": "StrongPass123!"}
        ).status_code
        == 401
    )
    assert (
        relogin.post(
            "/auth/login", json={"email": "owner@example.org", "password": "brandnewsecret"}
        ).status_code
        == 200
    )


def test_prisma_svg_export(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "svg"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    resp = client.get(f"/runs/{run_id}/prisma.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.text
    assert body.startswith("<svg")
    assert "PRISMA 2020 FLOW" in body
    assert "records identified" in body


def test_probe_traces_included_and_explains_missing(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "probe"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    traced = client.post(
        f"/runs/{run_id}/probe", json={"query": "Attention is all you need"}
    ).json()
    assert traced["status"] == "unsure"
    assert traced["resolved"]["work_id"] == "W1"
    stages = [s["stage"] for s in traced["steps"]]
    assert "identification" in stages
    assert "verdict" in stages
    missed = client.post(
        f"/runs/{run_id}/probe",
        json={"query": "A survey of active learning for text classification"},
    ).json()
    assert missed["status"] == "in_index_not_retrieved"
    query_steps = [s for s in missed["steps"] if s["stage"] == "query"]
    assert query_steps and "transformer" in query_steps[0]["detail"].lower()
    assert "broaden" in missed["suggestion"].lower()
    unknown = client.post(f"/runs/{run_id}/probe", json={"query": "10.9999/does-not-exist"}).json()
    assert unknown["status"] == "not_found"


def test_reproducibility_bundle(corpus: DuckDBCorpus) -> None:
    import io
    import json
    import zipfile

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "bundle"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    resp = client.get(f"/runs/{run_id}/bundle.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as bundle:
        names = set(bundle.namelist())
        assert {
            "README.txt",
            "manifest.json",
            "protocol.json",
            "prisma.json",
            "prisma.svg",
            "decisions.csv",
            "works.csv",
            "events.jsonl",
            "methods.txt",
            "usage.json",
        } <= names
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["question"] == "transformers"
        assert manifest["corpus_version"].startswith("micro-")
        prisma = json.loads(bundle.read("prisma.json"))
        assert prisma["records_identified"] == 1
        decisions = bundle.read("decisions.csv").decode()
        assert "W1" in decisions and "stub" in decisions
        assert bundle.read("events.jsonl").decode().count("\n") >= 3


def test_snowball_collects_references_and_citing_works(corpus: DuckDBCorpus) -> None:
    from sixsentences_server.core.models import WorkRecord
    from sixsentences_server.pipeline.snowball import collect_snowball_candidates

    seed = corpus.by_ids(["W1"])[0]
    assert seed.referenced_works == ["W2"]

    class FakeClient:
        def iter_works(self, oa_filter: str, *, limit: int) -> list[WorkRecord]:
            if oa_filter.startswith("cites:"):
                return [
                    WorkRecord(
                        id="W900",
                        title="A brand new study citing the transformer",
                        year=2024,
                        authors=["Z. New"],
                        cited_by_count=5,
                    )
                ]
            return []

    harvest = collect_snowball_candidates(
        [seed],
        known_ids={"W1"},
        known_dois=set(),
        known_titles=set(),
        corpus=corpus,
        client=FakeClient(),
    )
    assert {r.id for r in harvest.records} == {"W2", "W900"}
    assert harvest.backward_refs == 1
    assert harvest.backward_resolved == 1
    assert harvest.forward_returned == 1
    quiet = collect_snowball_candidates(
        [seed],
        known_ids={"W1", "W2"},
        known_dois=set(),
        known_titles=set(),
        corpus=corpus,
        client=None,
    )
    assert quiet.records == []
    assert quiet.backward_refs == 0
    assert quiet.forward_returned == 0


def test_reference_import_parsers() -> None:
    from sixsentences_server.connectors.refimport import parse_bibtex, parse_ris

    ris = "TY  - JOUR\nTI  - Screening with ensembles\nAU  - Smith, Alice\nAU  - Jones, Bob\nPY  - 2023\nDO  - https://doi.org/10.5/ens\nAB  - We study ensembles.\nJO  - JMLR\nER  -\nTY  - CONF\nTI  - Another record\nPY  - 2022/01/15\nER  -\n"
    records = parse_ris(ris)
    assert len(records) == 2
    assert records[0]["title"] == "Screening with ensembles"
    assert records[0]["doi"] == "10.5/ens"
    assert records[0]["authors"] == ["Smith, Alice", "Jones, Bob"]
    assert records[0]["venue"] == "JMLR"
    assert records[1]["year"] == 2022
    bib = "@article{smith2023,\n  title = {Screening with {LLM} ensembles},\n  author = {Smith, Alice and Jones, Bob},\n  year = {2023},\n  doi = {10.5/ens},\n  journal = {JMLR}\n}\n"
    parsed = parse_bibtex(bib)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Screening with LLM ensembles"
    assert parsed[0]["authors"] == ["Smith, Alice", "Jones, Bob"]
    assert parsed[0]["year"] == 2023


def test_query_translations_for_other_databases(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "translate"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": '"transformer architectures" AND attention'},
    ).json()["id"]
    payload = client.get(f"/runs/{run_id}/query-translations").json()
    targets = payload["targets"]
    assert '"transformer architectures"[tiab]' in targets["pubmed"]
    assert "attention[tiab]" in targets["pubmed"]
    assert "TITLE-ABS-KEY" in targets["scopus"]
    assert "TS=(" in targets["wos"]
    assert "All Metadata" in targets["ieee"]


def test_import_batch_joins_identification(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    ris = "TY  - JOUR\nTI  - A fresh scopus-only record about transformers\nPY  - 2021\nDO  - 10.7/fresh\nER  -\nTY  - JOUR\nTI  - Attention duplicate from the export\nDO  - 10.1/alpha\nER  -\n"
    batch = client.post(
        "/imports", json={"filename": "scopus.ris", "label": "Scopus", "text": ris}
    ).json()
    assert batch["count"] == 2
    project = client.post("/projects", json={"name": "imports"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={
            "question": "transformers",
            "query": "transformer",
            "import_batch_ids": [batch["id"]],
        },
    ).json()["id"]
    run = client.get(f"/runs/{run_id}").json()
    assert run["prisma"]["records_identified"] == 3
    assert run["prisma"]["duplicates_removed"] == 1
    works = client.get(f"/runs/{run_id}/works").json()
    titles = [w["title"].lower() for w in works["works"]]
    assert any("fresh scopus-only record" in title for title in titles)
    assert client.get("/imports").json()[0]["count"] == 2
    unknown = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "x", "query": "transformer", "import_batch_ids": [99999]},
    )
    assert unknown.status_code == 404


def test_zotero_canaries_resolve_against_corpus(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:

    class FakeZotero:
        def __init__(self, api_key: str, library_type: str, library_id: str) -> None:
            assert library_type == "user"

        def list_items(self, *, limit: int = 300) -> list[dict[str, str]]:
            return [
                {"title": "irrelevant", "doi": "10.1/alpha"},
                {"title": "A survey of active learning for text classification", "doi": ""},
                {"title": "Something nobody indexed", "doi": ""},
            ]

    monkeypatch.setattr("sixsentences_server.api.app.ZoteroClient", FakeZotero)
    client = _authed(create_app())
    resp = client.post(
        "/zotero/canaries",
        json={"api_key": "zk-testtest", "library_type": "user", "library_id": "42"},
    ).json()
    assert resp["canary_ids"] == ["W1", "W2"]
    assert resp["resolved"] == 2
    assert resp["total_items"] == 3
    assert len(resp["unresolved"]) == 1


def test_evidence_extraction_flow(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    import json as jsonlib
    from types import SimpleNamespace

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "evidence"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    refused = client.post(f"/runs/{run_id}/extraction", json={})
    assert refused.status_code == 409
    assert "provider" in refused.json()["detail"]

    class FakePool:
        def has_strong(self) -> bool:
            return True

        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            fields = jsonlib.loads(prompt.split("Fields to extract: ", 1)[1].split("\n", 1)[0])
            payload = {
                f: {
                    "value": f"{f} value",
                    "quote": "We introduce the transformer, based on self-attention.",
                    "page": None,
                }
                for f in fields
            }
            return SimpleNamespace(
                text=jsonlib.dumps({"fields": payload}), provider="fake", model="f1"
            )

    monkeypatch.setattr("sixsentences_server.api.app.available_specs", lambda: ["fake"])
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    decided = client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "seed include"}],
    )
    assert decided.status_code in (200, 201), decided.text
    started = client.post(f"/runs/{run_id}/extraction", json={})
    assert started.status_code == 202
    assert started.json()["scheduled"] == 1
    assert "population" in started.json()["fields"]
    table = client.get(f"/runs/{run_id}/extraction").json()
    assert table["status"] == "done"
    row = table["rows"][0]
    assert row["work_id"] == "W1"
    cell = row["payload"]["population"]
    assert cell["value"] == "population value"
    assert cell["verified"] is True
    assert cell["source"] == "abstract"
    edited = client.patch(
        f"/runs/{run_id}/extraction/W1", json={"field": "method", "value": "Randomized evaluation"}
    ).json()
    assert edited["payload"]["source"] == "human"
    assert edited["payload"]["verified"] is True
    csv_export = client.get(f"/runs/{run_id}/extraction.csv")
    assert csv_export.status_code == 200
    assert "population value" in csv_export.text
    tex_export = client.get(f"/runs/{run_id}/extraction.tex")
    assert "\\toprule" in tex_export.text
    assert "Randomized evaluation" in tex_export.text


def test_extraction_scheduling_reserves_then_settles_and_force_is_explicit(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importlib import import_module
    from types import SimpleNamespace

    from sixsentences_server.config import get_settings
    from sixsentences_server.core.db import (
        BackgroundJobRow,
        CapacityReservationRow,
        CreditEventRow,
        ExtractionRow,
        Run,
    )
    from sixsentences_server.jobs import JobConfigurationError

    api_app = import_module("sixsentences_server.api.app")
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "Synthetic extraction reservation"}).json()
    created_run = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    )
    assert created_run.status_code == 202, created_run.text
    public_id = created_run.json()["public_id"]
    decided = client.post(
        f"/runs/{public_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "synthetic include"}],
    )
    assert decided.status_code in (200, 201), decided.text
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    monkeypatch.setattr(api_app, "available_specs", lambda: ["synthetic"])
    monkeypatch.setattr(api_app, "_build_pool", lambda _settings: SimpleNamespace())
    monkeypatch.setattr(api_app, "extraction_cost", lambda count, _plan: count * 37)
    monkeypatch.setattr(
        api_app,
        "extract_for_work",
        lambda *_args: ({"finding": {"value": "synthetic", "verified": True}}, "synthetic"),
    )
    started = client.post(f"/runs/{public_id}/extraction", json={})
    assert started.status_code == 202 and started.json()["scheduled"] == 1
    with db_session() as session:
        run = session.scalar(select(Run).where(Run.public_id == public_id))
        assert run is not None
        run_id = run.id
        assert (
            session.query(CreditEventRow).filter_by(run_id=run_id, action="extraction").count() == 0
        )
        action = (
            session.query(CapacityReservationRow)
            .filter_by(run_id=run_id, action="extraction")
            .one()
        )
        assert action.reserved_credits == action.remaining_credits == 37
        job = session.query(BackgroundJobRow).filter_by(task="_execute_extraction").one()
        first_args, first_kwargs = (list(job.args), dict(job.kwargs))
        assert first_kwargs == {"work_ids": ["W1"], "capacity_costs": [0, 37]}
    duplicate = client.post(f"/runs/{public_id}/extraction", json={"force": True})
    assert duplicate.status_code == 409
    monkeypatch.setattr(api_app, "extraction_cost", lambda count, _plan: count * 61)
    api_app._execute_extraction(*first_args, **first_kwargs)
    retry = client.post(f"/runs/{public_id}/extraction", json={})
    assert retry.status_code == 202 and retry.json()["scheduled"] == 0

    def queue_failure(*_args, **_kwargs):
        raise JobConfigurationError("synthetic queue failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(api_app, "enqueue_job", queue_failure)
        with pytest.raises(JobConfigurationError):
            client.post(f"/runs/{public_id}/extraction", json={"force": True})
    with db_session() as session:
        assert session.query(ExtractionRow).filter_by(run_id=run_id).one().status == "done"
        assert (
            session.query(CapacityReservationRow)
            .filter_by(run_id=run_id, action="extraction")
            .count()
            == 1
        )
    forced = client.post(f"/runs/{public_id}/extraction", json={"force": True})
    assert forced.status_code == 202 and forced.json()["scheduled"] == 1
    with db_session() as session:
        jobs = session.scalars(
            select(BackgroundJobRow)
            .where(BackgroundJobRow.task == "_execute_extraction")
            .order_by(BackgroundJobRow.id)
        ).all()
        next_args, next_kwargs = (list(jobs[-1].args), dict(jobs[-1].kwargs))
    api_app._execute_extraction(*next_args, **next_kwargs)
    with db_session() as session:
        charges = session.scalars(
            select(CreditEventRow.credits)
            .where(CreditEventRow.run_id == run_id, CreditEventRow.action == "extraction")
            .order_by(CreditEventRow.id)
        ).all()
        assert charges == [37, 61]


def test_styled_bibliography_and_docx(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "word"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs", json={"question": "transformers", "query": "transformer"}
    ).json()["id"]
    text = client.get(f"/runs/{run_id}/bibliography?style=apa&format=text&included_only=false")
    assert text.status_code == 200
    assert "Vaswani" in text.text and "(2017)" in text.text
    ieee = client.get(f"/runs/{run_id}/bibliography?style=ieee&format=text&included_only=false")
    assert ieee.text.startswith("[1]")
    docx = client.get(f"/runs/{run_id}/bibliography?style=apa&format=docx&included_only=false")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    assert "references-apa.docx" in docx.headers["content-disposition"]
    methods = client.get(f"/runs/{run_id}/methods.docx")
    assert methods.status_code == 200 and methods.content[:2] == b"PK"
    assert client.get(f"/runs/{run_id}/bibliography?style=vancouver").status_code == 400


def test_compliance_pack(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "compliance"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    checklist = client.get(f"/runs/{run_id}/prisma-checklist").json()
    items = {row["item"]: row for row in checklist["items"]}
    assert len(items) == 27
    assert items["7"]["status"] == "covered"
    assert items["3"]["status"] == "author"
    assert items["9"]["status"] == "author"
    markdown = client.get(f"/runs/{run_id}/prisma-checklist?format=md")
    assert "PRISMA 2020 checklist" in markdown.text
    appendix = client.get(f"/runs/{run_id}/search-appendix")
    assert appendix.status_code == 200
    assert "query (verbatim):  transformer" in appendix.text
    assert "sixsentences-corpus" in appendix.text
    prereg = client.get(f"/runs/{run_id}/preregistration")
    assert prereg.status_code == 200
    assert "## Search strategy" in prereg.text
    assert "transformer" in prereg.text
    prereg_docx = client.get(f"/runs/{run_id}/preregistration?format=docx")
    assert prereg_docx.content[:2] == b"PK"


def test_writer_document_lifecycle(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "writer"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "seed"}],
    )
    doc = client.post(
        "/writer", json={"title": "My review", "template": "article", "run_ids": [run_id]}
    ).json()
    assert doc["compile_status"] == "none"
    assert "\\bibliography{references}" in doc["content"]
    assert len(doc["public_id"]) == 10 and (not doc["public_id"].isdigit())
    via_public = client.get(f"/writer/{doc['public_id']}").json()
    assert via_public["id"] == doc["id"]
    citations = client.get(f"/writer/{doc['id']}/citations").json()
    assert citations and citations[0]["key"].startswith("vaswani2017")
    bib = client.get(f"/writer/{doc['id']}/references.bib")
    assert f"@article{{{citations[0]['key']}," in bib.text
    prisma_art = client.get(f"/writer/{doc['id']}/artifacts/prisma", params={"run": run_id}).json()
    assert "\\begin{tikzpicture}" in prisma_art["latex"]
    assert "Records identified" in prisma_art["latex"]
    methods_art = client.get(
        f"/writer/{doc['id']}/artifacts/methods", params={"run": run_id}
    ).json()
    assert "Methods paragraph generated" in methods_art["latex"]
    assert (
        client.get(f"/writer/{doc['id']}/artifacts/evidence", params={"run": run_id}).status_code
        == 404
    )
    client.post(
        f"/writer/{doc['id']}/contributions",
        json={
            "events": [
                {
                    "kind": "ai_edit_applied",
                    "chars_added": 40,
                    "chars_removed": 10,
                    "auto": False,
                    "message_id": 1,
                },
                {"kind": "bogus"},
            ]
        },
    )
    log = client.get(f"/writer/{doc['id']}/contribution-log").json()
    assert log["summary"]["edits_applied"] == 1
    assert log["summary"]["ai_chars_added"] == 40
    assert log["summary"]["linked_searches"]
    assert "documented AI assistant" in log["disclosure"]
    assert "AI assistance disclosure" in log["disclosure_tex"]
    client.patch(f"/writer/{doc['id']}", json={"content": "\\documentclass{article}x"})
    client.post(f"/writer/{doc['id']}/snapshots", json={"note": "before edits"})
    client.patch(f"/writer/{doc['id']}", json={"content": "broken"})
    snaps = client.get(f"/writer/{doc['id']}/snapshots").json()
    restored = client.post(f"/writer/{doc['id']}/restore/{snaps[0]['id']}").json()
    assert restored["content"] == "\\documentclass{article}x"
    fake_ok = f'''{sys.executable} -c "import pathlib;pathlib.Path('main.pdf').write_bytes(b'%PDF-1.4 fake')"'''
    monkeypatch.setenv("SIX_TECTONIC_CMD", fake_ok)
    from sixsentences_server.config import get_settings

    get_settings.cache_clear()
    scheduled = client.post(f"/writer/{doc['id']}/compile")
    assert scheduled.status_code == 202
    compiled = client.get(f"/writer/{doc['id']}").json()
    assert compiled["compile_status"] == "ok"
    assert compiled["compile_errors"] == []
    pdf = client.get(f"/writer/{doc['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    fake_fail = f'''{sys.executable} -c "print('! Undefined control sequence.'); print('l.3 \\\\nope'); raise SystemExit(1)"'''
    monkeypatch.setenv("SIX_TECTONIC_CMD", fake_fail)
    get_settings.cache_clear()
    assert client.post(f"/writer/{doc['id']}/compile").status_code == 202
    failed = client.get(f"/writer/{doc['id']}").json()
    assert failed["compile_status"] == "error"
    assert failed["compile_errors"][0]["message"].startswith("Undefined control")
    assert failed["compile_errors"][0]["line"] == 3
    get_settings.cache_clear()
    from sixsentences_server.writer.service import parse_errors

    parsed = parse_errors(
        "error: main.tex:3: Undefined control sequence\nerror: halted on potentially-recoverable error"
    )
    assert parsed[0] == {"path": "main.tex", "line": 3, "message": "Undefined control sequence"}
    assert parsed[1]["line"] is None
    with db_session() as session:
        original = session.get(WriterDocumentRow, doc["id"])
        assert original is not None
        derived = WriterDocumentRow(
            org_id=original.org_id,
            created_by=original.created_by,
            title="Retargeted child",
            content="\\documentclass{article}",
            derived_from_document_id=original.id,
        )
        session.add(derived)
        session.flush()
        derived_id = derived.id
    deleted = client.delete(f"/writer/{doc['id']}")
    assert deleted.status_code == 200, deleted.text
    with db_session() as session:
        assert session.get(WriterDocumentRow, doc["id"]) is None
        child = session.get(WriterDocumentRow, derived_id)
        assert child is not None and child.derived_from_document_id is None
        contribution = session.scalar(
            select(WriterContributionRow).where(WriterContributionRow.document_id == doc["id"])
        )
        assert contribution is None


def test_writer_handoff_objective_is_persisted_as_chat_context(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app(), "writer-handoff@example.org", "Writer Handoff")
    objective = "Draft a source-grounded methods section that explains the screening protocol and preserves unresolved limitations."
    created = client.post(
        "/writer", json={"title": "Screening methods", "template": "blank", "objective": objective}
    )
    assert created.status_code == 201, created.text
    document = created.json()
    history = client.get(f"/writer/{document['public_id']}/chat")
    assert history.status_code == 200, history.text
    messages = history.json()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == f"Writing objective: {objective}"
    assert messages[0]["payload"] == {"kind": "workspace_handoff"}


def test_figures_generation_and_writer_attach(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(create_app())
    from sixsentences_server.config import get_settings

    monkeypatch.setenv("SIX_GEMINI_API_KEY", "")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    missing = client.post("/figures", json={"prompt": "A method pipeline"})
    assert missing.status_code == 409
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda _settings: None)
    get_settings.cache_clear()
    seen: dict[str, object] = {}

    def fake_render(
        prompt: str, *, api_key: str, model: str, context=None, timeout=180.0, provider="google"
    ) -> bytes:
        seen["prompt"] = prompt
        seen["context"] = context
        seen["model"] = model
        seen["provider"] = provider
        return b"\x89PNG fake image bytes"

    monkeypatch.setattr("sixsentences_server.api.app.render_figure", fake_render)
    bogus = client.post("/figures", json={"prompt": "A method pipeline", "model": "dall-e-1"})
    assert bogus.status_code == 422
    project = client.post("/projects", json={"name": "figs"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "seed"}],
    )
    created = client.post(
        "/figures", json={"prompt": "Draw the screening pipeline", "run_id": run_id}
    ).json()
    assert len(created["public_id"]) == 10
    fig = client.get(f"/figures/{created['public_id']}").json()
    assert fig["status"] == "ok"
    assert fig["created_at"].endswith("+00:00")
    assert [stage["status"] for stage in fig["config"]["pipeline"]] == [
        "completed",
        "completed",
        "skipped",
        "skipped",
    ]
    assert "Research question: transformers" in str(seen["context"])
    assert seen["provider"] == "google"
    assert seen["model"] == "gemini-3-pro-image"
    image = client.get(f"/figures/{created['public_id']}/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert client.get("/figures").json()[0]["run"]["label"] == "transformers"
    doc = client.post("/writer", json={"title": "Draft", "template": "blank"}).json()
    attached = client.post(
        f"/figures/{created['public_id']}/attach", json={"document_id": doc["public_id"]}
    ).json()
    assert attached["filename"] == "draw-the-screening-pipeline.png"
    assets = client.get(f"/writer/{doc['public_id']}/assets").json()
    assert [a["filename"] for a in assets] == [attached["filename"]]
    again = client.post(
        f"/figures/{created['public_id']}/attach", json={"document_id": doc["public_id"]}
    ).json()
    assert again["filename"] == attached["filename"]
    assert len(client.get(f"/writer/{doc['public_id']}/assets").json()) == 1
    client.patch(f"/figures/{created['public_id']}", json={"title": "Screening funnel"})
    titled = client.post(
        f"/figures/{created['public_id']}/attach", json={"document_id": doc["public_id"]}
    ).json()
    assert titled["filename"] == "screening-funnel.png"
    listed = client.get(f"/writer/{doc['public_id']}/assets").json()
    thumb = client.get(f"/writer/{doc['public_id']}/assets/{listed[0]['id']}/image")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/png"
    assert client.delete(f"/figures/{created['public_id']}").json() == {"ok": True}
    from datetime import UTC, datetime, timedelta

    from sixsentences_server.core.db import FigureRow

    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        stale = FigureRow(
            org_id=org_id,
            prompt="An interrupted render",
            status="pending",
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(stale)
        session.flush()
        stale_public_id = stale.public_id
    stale_payload = next(
        item for item in client.get("/figures").json() if item["public_id"] == stale_public_id
    )
    assert stale_payload["status"] == "error"
    assert "interrupted or timed out" in stale_payload["error"]
    assert client.delete(f"/figures/{stale_public_id}").json() == {"ok": True}
    picked = client.post(
        "/figures", json={"prompt": "A workflow schematic", "model": "nano-banana-2"}
    ).json()
    assert seen["model"] == "gemini-3.1-flash-image"
    assert "model" not in picked
    assert "cost_action_id" not in picked["config"]
    retired = client.post(
        "/figures", json={"prompt": "A workflow schematic", "model": "recraft-v4"}
    )
    assert retired.status_code == 422
    get_settings.cache_clear()
    from sixsentences_server.figures.service import resolve_figure_model

    assert (
        resolve_figure_model("nano-banana-2", "google", "gemini-3-pro-image")
        == "gemini-3.1-flash-image"
    )
    assert resolve_figure_model("auto", "google", "gemini-3-pro-image") == "gemini-3-pro-image"
    from sixsentences_server.figures.service import _friendly_provider_error

    assert "usage limit" in _friendly_provider_error(429)
    assert "access key" in _friendly_provider_error(403)
    assert "having trouble" in _friendly_provider_error(503)
    assert "balance" in _friendly_provider_error(402)
    assert "exceeded" not in _friendly_provider_error(429)
    from io import BytesIO as _BytesIO

    from PIL import Image as _Image

    from sixsentences_server.figures.service import _ensure_png, _format_output

    jpeg = _BytesIO()
    _Image.new("RGB", (2, 2), (10, 20, 30)).save(jpeg, format="JPEG")
    assert _ensure_png(jpeg.getvalue()).startswith(b"\x89PNG")
    rgba = _BytesIO()
    _Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(rgba, format="PNG")
    flattened = _Image.open(_BytesIO(_ensure_png(rgba.getvalue())))
    assert flattened.mode == "RGB"
    assert flattened.getpixel((0, 0)) == (255, 255, 255)
    generated = _Image.open(_BytesIO(_format_output(jpeg.getvalue(), "1k", "1:1")))
    assert generated.info["AI-Generated"] == "true"
    assert "trainedAlgorithmicMedia" in generated.info["XML:com.adobe.xmp"]


def test_visual_brief_accepts_15k_and_rejects_above_server_limit(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API, durable row and worker must preserve a PaperBanana-sized brief."""
    from sixsentences_server.config import get_settings
    from sixsentences_server.core.db import CapacityReservationRow, FigureRow
    from sixsentences_server.figures.limits import FIGURE_PROMPT_MAX_CHARACTERS

    client = _authed(create_app(), email="long-visual-brief@example.org", org="Long visual brief")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    provider_prompts: list[str] = []

    def fake_render(prompt: str, **_kwargs: object) -> bytes:
        provider_prompts.append(prompt)
        return b"\x89PNG long visual brief"

    monkeypatch.setattr("sixsentences_server.api.app.render_figure", fake_render)
    long_brief = "A" * 14999 + "Z"
    created = client.post("/figures", json={"prompt": long_brief, "review_passes": 0})
    assert created.status_code == 202, created.text
    assert created.json()["prompt"] == long_brief
    assert provider_prompts == [long_brief]
    with db_session() as session:
        stored = session.scalar(select(FigureRow).where(FigureRow.prompt == long_brief))
        assert stored is not None
        reservations_before = session.query(CapacityReservationRow).count()
        figures_before = session.query(FigureRow).count()
    rejected = client.post("/figures", json={"prompt": "X" * (FIGURE_PROMPT_MAX_CHARACTERS + 1)})
    assert rejected.status_code == 422
    with db_session() as session:
        assert session.query(CapacityReservationRow).count() == reservations_before
        assert session.query(FigureRow).count() == figures_before
    get_settings.cache_clear()


def test_figure_worker_never_persists_provider_diagnostics(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable figure errors are safe to display even after a UI reload."""
    from sixsentences_server.config import get_settings
    from sixsentences_server.figures.service import FigureGenerationError

    client = _authed(create_app(), email="safe-figure-error@example.org", org="Safe figure error")
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()

    def failing_render(_prompt: str, **_kwargs: object) -> bytes:
        raise FigureGenerationError(
            "OpenRouter API key rejected by provider endpoint request_id=secret"
        )

    monkeypatch.setattr("sixsentences_server.api.app.render_figure", failing_render)
    response = client.post(
        "/figures", json={"prompt": "Draw a safe failure path", "review_passes": 0}
    )
    assert response.status_code == 202, response.text
    figure = client.get(f"/figures/{response.json()['public_id']}").json()
    assert figure["status"] == "error"
    assert figure["error"] == "Figure generation could not be completed. Please try again."
    assert "OpenRouter" not in str(figure)
    assert "request_id" not in str(figure)
    get_settings.cache_clear()


def test_figure_output_fills_requested_canvas_and_repairs_legacy_padding() -> None:
    """Provider-sized artwork must not sit tiny inside a larger export."""
    from io import BytesIO

    from PIL import Image, ImageChops

    from sixsentences_server.figures.service import _format_output, repair_legacy_output

    provider = BytesIO()
    Image.new("RGB", (64, 64), (20, 80, 50)).save(provider, format="PNG")
    formatted = Image.open(BytesIO(_format_output(provider.getvalue(), "1k", "4:3")))
    assert formatted.size == (1024, 768)
    formatted_bounds = ImageChops.difference(
        formatted, Image.new("RGB", formatted.size, (255, 255, 255))
    ).getbbox()
    assert formatted_bounds is not None
    assert formatted_bounds[2] - formatted_bounds[0] == 768
    assert formatted_bounds[3] - formatted_bounds[1] == 768
    legacy = Image.new("RGB", (1024, 768), (255, 255, 255))
    legacy.paste(Image.new("RGB", (64, 64), (20, 80, 50)), (480, 352))
    legacy_buffer = BytesIO()
    legacy.save(legacy_buffer, format="PNG")
    repaired = Image.open(BytesIO(repair_legacy_output(legacy_buffer.getvalue())))
    repaired_bounds = ImageChops.difference(
        repaired, Image.new("RGB", repaired.size, (255, 255, 255))
    ).getbbox()
    assert repaired_bounds is not None
    assert repaired_bounds[2] - repaired_bounds[0] > 500
    assert repaired_bounds[3] - repaired_bounds[1] > 500


def test_checked_figure_runs_real_plan_review_and_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked mode must execute the PaperBanana-style stages, not merely
    mention a review in the image prompt."""
    from io import BytesIO

    from PIL import Image

    from sixsentences_server.figures import service

    provider_image = BytesIO()
    Image.new("RGB", (80, 60), (245, 245, 240)).save(provider_image, format="PNG")
    provider_blob = provider_image.getvalue()
    render_prompts: list[str] = []
    transitions: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "_plan_google",
        lambda *args, **kwargs: (
            "Three short labeled stages connected left to right, with a compact rectangular layout, a restrained green palette, and no title."
        ),
    )

    def fake_render(full_prompt: str, **kwargs: object) -> bytes:
        render_prompts.append(full_prompt)
        return provider_blob

    monkeypatch.setattr(service, "_post_google", fake_render)
    monkeypatch.setattr(
        service,
        "_review_google",
        lambda *args, **kwargs: {
            "needs_revision": True,
            "issues": ["The center label is too small"],
            "revised_description": "Three short labeled stages connected left to right, with the center label enlarged, a compact rectangular layout, restrained green palette, high contrast, and no title or caption.",
        },
    )
    output = service.render_figure(
        "Show the retrieval pipeline",
        api_key="test",
        model="gemini-3-pro-image",
        provider="google",
        resolution="1k",
        review_passes=1,
        on_stage=lambda stage, status, detail=None: transitions.append((stage, status)),
    )
    assert output.startswith(b"\x89PNG")
    assert len(render_prompts) == 2
    assert "Do not render a figure number, figure title" in render_prompts[0]
    assert ("review_1", "running") in transitions
    assert ("review_1", "completed") in transitions
    assert ("refine_1", "completed") in transitions
    assert [stage["id"] for stage in service.figure_pipeline(2)] == [
        "brief",
        "render",
        "review_1",
        "refine_1",
        "review_2",
        "refine_2",
    ]


def test_paperbanana_sized_brief_reaches_planner_with_cost_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 15k brief is planned in full while every provider call remains guarded."""
    import json
    from io import BytesIO

    from PIL import Image

    from sixsentences_server.figures import service
    from sixsentences_server.figures.limits import FIGURE_PROMPT_MAX_CHARACTERS

    provider_image = BytesIO()
    Image.new("RGB", (32, 24), (245, 245, 240)).save(provider_image, format="PNG")
    messages_seen: list[list[dict[str, object]]] = []
    projected_costs: list[float] = []

    def fake_chat(messages: list[dict[str, object]], **kwargs: object) -> str:
        messages_seen.append(messages)
        before_request = kwargs.get("before_request")
        if callable(before_request):
            before_request(0.03 if len(messages_seen) == 1 else 0.06)
        if len(messages_seen) == 1:
            return json.dumps(
                {
                    "description": "Arrange the requested stages in a compact evidence-backed flow with exact short labels, clear arrows, and a restrained palette."
                }
            )
        return json.dumps(
            {
                "needs_revision": False,
                "issues": [],
                "revised_description": "The rendered topology is faithful and legible.",
            }
        )

    monkeypatch.setattr(service, "_post_google_chat", fake_chat)

    def fake_render(*_args: object, **kwargs: object) -> bytes:
        before_request = kwargs.get("before_request")
        if callable(before_request):
            before_request(0.35)
        return provider_image.getvalue()

    monkeypatch.setattr(service, "_post_google", fake_render)
    brief = "A" * 14980 + "TAIL_VISUAL_MARKER"
    output = service.render_figure(
        brief,
        api_key="test",
        model="gemini-3-pro-image",
        provider="google",
        review_passes=1,
        resolution="1k",
        before_request=projected_costs.append,
    )
    assert output.startswith(b"\x89PNG")
    assert "TAIL_VISUAL_MARKER" in json.dumps(messages_seen[0])
    assert projected_costs == [0.03, 0.35, 0.06]
    with pytest.raises(service.FigureGenerationError, match="16,000-character limit"):
        service.render_figure(
            "X" * (FIGURE_PROMPT_MAX_CHARACTERS + 1),
            api_key="test",
            model="gemini-3-pro-image",
            provider="google",
            review_passes=0,
        )


def test_figure_pipeline_keeps_only_the_latest_stage_running() -> None:
    from sixsentences_server.figures.service import advance_figure_pipeline, figure_pipeline

    stages = figure_pipeline(1)
    stages = advance_figure_pipeline(stages, "brief", "running")
    stages = advance_figure_pipeline(stages, "brief", "completed")
    stages = advance_figure_pipeline(stages, "render", "running")
    stages = advance_figure_pipeline(stages, "review_1", "completed", "1 correction")
    stages = advance_figure_pipeline(stages, "refine_1", "running", "Fix duplicated labels")
    assert [stage["status"] for stage in stages] == [
        "completed",
        "completed",
        "completed",
        "running",
    ]
    assert sum(stage["status"] == "running" for stage in stages) == 1
    assert stages[-1]["detail"] == "Fix duplicated labels"


def test_writer_templates_and_docx_import(corpus: DuckDBCorpus) -> None:
    import base64 as b64
    from io import BytesIO

    from docx import Document as DocxDocument

    client = _authed(create_app())
    tpl = client.post(
        "/writer/templates", json={"name": "Lab notes", "content": "\\documentclass{article}\\nLAB"}
    ).json()
    listed = client.get("/writer/templates").json()
    assert [t["name"] for t in listed] == ["Lab notes"]
    doc = client.post("/writer", json={"title": "From mine", "template_id": tpl["id"]}).json()
    assert "LAB" in doc["content"]
    assert client.delete(f"/writer/templates/{tpl['id']}").json() == {"ok": True}
    assert client.get("/writer/templates").json() == []
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml.ns import qn
    from docx.oxml.parser import OxmlElement
    from PIL import Image as PILImage

    word = DocxDocument()
    word.add_heading("Background", level=1)
    paragraph = word.add_paragraph()
    paragraph.add_run("bold claim").bold = True
    paragraph.add_run(" underlined").underline = True
    paragraph.add_run("2").font.superscript = True
    paragraph.add_run(" line one\nline two")
    rel_id = word.part.relate_to(
        "https://example.org/a#b", RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    link_run = OxmlElement("w:r")
    link_text = OxmlElement("w:t")
    link_text.text = "the registry"
    link_run.append(link_text)
    link.append(link_run)
    paragraph._p.append(link)
    methods = word.add_paragraph("Methoden")
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "1")
    methods._p.get_or_add_pPr().append(outline)
    word.add_paragraph("first point", style="List Bullet")
    word.add_paragraph("second point", style="List Bullet")
    nested = word.add_paragraph("sub point", style="List Bullet")
    num_pr = nested._p.get_or_add_pPr().get_or_add_numPr()
    num_pr.get_or_add_ilvl().val = 1
    num_pr.get_or_add_numId().val = 1
    table = word.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Method"
    table.rows[0].cells[1].text = "Score"
    table.rows[1].cells[0].text = "Ours"
    table.rows[1].cells[1].text = "0.9"
    png = BytesIO()
    PILImage.new("RGB", (4, 4), (200, 30, 30)).save(png, format="PNG")
    png.seek(0)
    word.add_picture(png)
    try:
        word.add_paragraph("Setup overview", style="Caption")
    except KeyError:
        from docx.enum.style import WD_STYLE_TYPE

        word.styles.add_style("Caption", WD_STYLE_TYPE.PARAGRAPH)
        word.add_paragraph("Setup overview", style="Caption")
    buffer = BytesIO()
    word.save(buffer)
    imported = client.post(
        "/writer/import",
        json={
            "filename": "My Draft.docx",
            "content_base64": b64.b64encode(buffer.getvalue()).decode(),
        },
    ).json()
    content = imported["content"]
    assert imported["title"] == "My Draft"
    assert "\\section{Background}" in content
    assert "\\textbf{bold claim}" in content
    assert "\\underline{ underlined}" in content
    assert "\\textsuperscript{2}" in content
    assert "line one\\\\\nline two" in content
    assert "\\href{https://example.org/a\\#b}{the registry}" in content
    assert "\\subsection{Methoden}" in content
    assert content.count("\\item") == 3
    assert content.count("\\begin{itemize}") == 2
    assert "\\toprule" in content and "Method & Score" in content
    assert "\\includegraphics[width=0.85\\linewidth]{word-image-1.png}" in content
    assert "\\caption{Setup overview}" in content
    imported_assets = client.get(f"/writer/{imported['public_id']}/assets").json()
    assert [a["filename"] for a in imported_assets] == ["word-image-1.png"]
    bad = client.post(
        "/writer/import",
        json={"filename": "fake.docx", "content_base64": b64.b64encode(b"not a zip").decode()},
    )
    assert bad.status_code == 422


def test_writer_multifile_latex_project_roundtrip(corpus: DuckDBCorpus) -> None:
    import base64 as b64
    from io import BytesIO
    from zipfile import ZipFile

    client = _authed(create_app())
    incoming = BytesIO()
    with ZipFile(incoming, "w") as archive:
        archive.writestr(
            "paper/main.tex",
            "\\documentclass{article}\n\\begin{document}\n\\input{sections/method}\n\\includegraphics{figs/chart.png}\n\\end{document}",
        )
        archive.writestr("paper/sections/method.tex", "\\section{Method}\nExact text.")
        archive.writestr("paper/styles/journal.sty", "% local style")
        archive.writestr("paper/figs/chart.png", b"png bytes")
        archive.writestr("notes/outside.txt", "not part of the detected root")
    imported = client.post(
        "/writer/import",
        json={
            "filename": "camera-ready.zip",
            "content_base64": b64.b64encode(incoming.getvalue()).decode(),
        },
    )
    assert imported.status_code == 201, imported.text
    doc = imported.json()
    files = client.get(f"/writer/{doc['public_id']}/files").json()
    assert [row["path"] for row in files] == [
        "main.tex",
        "sections/method.tex",
        "styles/journal.sty",
    ]
    method = files[1]
    updated = client.patch(
        f"/writer/{doc['public_id']}/files/{method['id']}",
        json={"content": "\\section{Method}\nRevised."},
    )
    assert updated.status_code == 200
    renamed = client.patch(
        f"/writer/{doc['public_id']}/files/{method['id']}",
        json={"path": "sections/methods-renamed.tex"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["path"] == "sections/methods-renamed.tex"
    assert renamed.json()["content"].endswith("Revised.")
    created = client.post(
        f"/writer/{doc['public_id']}/files",
        json={"path": "sections/results.tex", "content": "\\section{Results}"},
    )
    assert created.status_code == 201
    assets = client.get(f"/writer/{doc['public_id']}/assets").json()
    assert [asset["filename"] for asset in assets] == ["figs/chart.png"]
    exported = client.get(f"/writer/{doc['public_id']}/project.zip")
    assert exported.status_code == 200
    with ZipFile(BytesIO(exported.content)) as archive:
        assert archive.read("sections/methods-renamed.tex").decode().endswith("Revised.")
        assert "sections/method.tex" not in archive.namelist()
        assert archive.read("sections/results.tex").decode() == "\\section{Results}"
        assert archive.read("figs/chart.png") == b"png bytes"
    traversal = client.post(
        f"/writer/{doc['public_id']}/files", json={"path": "../secret.tex", "content": "no"}
    )
    assert traversal.status_code == 422
    rename_collision = client.patch(
        f"/writer/{doc['public_id']}/files/{method['id']}", json={"path": "sections/results.tex"}
    )
    assert rename_collision.status_code == 409


def test_writer_assistant_and_assets(corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64 as b64
    import json as jsonlib
    from io import BytesIO
    from types import SimpleNamespace

    from PIL import Image
    from pypdf import PdfWriter

    client = _authed(create_app())
    doc = client.post("/writer", json={"title": "Draft", "template": "blank"}).json()

    class FakePool:
        def has_strong(self) -> bool:
            return True

        def pinned(self, ref: object):
            return self

        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            assert "figure.png" in prompt
            payload = {
                "reply": "I added a figure after the documentclass line.",
                "edits": [
                    {
                        "find": "\\documentclass{article}",
                        "replace": "\\documentclass{article}\n\\usepackage{graphicx}",
                    }
                ],
            }
            return SimpleNamespace(text=jsonlib.dumps(payload), provider="fake", model="f1")

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    png_buffer = BytesIO()
    Image.new("RGB", (2, 2), (34, 98, 76)).save(png_buffer, format="PNG")
    png = b64.b64encode(png_buffer.getvalue()).decode()
    uploaded = client.post(
        f"/writer/{doc['id']}/assets", json={"filename": "My Figure.png", "content_base64": png}
    ).json()
    assert uploaded["filename"] == "My-Figure.png"
    pdf_buffer = BytesIO()
    pdf_writer = PdfWriter()
    pdf_writer.add_blank_page(width=120, height=120)
    pdf_writer.write(pdf_buffer)
    blank_pdf = b64.b64encode(pdf_buffer.getvalue()).decode()
    pdf_asset = client.post(
        f"/writer/{doc['id']}/assets", json={"filename": "paper.pdf", "content_base64": blank_pdf}
    ).json()
    assert pdf_asset["readable"] is False
    from sixsentences_server.writer.assistant import build_context

    ctx = build_context(
        {"main.tex": "src"},
        "main.tex",
        [],
        [],
        [],
        attachments=[{"filename": "p.pdf", "text": "The method uses X."}],
    )
    assert "ATTACHED PDF DOCUMENTS" in ctx and "[p.pdf]" in ctx
    client.post(
        f"/writer/{doc['id']}/assets", json={"filename": "figure.png", "content_base64": png}
    )
    assert (
        client.post(
            f"/writer/{doc['id']}/assets", json={"filename": "evil.exe", "content_base64": png}
        ).status_code
        == 422
    )
    from sixsentences_server.writer.service import CompileResult

    candidate_compiles = 0

    def compile_candidate(*args: object, **kwargs: object) -> CompileResult:
        nonlocal candidate_compiles
        del args, kwargs
        candidate_compiles += 1
        return CompileResult(ok=True, log_tail="candidate compiled")

    with monkeypatch.context() as candidate_patch:
        candidate_patch.setattr("sixsentences_server.api.app.compile_document", compile_candidate)
        turn = client.post(
            f"/writer/{doc['id']}/chat", json={"message": "Add graphicx and embed figure.png"}
        ).json()
    assert "figure" in turn["reply"].lower()
    assert len(turn["edits"]) == 1
    assert turn["edits"][0]["applicable"] is True
    assert candidate_compiles == 1
    assert turn["verification"]["status"] == "passed"
    history = client.get(f"/writer/{doc['id']}/chat").json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["payload"]["edits"][0]["find"].startswith("\\documentclass")
    assert history[1]["payload"]["visual_request"] is None

    class VisualPool:
        def has_strong(self) -> bool:
            return True

        def pinned(self, ref: object):
            return self

        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            return SimpleNamespace(
                text=jsonlib.dumps(
                    {
                        "reply": "I prepared a reviewable scientific visual brief.",
                        "edits": [],
                        "visual_request": {
                            "prompt": "A publication-ready evidence screening workflow with uncertainty-aware human verification.",
                            "kind": "flow",
                            "aspect_ratio": "4:3",
                            "resolution": "2k",
                            "review_passes": 2,
                        },
                    }
                ),
                provider="fake",
                model="f1",
            )

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: VisualPool())
    visual_turn = client.post(
        f"/writer/{doc['id']}/chat", json={"message": "Create a new scientific workflow figure."}
    ).json()
    assert visual_turn["edits"] == []
    assert visual_turn["visual_request"]["kind"] == "flow"
    assert visual_turn["visual_request"]["aspect_ratio"] == "4:3"
    assert visual_turn["visual_request"]["resolution"] == "2k"
    assert visual_turn["visual_request"]["review_passes"] == 2
    assert "Scientific objective:" in visual_turn["visual_request"]["prompt"]
    assert "uncertainty-aware human verification" in visual_turn["visual_request"]["prompt"]
    assert "do not turn metadata counts into outcome" in visual_turn["visual_request"]["prompt"]
    history = client.get(f"/writer/{doc['id']}/chat").json()
    assert history[-1]["payload"]["visual_request"]["kind"] == "flow"
    import importlib

    from sixsentences_server.config import get_settings

    figure_settings = get_settings().model_copy(
        update={
            "openrouter_api_key": "test-openrouter-key",
            "gemini_api_key": "test-google-key",
            "gemini_data_processing_confirmed": True,
        }
    )
    api_module = importlib.import_module("sixsentences_server.api.app")
    original_get_settings = api_module.get_settings
    original_enqueue_job = api_module.enqueue_job
    monkeypatch.setattr("sixsentences_server.api.app.get_settings", lambda: figure_settings)
    monkeypatch.setattr(
        "sixsentences_server.api.app.enqueue_job",
        lambda background, function, *args, **kwargs: None,
    )
    confirmed_body = {
        **visual_turn["visual_request"],
        "model": "auto",
        "writer_document_id": doc["public_id"],
        "writer_message_id": visual_turn["id"],
    }
    confirmed = client.post("/figures", json=confirmed_body)
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["config"]["writer_document_id"] == doc["public_id"]
    duplicate = client.post("/figures", json=confirmed_body)
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["public_id"] == confirmed.json()["public_id"]
    mismatched = client.post(
        "/figures", json={**confirmed_body, "prompt": "A different unconfirmed prompt."}
    )
    assert mismatched.status_code == 409
    monkeypatch.setattr("sixsentences_server.api.app.get_settings", original_get_settings)
    monkeypatch.setattr("sixsentences_server.api.app.enqueue_job", original_enqueue_job)
    import sys

    probe = f'''{sys.executable} -c "import pathlib,sys;assert pathlib.Path('figure.png').exists(), 'asset missing';pathlib.Path('main.pdf').write_bytes(b'%PDF-1.4 ok')"'''
    monkeypatch.setenv("SIX_TECTONIC_CMD", probe)
    from sixsentences_server.config import get_settings

    get_settings.cache_clear()
    client.post(f"/writer/{doc['id']}/compile")
    assert client.get(f"/writer/{doc['id']}").json()["compile_status"] == "ok"
    fail = f'''{sys.executable} -c "print('! Undefined control sequence.'); print('l.3 \\\\nope'); raise SystemExit(1)"'''
    monkeypatch.setenv("SIX_TECTONIC_CMD", fail)
    get_settings.cache_clear()
    client.post(f"/writer/{doc['id']}/compile")
    assert client.get(f"/writer/{doc['id']}").json()["compile_status"] == "error"

    class DebugPool(FakePool):
        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            assert "LATEST COMPILE FAILED" in prompt
            assert "Undefined control sequence" in prompt
            assert "debugger" in system
            payload = {"reply": "The macro on line 3 does not exist.", "edits": []}
            return SimpleNamespace(text=jsonlib.dumps(payload), provider="fake", model="f1")

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: DebugPool())
    debugged = client.post(
        f"/writer/{doc['id']}/chat", json={"message": "Fix the compile errors."}
    ).json()
    assert "line 3" in debugged["reply"]
    get_settings.cache_clear()


def test_writer_apply_accepts_verified_proposal_with_more_than_five_edits(
    corpus: DuckDBCorpus,
) -> None:
    client = _authed(create_app())
    doc = client.post(
        "/writer", json={"title": "Broad verified proposal", "template": "blank"}
    ).json()
    source = "\n".join(f"Placeholder {index}." for index in range(1, 7))
    client.patch(f"/writer/{doc['public_id']}", json={"content": source})
    proposed = [
        {
            "path": "main.tex",
            "find": f"Placeholder {index}.",
            "replace": f"Completed section {index}.",
            "applicable": True,
        }
        for index in range(1, 7)
    ]
    with db_session() as session:
        document = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == doc["public_id"])
        )
        assert document is not None
        assistant_message = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="I prepared six sequentially verified source edits.",
            payload={"edits": proposed, "verification": {"status": "passed", "errors": []}},
        )
        session.add(assistant_message)
        session.flush()
        message_id = assistant_message.id
    response = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={
            "message_id": message_id,
            "edits": [
                {"path": edit["path"], "find": edit["find"], "replace": edit["replace"]}
                for edit in proposed
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == list(range(6))
    files = client.get(f"/writer/{doc['public_id']}/files").json()
    main = next(row["content"] for row in files if row["path"] == "main.tex")
    assert "Placeholder" not in main
    assert "Completed section 6." in main


def test_writer_edit_review_rejection_is_durable_and_exclusive(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    doc = client.post("/writer", json={"title": "Inline review", "template": "blank"}).json()
    client.patch(
        f"/writer/{doc['public_id']}",
        json={"content": "First anchor.\nSecond anchor.\nThird anchor.\n"},
    )
    proposals = [
        {
            "path": "main.tex",
            "find": "First anchor.",
            "replace": "Approved first replacement.",
            "applicable": True,
        },
        {
            "path": "main.tex",
            "find": "Second anchor.",
            "replace": "Rejected second replacement.",
            "applicable": True,
        },
        {
            "path": "main.tex",
            "find": "Third anchor.",
            "replace": "Superseded third replacement.",
            "applicable": True,
        },
    ]
    with db_session() as session:
        document = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == doc["public_id"])
        )
        assert document is not None
        assistant_message = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="Review these two edits.",
            payload={"edits": proposals, "verification": {"status": "passed", "errors": []}},
        )
        session.add(assistant_message)
        session.flush()
        message_id = assistant_message.id
        document.compile_status = "ok"
        document_id = document.id
    from sixsentences_server.config import get_settings

    pdf_path = get_settings().data_dir / "writer" / f"{document_id}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n% stale review fixture\n")
    share = client.post(f"/writer/{doc['public_id']}/share", json={})
    assert share.status_code == 201, share.text
    public_pdf_url = f"/public/writer/{share.json()['token']}/pdf"
    public = TestClient(client.app)
    assert public.get(public_pdf_url).status_code == 200
    out_of_order = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={"message_id": message_id, "edits": [proposals[1]]},
    )
    assert out_of_order.status_code == 409
    assert "review the earlier proposed edits first" in out_of_order.json()["detail"]
    applied = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={"message_id": message_id, "edits": [proposals[0]]},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == [0]
    applied_main = next(row for row in applied.json()["files"] if row["path"] == "main.tex")
    assert applied_main["revision"] == client.get(f"/writer/{doc['public_id']}").json()["revision"]
    assert public.get(public_pdf_url).status_code == 409
    rejected = client.post(
        f"/writer/{doc['public_id']}/edits/reject", json={"message_id": message_id, "index": 1}
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["idempotent"] is False
    assert rejected.json()["superseded"] == [2]
    repeated = client.post(
        f"/writer/{doc['public_id']}/edits/reject", json={"message_id": message_id, "index": 1}
    )
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert repeated.json()["superseded"] == [2]
    blocked_apply = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={"message_id": message_id, "edits": [proposals[1]]},
    )
    assert blocked_apply.status_code == 409
    assert "was rejected" in blocked_apply.json()["detail"]
    blocked_dependent_apply = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={"message_id": message_id, "edits": [proposals[2]]},
    )
    assert blocked_dependent_apply.status_code == 409
    assert "depends on an earlier rejected edit" in blocked_dependent_apply.json()["detail"]
    blocked_reject = client.post(
        f"/writer/{doc['public_id']}/edits/reject", json={"message_id": message_id, "index": 0}
    )
    assert blocked_reject.status_code == 409
    assert "already applied" in blocked_reject.json()["detail"]
    main = next(
        row["content"]
        for row in client.get(f"/writer/{doc['public_id']}/files").json()
        if row["path"] == "main.tex"
    )
    assert "Approved first replacement." in main
    assert "Second anchor." in main
    assert "Third anchor." in main
    assert "Rejected second replacement." not in main
    assert "Superseded third replacement." not in main
    assert client.get(f"/writer/{doc['public_id']}").json()["compile_status"] == "none"
    assert client.get(f"/writer/{doc['public_id']}/pdf").status_code == 409
    history = client.get(f"/writer/{doc['public_id']}/chat").json()
    assistant = next(row for row in history if row["id"] == message_id)
    assert assistant["payload"]["edit_decisions"] == {
        "0": "applied",
        "1": "rejected",
        "2": "superseded",
    }
    rejected_events = [
        event
        for event in assistant["payload"]["agent_events"]
        if event.get("event") == "change.rejected"
    ]
    assert len(rejected_events) == 1
    assert rejected_events[0]["output"]["proposal_index"] == 1


def test_writer_edit_review_apply_and_reject_are_serialized(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    doc = client.post(
        "/writer", json={"title": "Concurrent inline review", "template": "blank"}
    ).json()
    client.patch(f"/writer/{doc['public_id']}", json={"content": "One review anchor."})
    proposal = {
        "path": "main.tex",
        "find": "One review anchor.",
        "replace": "One accepted replacement.",
        "applicable": True,
    }
    with db_session() as session:
        document = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == doc["public_id"])
        )
        assert document is not None
        message = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="Review this edit.",
            payload={"edits": [proposal], "verification": {"status": "passed", "errors": []}},
        )
        session.add(message)
        session.flush()
        message_id = message.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        apply_future = pool.submit(
            client.post,
            f"/writer/{doc['public_id']}/edits/apply",
            json={"message_id": message_id, "edits": [proposal]},
        )
        reject_future = pool.submit(
            client.post,
            f"/writer/{doc['public_id']}/edits/reject",
            json={"message_id": message_id, "index": 0},
        )
        responses = [apply_future.result(), reject_future.result()]
    assert sorted(response.status_code for response in responses) == [200, 409]
    history = client.get(f"/writer/{doc['public_id']}/chat").json()
    assistant = next(row for row in history if row["id"] == message_id)
    decision = assistant["payload"]["edit_decisions"]["0"]
    main = next(
        row["content"]
        for row in client.get(f"/writer/{doc['public_id']}/files").json()
        if row["path"] == "main.tex"
    )
    if decision == "applied":
        assert main == "One accepted replacement."
    else:
        assert decision == "rejected"
        assert main == "One review anchor."


def test_writer_compile_discards_output_after_source_revision_changes(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from sixsentences_server.api.app import _execute_writer_compile
    from sixsentences_server.config import get_settings

    client = _authed(create_app())
    doc = client.post(
        "/writer", json={"title": "Compile revision fence", "template": "blank"}
    ).json()
    client.patch(f"/writer/{doc['public_id']}", json={"content": "Old source revision."})
    with db_session() as session:
        document = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == doc["public_id"])
        )
        assert document is not None
        document.compile_status = "running"
        document_id = document.id
    pdf_path = get_settings().data_dir / "writer" / f"{document_id}.pdf"
    pdf_path.unlink(missing_ok=True)
    compile_started = threading.Event()
    release_compile = threading.Event()

    def compile_old_revision(*_args: object, **_kwargs: object) -> object:
        compile_started.set()
        assert release_compile.wait(timeout=5)
        return SimpleNamespace(
            ok=True,
            pdf=b"%PDF-1.4\n% stale compile\n",
            synctex=None,
            log_tail="old source compiled",
        )

    monkeypatch.setattr("sixsentences_server.api.app.compile_document", compile_old_revision)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_execute_writer_compile, document_id)
        assert compile_started.wait(timeout=5)
        changed = client.patch(
            f"/writer/{doc['public_id']}", json={"content": "Approved new source revision."}
        )
        assert changed.status_code == 200, changed.text
        release_compile.set()
        future.result(timeout=5)
    current = client.get(f"/writer/{doc['public_id']}").json()
    assert current["content"] == "Approved new source revision."
    assert current["compile_status"] == "none"
    assert pdf_path.exists() is False
    assert client.get(f"/writer/{doc['public_id']}/pdf").status_code == 409


def test_writer_project_edits_audit_and_semantic_versions(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    doc = client.post("/writer", json={"title": "Project intelligence", "template": "blank"}).json()
    main = "\\documentclass{article}\n\\begin{document}\nThe intervention improved accuracy by 12 percent.\nSee \\cref{sec:missing} and \\citep{unknown2026}.\n\\input{sections/method}\n\\end{document}\n"
    client.patch(f"/writer/{doc['public_id']}", json={"content": main})
    method = client.post(
        f"/writer/{doc['public_id']}/files",
        json={
            "path": "sections/method.tex",
            "content": "\\section{Method}\\label{sec:method}\nOriginal method.\n",
        },
    ).json()
    snapshot = client.post(
        f"/writer/{doc['public_id']}/snapshots", json={"note": "baseline"}
    ).json()
    with db_session() as session:
        document = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == doc["public_id"])
        )
        assert document is not None
        assistant_message = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="I prepared two source edits.",
            payload={
                "edits": [
                    {
                        "path": "main.tex",
                        "find": "improved accuracy",
                        "replace": "improved held-out accuracy",
                        "applicable": True,
                    },
                    {
                        "path": "sections/method.tex",
                        "find": "Original method.",
                        "replace": "Reproducible method.",
                        "applicable": True,
                    },
                ],
                "verification": {"status": "passed", "errors": []},
                "agent_events": [
                    {
                        "event": "change.proposed",
                        "tool": "manuscript.edit_source",
                        "label": "Edit main.tex",
                    }
                ],
            },
        )
        session.add(assistant_message)
        session.flush()
        assistant_message_id = assistant_message.id
        failed_message = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="This proposal did not compile.",
            payload={
                "edits": [
                    {
                        "path": "main.tex",
                        "find": "improved accuracy",
                        "replace": "broken replacement",
                        "applicable": True,
                    }
                ],
                "verification": {
                    "status": "failed",
                    "errors": [{"message": "Undefined control sequence"}],
                },
            },
        )
        session.add(failed_message)
        session.flush()
        failed_message_id = failed_message.id
        stale_message = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="I prepared an atomic source-edit batch.",
            payload={
                "edits": [
                    {
                        "path": "main.tex",
                        "find": "improved accuracy",
                        "replace": "must not land partially",
                        "applicable": True,
                    },
                    {
                        "path": "sections/method.tex",
                        "find": "A stale method anchor.",
                        "replace": "must not land",
                        "applicable": True,
                    },
                ],
                "verification": {"status": "passed", "errors": []},
            },
        )
        session.add(stale_message)
        session.flush()
        stale_message_id = stale_message.id
    edit_payload = {
        "message_id": assistant_message_id,
        "edits": [
            {
                "path": "main.tex",
                "find": "improved accuracy",
                "replace": "improved held-out accuracy",
            },
            {
                "path": "sections/method.tex",
                "find": "Original method.",
                "replace": "Reproducible method.",
            },
        ],
    }
    blocked = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={"message_id": failed_message_id, "edits": [edit_payload["edits"][0]]},
    )
    assert blocked.status_code == 409
    assert "failed manuscript verification" in blocked.json()["detail"]
    unchanged = {
        row["path"]: row["content"]
        for row in client.get(f"/writer/{doc['public_id']}/files").json()
    }
    assert "held-out" not in unchanged["main.tex"]
    missing_provenance = client.post(
        f"/writer/{doc['public_id']}/edits/apply", json={"edits": [edit_payload["edits"][0]]}
    )
    assert missing_provenance.status_code == 400
    forged = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={
            "message_id": assistant_message_id,
            "edits": [
                {**edit_payload["edits"][0], "replace": "a replacement that was never proposed"}
            ],
        },
    )
    assert forged.status_code == 409
    assert "not in the proposal" in forged.json()["detail"]
    incomplete_auto_apply = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={
            "message_id": assistant_message_id,
            "auto": True,
            "edits": [edit_payload["edits"][0]],
        },
    )
    assert incomplete_auto_apply.status_code == 409
    assert "complete verified proposal" in incomplete_auto_apply.json()["detail"]
    atomic = client.post(
        f"/writer/{doc['public_id']}/edits/apply",
        json={
            "message_id": stale_message_id,
            "edits": [
                {
                    "path": "main.tex",
                    "find": "improved accuracy",
                    "replace": "must not land partially",
                },
                {
                    "path": "sections/method.tex",
                    "find": "A stale method anchor.",
                    "replace": "must not land",
                },
            ],
        },
    )
    assert atomic.status_code == 200
    assert atomic.json()["applied"] == []
    assert {row["index"] for row in atomic.json()["skipped"]} == {0, 1}
    atomic_files = {
        row["path"]: row["content"]
        for row in client.get(f"/writer/{doc['public_id']}/files").json()
    }
    assert "must not land" not in atomic_files["main.tex"]
    atomic_history = client.get(f"/writer/{doc['public_id']}/chat").json()
    atomic_message = next(row for row in atomic_history if row["id"] == stale_message_id)
    apply_failures = [
        event
        for event in atomic_message["payload"]["agent_events"]
        if event.get("event") == "tool.failed" and event.get("tool") == "manuscript.apply_change"
    ]
    assert len(apply_failures) == 1
    assert isinstance(apply_failures[0]["id"], int)
    assert apply_failures[0]["output"]["applied"] is False
    applied = client.post(f"/writer/{doc['public_id']}/edits/apply", json=edit_payload)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == [0, 1]
    assert applied.json()["idempotent"] is False
    files = client.get(f"/writer/{doc['public_id']}/files").json()
    sources = {row["path"]: row["content"] for row in files}
    assert "held-out" in sources["main.tex"]
    assert "Reproducible" in sources[method["path"]]
    history = client.get(f"/writer/{doc['public_id']}/chat").json()
    assistant = next(row for row in history if row["id"] == assistant_message_id)
    completed = [
        event
        for event in assistant["payload"]["agent_events"]
        if event.get("event") == "change.completed"
        and event.get("tool") == "manuscript.apply_change"
    ]
    assert [event["index"] for event in completed] == [1, 2]
    assert [event["before"] for event in completed] == ["improved accuracy", "Original method."]
    assert [event["after"] for event in completed] == [
        "improved held-out accuracy",
        "Reproducible method.",
    ]
    with db_session() as session:
        receipts = session.scalars(
            select(WriterContributionRow).where(
                WriterContributionRow.document_id == doc["id"],
                WriterContributionRow.kind == "ai_edit_application",
            )
        ).all()
        assert len(receipts) == 1
        session.delete(receipts[0])
    repeated = client.post(f"/writer/{doc['public_id']}/edits/apply", json=edit_payload)
    assert repeated.status_code == 200
    assert repeated.json()["applied"] == [0, 1]
    assert repeated.json()["skipped"] == []
    assert repeated.json()["idempotent"] is True
    repeated_history = client.get(f"/writer/{doc['public_id']}/chat").json()
    repeated_assistant = next(row for row in repeated_history if row["id"] == assistant_message_id)
    repeated_completed = [
        event
        for event in repeated_assistant["payload"]["agent_events"]
        if event.get("event") == "change.completed"
        and event.get("tool") == "manuscript.apply_change"
    ]
    assert len(repeated_completed) == 2
    assert not any(
        event.get("event") == "tool.failed" and event.get("tool") == "manuscript.apply_change"
        for event in repeated_assistant["payload"]["agent_events"]
    )
    audit = client.get(f"/writer/{doc['public_id']}/audit").json()
    titles = {row["title"] for row in audit["findings"]}
    assert "Unknown citation key: unknown2026" in titles
    assert "Missing cross-reference target: sec:missing" in titles
    assert audit["files_checked"] == 2
    versions = client.get(f"/writer/{doc['public_id']}/snapshots").json()
    assert versions[0]["files"] == 2
    assert versions[0]["summary"]
    diff = client.get(f"/writer/{doc['public_id']}/snapshots/{versions[0]['id']}/diff").json()
    assert "files" in diff
    restored = client.post(f"/writer/{doc['public_id']}/restore/{snapshot['id']}")
    assert restored.status_code == 200
    restored_files = client.get(f"/writer/{doc['public_id']}/files").json()
    restored_sources = {row["path"]: row["content"] for row in restored_files}
    assert "improved accuracy" in restored_sources["main.tex"]
    assert "Original method." in restored_sources["sections/method.tex"]


def test_parallel_writer_apply_replays_one_verified_edit_set_once(corpus: DuckDBCorpus) -> None:
    app = create_app()
    client = _authed(app)
    doc = client.post("/writer", json={"title": "Idempotent apply", "template": "blank"}).json()
    client.patch(
        f"/writer/{doc['public_id']}",
        json={
            "content": "\\documentclass{article}\n\\begin{document}\nOriginal sentence.\n\\end{document}\n"
        },
    )
    with db_session() as session:
        document = session.scalar(
            select(WriterDocumentRow).where(WriterDocumentRow.public_id == doc["public_id"])
        )
        assert document is not None
        revision_before_apply = document.revision
        proposal = WriterMessageRow(
            org_id=document.org_id,
            document_id=document.id,
            role="assistant",
            content="I prepared one verified source edit.",
            payload={
                "edits": [
                    {
                        "path": "main.tex",
                        "find": "Original sentence.",
                        "replace": "Revised sentence.",
                        "applicable": True,
                    }
                ],
                "verification": {"status": "passed", "errors": []},
                "agent_events": [],
            },
        )
        session.add(proposal)
        session.flush()
        proposal_id = proposal.id
        document_id = document.id
    payload = {
        "message_id": proposal_id,
        "auto": True,
        "edits": [
            {"path": "main.tex", "find": "Original sentence.", "replace": "Revised sentence."}
        ],
    }
    peer = TestClient(app)
    peer.headers["Authorization"] = client.headers["Authorization"]
    both_ready = threading.Barrier(2)

    def apply_once(request_client: TestClient):
        both_ready.wait(timeout=3)
        return request_client.post(f"/writer/{doc['public_id']}/edits/apply", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(apply_once, (client, peer)))
    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()["idempotent"] for response in responses) == [False, True]
    assert all(response.json()["applied"] == [0] for response in responses)
    assert all(response.json()["skipped"] == [] for response in responses)
    with db_session() as session:
        document = session.get(WriterDocumentRow, document_id)
        assert document is not None
        assert document.revision == revision_before_apply + 1
        assert document.content.count("Revised sentence.") == 1
        assert "Original sentence." not in document.content
        contributions = session.scalars(
            select(WriterContributionRow).where(WriterContributionRow.document_id == document_id)
        ).all()
        assert sum(row.kind == "ai_edit_applied" for row in contributions) == 1
        assert sum(row.kind == "ai_edit_application" for row in contributions) == 1
        persisted_proposal = session.get(WriterMessageRow, proposal_id)
        assert persisted_proposal is not None
        apply_events = [
            event
            for event in (persisted_proposal.payload or {}).get("agent_events", [])
            if event.get("tool") == "manuscript.apply_change"
        ]
        assert [event["event"] for event in apply_events] == ["change.completed"]


def test_writer_owned_sources_and_research_data(corpus: DuckDBCorpus) -> None:
    import base64

    client = _authed(create_app())
    doc = client.post("/writer", json={"title": "Study", "template": "blank"}).json()
    bib = b"@article{ignored,\n      title={A user supplied source},\n      author={Doe, Jane and Smith, Kai},\n      year={2024},\n      doi={10.1000/example}\n    }"
    imported = client.post(
        f"/writer/{doc['public_id']}/sources",
        json={"filename": "library.bib", "content_base64": base64.b64encode(bib).decode()},
    )
    assert imported.status_code == 201, imported.text
    source = imported.json()[0]
    assert source["title"] == "A user supplied source"
    assert source["cite_key"]
    citations = client.get(f"/writer/{doc['public_id']}/citations").json()
    assert any(row["owned"] and row["key"] == source["cite_key"] for row in citations)
    bibliography = client.get(f"/writer/{doc['public_id']}/references.bib").text
    assert f"@article{{{source['cite_key']}," in bibliography
    csv_blob = b"group,outcome\ncontrol,4.2\ntreatment,7.1\ntreatment,8.0\n"
    created = client.post(
        "/datasets",
        json={
            "filename": "outcomes.csv",
            "content_base64": base64.b64encode(csv_blob).decode(),
            "name": "Trial outcomes",
            "description": "One row per participant",
            "provenance": "Trial S-04 export",
            "license": "Restricted",
        },
    )
    assert created.status_code == 201, created.text
    dataset = created.json()
    assert dataset["row_count"] == 3
    assert dataset["column_count"] == 2
    outcome = next(column for column in dataset["columns"] if column["name"] == "outcome")
    assert outcome["stats"]["mean"] == pytest.approx(6.433333, rel=0.0001)
    linked = client.patch(
        f"/writer/{doc['public_id']}", json={"dataset_ids": [dataset["public_id"]]}
    ).json()
    assert linked["dataset_ids"] == [dataset["public_id"]]
    chart = client.post(
        f"/datasets/{dataset['public_id']}/chart",
        json={
            "x_column": "group",
            "y_column": "outcome",
            "kind": "bar",
            "title": "Outcome by group",
        },
    )
    assert chart.status_code == 201, chart.text
    assert chart.json()["config"]["exact"] is True
    assert client.get(f"/figures/{chart.json()['public_id']}/image").content.startswith(b"\x89PNG")


def test_figure_rename_sets_custom_title(settings) -> None:
    from sixsentences_server.core.db import FigureRow, db_session

    client = _authed(create_app())
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        fig = FigureRow(org_id=org_id, prompt="A pipeline diagram", status="ok")
        session.add(fig)
        session.flush()
        figure_id = fig.public_id
    renamed = client.patch(f"/figures/{figure_id}", json={"title": "Figure 1: Screening funnel"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["config"]["title"] == "Figure 1: Screening funnel"
    cleared = client.patch(f"/figures/{figure_id}", json={"title": "  "})
    assert cleared.status_code == 200, cleared.text
    assert "title" not in cleared.json()["config"]


def test_figure_can_move_only_to_a_project_in_its_workspace(settings) -> None:
    from sixsentences_server.core.db import FigureRow, Project, db_session

    client = _authed(create_app())
    org_id = client.get("/auth/me").json()["org_id"]
    project = client.post("/projects", json={"name": "Figure destination"}).json()
    with db_session() as session:
        figure = FigureRow(org_id=org_id, prompt="A pipeline diagram", status="ok")
        other_org = Org(name="Other workspace")
        session.add_all([figure, other_org])
        session.flush()
        other_project = Project(org_id=other_org.id, name="Foreign project")
        session.add(other_project)
        session.flush()
        figure_id = figure.public_id
        foreign_project_id = other_project.id
    moved = client.patch(f"/figures/{figure_id}", json={"project_id": project["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_id"] == project["id"]
    rejected = client.patch(f"/figures/{figure_id}", json={"project_id": foreign_project_id})
    assert rejected.status_code == 404


def test_public_run_share_link(corpus: DuckDBCorpus) -> None:
    client = _authed(create_app())
    project = client.post("/projects", json={"name": "share"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]
    created = client.post(f"/runs/{run_id}/share")
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert len(token) == 26
    assert "/share/r/" in created.json()["url"]
    assert client.get(f"/runs/{run_id}/share").json()["shared"] is True
    public = TestClient(client.app)
    payload = public.get(f"/public/runs/{token}")
    assert payload.status_code == 200, payload.text
    body = payload.json()
    assert body["question"] == "transformers"
    assert body["query_string"]
    assert body["prisma"]["records_identified"] >= 1
    assert body["works"], "the record must show its works"
    assert all("org_id" not in work for work in body["works"])
    assert body["methods"]
    svg = public.get(f"/public/runs/{token}/prisma.svg")
    assert svg.status_code == 200
    assert "<svg" in svg.text[:300]
    assert client.delete(f"/runs/{run_id}/share").json() == {"ok": True}
    assert public.get(f"/public/runs/{token}").status_code == 404
    assert public.get(f"/public/runs/{token}/prisma.svg").status_code == 404


def test_custom_extraction_schema_and_force_reextract(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as jsonlib
    from types import SimpleNamespace

    client = _authed(create_app())
    project = client.post("/projects", json={"name": "schema"}).json()
    run_id = client.post(
        f"/projects/{project['id']}/runs",
        json={"question": "transformers", "query": "transformer", "screen": True},
    ).json()["id"]

    class FakePool:
        def has_strong(self) -> bool:
            return True

        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            fields = jsonlib.loads(prompt.split("Fields to extract: ", 1)[1].split("\n", 1)[0])
            payload = {f: {"value": f"{f} value", "quote": "", "page": None} for f in fields}
            return SimpleNamespace(
                text=jsonlib.dumps({"fields": payload}), provider="fake", model="f1"
            )

    monkeypatch.setattr("sixsentences_server.api.app.available_specs", lambda: ["fake"])
    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    client.post(
        f"/runs/{run_id}/decisions",
        json=[{"work_id": "W1", "verdict": "include", "reason": "seed"}],
    )
    custom = ["baseline", "dataset", "metric", "score", "seed"]
    started = client.post(f"/runs/{run_id}/extraction", json={"fields": custom})
    assert started.status_code == 202
    assert started.json()["fields"] == custom
    table = client.get(f"/runs/{run_id}/extraction").json()
    assert table["fields"] == custom
    assert table["rows"][0]["payload"]["seed"]["value"] == "seed value"
    again = client.post(f"/runs/{run_id}/extraction", json={"fields": ["optimizer"]})
    assert again.json()["scheduled"] == 0
    forced = client.post(
        f"/runs/{run_id}/extraction", json={"fields": ["optimizer"], "force": True}
    )
    assert forced.json()["scheduled"] == 1
    refreshed = client.get(f"/runs/{run_id}/extraction").json()
    assert "optimizer" in refreshed["fields"]
    assert refreshed["rows"][0]["payload"]["optimizer"]["value"] == "optimizer value"


def test_writer_slides_drafted_from_manuscript(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    client = _authed(create_app())
    doc = client.post("/writer", json={"title": "Screening review", "template": "blank"}).json()
    content = "\\documentclass{article}\\begin{document}Our recall reaches 0.91.\\end{document}"
    client.patch(f"/writer/{doc['public_id']}", json={"content": content})
    deck = "\\documentclass[aspectratio=169]{beamer}\n\\usetheme{metropolis}\n\\begin{document}\n\\begin{frame}{Main result}\nRecall reaches 0.91.\n\\end{frame}\n\\end{document}"

    class FakePool:
        def has_strong(self) -> bool:
            return True

        def complete(self, task: object, *, system: str, prompt: str, max_tokens: int):
            assert "0.91" in prompt, "the deck must see the manuscript"
            return SimpleNamespace(text=f"```latex\n{deck}\n```", provider="fake", model="f1")

    monkeypatch.setattr("sixsentences_server.api.app._build_pool", lambda settings: FakePool())
    created = client.post(f"/writer/{doc['public_id']}/slides")
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["title"].startswith("Slides:")
    fetched = client.get(f"/writer/{payload['public_id']}").json()
    assert "\\begin{frame}" in fetched["content"]
    assert "metropolis" in fetched["content"]


def test_writer_share_review_comments(settings) -> None:
    client = _authed(create_app())
    doc = client.post("/writer", json={"title": "Thesis draft", "template": "blank"}).json()
    created = client.post(f"/writer/{doc['public_id']}/share", json={"password": "reviewpass"})
    assert created.status_code == 201, created.text
    share = created.json()
    assert share["password_protected"] is True
    token = share["token"]
    assert len(token) == 26
    public = TestClient(client.app)
    meta = public.get(f"/public/writer/{token}")
    assert meta.status_code == 200
    assert meta.json()["password_protected"] is True
    assert meta.json()["title"] == "Protected manuscript"
    assert public.get(f"/public/writer/{token}/comments").status_code == 401
    denied = public.post(f"/public/writer/{token}/access", json={"password": "nope"})
    assert denied.status_code == 401
    assert (
        public.post(f"/public/writer/{token}/access", json={"password": "reviewpass"}).status_code
        == 200
    )
    headers = {"x-share-password": "reviewpass"}
    comment = public.post(
        f"/public/writer/{token}/comments",
        json={
            "author_label": "Prof. X",
            "author_key": "reviewer-prof-x",
            "quote": "Our recall reaches 0.91.",
            "anchor_prefix": "On the held-out benchmark,",
            "anchor_suffix": "with a narrow confidence interval.",
            "anchor_revision": "2026-07-27T09:30:00Z",
            "page": 3,
            "content": "Back this number with the confidence interval.",
            "password": "reviewpass",
        },
    )
    assert comment.status_code == 201, comment.text
    comment_id = comment.json()["id"]
    listed = public.get(f"/public/writer/{token}/comments", headers=headers).json()
    assert [item["id"] for item in listed] == [comment_id]
    assert listed[0]["quote"].startswith("Our recall")
    assert listed[0]["author_key"] == "reviewer-prof-x"
    assert listed[0]["anchor_prefix"].startswith("On the held-out")
    assert listed[0]["anchor_revision"] == "2026-07-27T09:30:00Z"
    assert 0 <= listed[0]["color_index"] <= 5
    own_comment = client.post(
        f"/writer/{doc['public_id']}/comments",
        json={"content": "Recheck the discussion after the robustness analysis."},
    )
    assert own_comment.status_code == 201, own_comment.text
    assert own_comment.json()["author_key"].startswith("user:")
    assert own_comment.json()["page"] is None
    collaborative_view = public.get(f"/public/writer/{token}/comments", headers=headers).json()
    assert [item["id"] for item in collaborative_view] == [comment_id, own_comment.json()["id"]]
    assert public.get(f"/public/writer/{token}/pdf", headers=headers).status_code == 409
    owner_view = client.get(f"/writer/{doc['public_id']}/comments").json()
    assert [item["id"] for item in owner_view] == [comment_id, own_comment.json()["id"]]
    assert owner_view[0]["author_label"] == "Prof. X"
    resolved = client.post(f"/writer/{doc['public_id']}/comments/{comment_id}/resolve").json()
    assert resolved["status"] == "resolved"
    assert client.delete(f"/writer/{doc['public_id']}/share").json() == {"ok": True}
    assert public.get(f"/public/writer/{token}").status_code == 404
    remaining = client.get(f"/writer/{doc['public_id']}/comments").json()
    assert remaining == []


def test_figure_redraw_from_upload_and_existing_figure(
    corpus: DuckDBCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rough draft (or an existing figure) becomes the model's source image;
    provenance survives redraw chains and bad inputs are refused up front."""
    import base64
    import io

    from PIL import Image

    from sixsentences_server.config import get_settings

    client = _authed(create_app())
    monkeypatch.setenv("SIX_OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "test-google-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    seen: dict[str, object] = {}

    def fake_render(
        prompt, *, api_key, model, context=None, provider="google", source_image=None, **kwargs
    ) -> bytes:
        seen["prompt"] = prompt
        seen["source_image"] = source_image
        out = io.BytesIO()
        Image.new("RGB", (200, 150), "white").save(out, format="PNG")
        return out.getvalue()

    monkeypatch.setattr("sixsentences_server.api.app.render_figure", fake_render)
    sketch = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(sketch, format="JPEG")
    sketch_b64 = base64.b64encode(sketch.getvalue()).decode()
    both = client.post(
        "/figures",
        json={
            "prompt": "Make it professional",
            "source_image_base64": sketch_b64,
            "source_figure_id": "abc123",
        },
    )
    assert both.status_code == 422
    created = client.post(
        "/figures",
        json={
            "prompt": "Make it professional",
            "kind": "refine",
            "source_image_base64": sketch_b64,
        },
    ).json()
    fig = client.get(f"/figures/{created['public_id']}").json()
    assert fig["status"] == "ok"
    assert fig["config"]["source"] == {"type": "redraw_upload"}
    assert isinstance(seen["source_image"], bytes)
    assert seen["source_image"].startswith(b"\x89PNG")
    redrawn = client.post(
        "/figures",
        json={"prompt": "Same content, cleaner layout", "source_figure_id": created["public_id"]},
    ).json()
    fig2 = client.get(f"/figures/{redrawn['public_id']}").json()
    assert fig2["status"] == "ok"
    assert fig2["config"]["source"]["type"] == "redraw_figure"
    assert fig2["config"]["source"]["figure_id"] == created["public_id"]
    assert fig2["config"]["source"]["source"] == {"type": "redraw_upload"}
    bad = client.post(
        "/figures",
        json={
            "prompt": "Make it professional",
            "source_image_base64": base64.b64encode(b"not an image").decode(),
        },
    )
    assert bad.status_code == 422
    missing = client.post(
        "/figures", json={"prompt": "Make it professional", "source_figure_id": "zzzznope123"}
    )
    assert missing.status_code == 404


def test_writer_template_share_is_revocable_and_copies_across_orgs(corpus: DuckDBCorpus) -> None:
    app = create_app()
    owner = _authed(app, "template-owner@example.org", "Template owner")
    recipient = _authed(app, "template-recipient@example.org", "Template recipient")
    outsider = _authed(app, "template-outsider@example.org", "Template outsider")
    template = owner.post(
        "/writer/templates",
        json={
            "name": "Reusable conference format",
            "content": "\\documentclass{article}\n\\begin{document}Shared\\end{document}",
        },
    ).json()
    first_share = owner.post(f"/writer/templates/{template['id']}/share")
    assert first_share.status_code == 201, first_share.text
    token = first_share.json()["token"]
    assert len(token) == 26
    repeated = owner.post(f"/writer/templates/{template['id']}/share")
    assert repeated.status_code == 201
    assert repeated.json()["token"] == token
    public = TestClient(app)
    preview = public.get(f"/writer/templates/shared/{token}")
    assert preview.status_code == 200
    assert preview.json()["name"] == "Reusable conference format"
    assert "content" not in preview.json()
    imported = recipient.post(f"/writer/templates/shared/{token}/import")
    assert imported.status_code == 201, imported.text
    assert imported.json()["id"] != template["id"]
    recipient_templates = recipient.get("/writer/templates").json()
    assert [item["name"] for item in recipient_templates] == ["Reusable conference format"]
    assert outsider.delete(f"/writer/templates/{template['id']}/share").status_code == 404
    revoked = owner.delete(f"/writer/templates/{template['id']}/share")
    assert revoked.status_code == 200
    assert public.get(f"/writer/templates/shared/{token}").status_code == 404
    assert recipient.post(f"/writer/templates/shared/{token}/import").status_code == 404
