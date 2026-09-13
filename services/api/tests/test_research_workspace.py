"""Integrated project workspace, study map and evidence lineage."""

import base64

from fastapi.testclient import TestClient

from sixsentences_server.api.app import create_app
from sixsentences_server.research_data import profile_rows, run_analysis


def _client(email: str, org: str) -> TestClient:
    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={"name": "Researcher", "email": email, "password": "StrongPass123!", "org_name": org},
    )
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def test_research_workspace_lifecycle(settings) -> None:
    client = _client("workspace@example.org", "Workspace Lab")
    project = client.post(
        "/projects",
        json={
            "name": "Review of screening systems",
            "kind": "systematic_review",
            "question": "Which systems improve screening quality?",
        },
    ).json()
    project_id = project["id"]
    study = client.post(
        f"/projects/{project_id}/studies",
        json={
            "title": "SCREEN trial",
            "design": "randomized_trial",
            "registry_id": "NCT00000001",
            "report_work_ids": ["W1", "W1-preprint"],
            "outcomes": ["recall", "time"],
        },
    ).json()
    assert len(study["report_work_ids"]) == 2
    task = client.post(
        f"/projects/{project_id}/tasks",
        json={"title": "Resolve screening conflicts", "priority": "high"},
    ).json()
    assert (
        client.patch(f"/projects/{project_id}/tasks/{task['id']}", json={"status": "doing"}).json()[
            "status"
        ]
        == "doing"
    )
    dataset = client.post(
        "/datasets",
        json={
            "filename": "results.csv",
            "name": "Primary outcomes",
            "content_base64": base64.b64encode(b"group,recall\nA,0.91\nB,0.87\n").decode(),
            "project_id": project_id,
        },
    ).json()
    versions = client.get(f"/datasets/{dataset['public_id']}/versions").json()
    assert versions[0]["version"] == 1
    writer = client.post(
        "/writer", json={"title": "Manuscript", "template": "blank", "project_id": project_id}
    ).json()
    survey = client.post(
        "/surveys", json={"title": "Screening workflow survey", "project_id": project_id}
    ).json()
    claim = client.post(
        f"/projects/{project_id}/claims",
        json={
            "text": "The intervention improved screening recall.",
            "section": "Results",
            "writer_document_id": writer["id"],
        },
    ).json()
    client.post(
        f"/projects/{project_id}/claims/{claim['id']}/evidence",
        json={
            "target_type": "dataset",
            "target_id": dataset["public_id"],
            "relationship": "supports",
            "source_version": "1",
            "verified": True,
        },
    )
    client.post(
        f"/projects/{project_id}/risk-of-bias",
        json={
            "study_id": study["id"],
            "tool": "rob2",
            "overall": "low",
            "domains": [{"id": "randomization", "judgment": "low"}],
            "status": "final",
        },
    )
    analysis = client.post(
        f"/projects/{project_id}/analyses",
        json={
            "dataset_id": dataset["public_id"],
            "name": "Recall summary",
            "kind": "descriptive",
            "definition": {"metric": "mean", "column": "recall"},
        },
    ).json()
    assert analysis["dataset_version"] == 1
    research_objects = client.get(f"/writer/{writer['public_id']}/research-objects").json()
    assert research_objects[0]["public_id"] == analysis["public_id"]
    assert "six-live:analysis" in research_objects[0]["latex"]
    assert "\\SixRecallsummaryMean" in research_objects[0]["latex"]
    client.patch(f"/writer/{writer['public_id']}", json={"content": research_objects[0]["latex"]})
    client.post(
        f"/datasets/{dataset['public_id']}/versions",
        json={
            "filename": "results-v2.csv",
            "content_base64": base64.b64encode(b"group,recall\nA,0.92\nB,0.89\n").decode(),
            "note": "updated outcomes",
        },
    )
    writer_audit = client.get(f"/writer/{writer['public_id']}/audit").json()
    assert any(
        finding["title"] == "Live result is out of date" for finding in writer_audit["findings"]
    )
    submission = client.post(
        f"/projects/{project_id}/submissions",
        json={"journal": "Journal of Evidence", "writer_document_id": writer["id"]},
    ).json()
    assert submission["total"] >= 7
    workspace = client.get(f"/projects/{project_id}/workspace").json()
    assert workspace["counts"] == {
        "runs": 0,
        "studies": 1,
        "datasets": 1,
        "interviews": 0,
        "analyses": 1,
        "claims": 1,
        "unsupported_claims": 0,
        "writers": 1,
        "figures": 0,
        "documents": 0,
        "web_sources": 0,
        "surveys": 1,
        "open_tasks": 1,
    }
    assert workspace["surveys"][0]["id"] == survey["public_id"]
    assert workspace["surveys"][0]["response_count"] == 0
    assert workspace["documents"] == []
    assert workspace["claims"][0]["support_count"] == 1
    assert workspace["claims"][0]["unverified_count"] == 0
    manifest = client.get(f"/projects/{project_id}/reproducibility").json()
    assert manifest["conformsTo"].endswith("/ro/crate/1.1")
    assert manifest["inventory"]["studies"] == 1


def test_workspace_is_tenant_scoped(settings) -> None:
    owner = _client("owner@example.org", "Owner Lab")
    project_id = owner.post("/projects", json={"name": "Private"}).json()["id"]
    stranger = _client("stranger@example.org", "Other Lab")
    assert stranger.get(f"/projects/{project_id}/workspace").status_code == 404
    assert (
        stranger.post(f"/projects/{project_id}/claims", json={"text": "steal me"}).status_code
        == 404
    )


