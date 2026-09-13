"""Living reviews: retraction-delta recheck + the Atlas-gated living flag."""

from fastapi.testclient import TestClient

from sixsentences_server.config import Settings
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.db import (
    Run,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.living.monitor import recheck_retractions


def test_recheck_flags_newly_retracted_included_works(settings: Settings) -> None:
    init_db()
    with db_session() as session:
        org = get_default_org(session)
        project = ProjectRow(org_id=org.id, name="p")
        session.add(project)
        session.flush()
        run = Run(org_id=org.id, project_id=project.id, question="q", status="completed")
        session.add(run)
        session.flush()
        for work_id, doi in [("W_clean", "10.1/clean"), ("W_ret", "10.1/ret")]:
            payload = WorkRecord(id=work_id, doi=doi, title=work_id).model_dump(mode="json")
            session.add(WorkRow(id=work_id, doi=doi, title=work_id, payload=payload))
            session.add(
                SourceRecordRow(org_id=org.id, run_id=run.id, work_id=work_id, source="openalex")
            )
            session.add(
                ScreeningDecisionRow(
                    org_id=org.id,
                    run_id=run.id,
                    work_id=work_id,
                    reviewer="ensemble-or",
                    verdict="include",
                    reason="r",
                )
            )
        session.flush()
        delta = recheck_retractions(session, run, ["10.1/ret"])
        assert delta.checked == 2
        assert delta.retracted_now == ["W_ret"]
        assert delta.newly_retracted == ["W_ret"]


def _authed(app: object, email: str, org: str) -> TestClient:
    client = TestClient(app)
    body = {"email": email, "password": "StrongPass123!", "org_name": org, "name": "Test"}
    token = client.post("/auth/register", json=body).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