def test_deterministic_analysis_recipes() -> None:
    profile = profile_rows(
        [
            {"study": "A", "effect": 0.2, "se": 0.1, "group": "x"},
            {"study": "B", "effect": 0.5, "se": 0.2, "group": "x"},
            {"study": "C", "effect": 0.1, "se": 0.15, "group": "y"},
        ]
    )
    descriptive = run_analysis(profile, kind="descriptive", definition={"column": "effect"})
    assert descriptive["n"] == 3
    assert descriptive["mean"] == (0.2 + 0.5 + 0.1) / 3
    grouped = run_analysis(
        profile,
        kind="group_summary",
        definition={"group_by": "group", "value_column": "effect", "metric": "mean"},
    )
    assert grouped["groups"][0] == {"group": "x", "n": 2, "value": 0.35}
    meta = run_analysis(
        profile,
        kind="meta_analysis",
        definition={"label_column": "study", "effect_column": "effect", "se_column": "se"},
    )
    assert meta["k"] == 3
    assert len(meta["ci_95"]) == 2
    assert 0 <= meta["i_squared"] <= 100


def test_empty_dataset_profile_filled_by_version(settings) -> None:
    import base64 as b64

    client = _client("profiles@example.org", "Profile Lab")
    created = client.post(
        "/datasets",
        json={
            "name": "Follow-up outcomes",
            "description": "One row per participant.",
            "provenance": "Lab study S-04",
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["row_count"] == 0
    assert profile["columns"] == []
    assert profile["format"] == ""
    assert client.get(f"/datasets/{profile['public_id']}/versions").json() == []
    filled = client.post(
        f"/datasets/{profile['public_id']}/versions",
        json={
            "filename": "outcomes.csv",
            "content_base64": b64.b64encode(b"group,recall\nA,0.91\n").decode(),
        },
    )
    assert filled.status_code == 201, filled.text
    assert filled.json()["version"] == 1
    current = client.get(f"/datasets/{profile['public_id']}").json()
    assert current["row_count"] == 1
    assert current["format"] == "csv"
    assert [column["name"] for column in current["columns"]] == ["group", "recall"]
    assert current["provenance"] == "Lab study S-04"


def test_analysis_latex_and_writer_linking_without_project(settings) -> None:
    from sqlalchemy import select

    from sixsentences_server.core.db import AnalysisRecipeRow, ResearchDatasetRow, db_session

    client = _client("latex-link@example.org", "Latex Link Lab")
    dataset = client.post(
        "/datasets",
        json={
            "filename": "outcomes.csv",
            "name": "Outcomes",
            "content_base64": base64.b64encode(b"group,recall\nA,0.91\nB,0.87\n").decode(),
        },
    ).json()
    doc = client.post("/writer", json={"title": "Draft", "template": "blank"}).json()
    assert client.get(f"/writer/{doc['public_id']}/research-objects").json() == []
    client.patch(f"/writer/{doc['public_id']}", json={"dataset_ids": [dataset["public_id"]]})
    org_id = client.get("/auth/me").json()["org_id"]
    with db_session() as session:
        dataset_row = session.scalars(
            select(ResearchDatasetRow).where(ResearchDatasetRow.public_id == dataset["public_id"])
        ).one()
        recipe = AnalysisRecipeRow(
            org_id=org_id,
            project_id=None,
            dataset_id=dataset_row.id,
            dataset_version=1,
            name="Recall summary",
            kind="descriptive",
            definition={"column": "recall"},
            result={"kind": "descriptive", "n": 2, "mean": 0.89, "sd": 0.028, "ci_95": [0.8, 0.98]},
            status="ready",
        )
        session.add(recipe)
        session.flush()
        recipe_id = recipe.public_id
    objects = client.get(f"/writer/{doc['public_id']}/research-objects").json()
    assert [item["public_id"] for item in objects] == [recipe_id]
    assert "\\SixRecallsummaryMean" in objects[0]["latex"]
    assert "\\SixRecallsummaryCiLow" in objects[0]["latex"]
    latex = client.get(f"/analyses/{recipe_id}/latex").json()["latex"]
    assert "six-live:analysis" in latex
    assert latex.count("providecommand") >= 4


def test_dataset_agent_drops_duplicate_actions() -> None:
    """The model sometimes emits the same table-producing action three
    times in one turn; only one may survive into the output."""
    import json as jsonlib

    from sixsentences_server.datasets.service import run_dataset_agent
    from sixsentences_server.llm.mock import mock_pool

    action = {
        "operation": "run_analysis",
        "kind": "group_summary",
        "definition": {"group_by": "group", "value_column": "score"},
        "name": "Scores by group",
    }
    planned = {
        "answer": "Here is the summary by group.",
        "actions": [action, dict(action), dict(action)],
    }
    pool = mock_pool(lambda model, prompt: jsonlib.dumps(planned))
    turn = run_dataset_agent(
        pool,
        request="Fasse die Scores pro Gruppe zusammen",
        name="Study data",
        description="",
        provenance="",
        license="",
        format="csv",
        row_count=42,
        profile={
            "columns": [{"name": "group", "type": "string"}, {"name": "score", "type": "number"}]
        },
        versions=[],
        history=[],
        language="de",
    )
    assert len(turn.actions) == 1
    assert turn.actions[0]["kind"] == "group_summary"
