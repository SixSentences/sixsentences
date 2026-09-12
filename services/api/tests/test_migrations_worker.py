"""Fresh-schema and durable-worker tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config
from conftest import AppContext, bearer, register
from sqlalchemy import create_engine, inspect

from alembic import command
from sixsentences_server.mailer import MemoryTransport
from sixsentences_server.models import Base
from sixsentences_server.providers import DisabledTextProvider
from sixsentences_server.worker import Worker


class FakeResearchExecutor:
    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_count": 1,
            "records": [{"id": "W1", "title": "A reproducible result"}],
            "query": payload["query"],
        }


def test_clean_baseline_upgrades_and_downgrades(tmp_path: Path) -> None:
    service_root = Path(__file__).parents[1]
    database_path = tmp_path / "migration.sqlite3"
    config = Config(service_root / "alembic.ini")
    config.set_main_option("script_location", str(service_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    assert tables == set(Base.metadata.tables) | {"alembic_version"}

    command.downgrade(config, "base")
    remaining = set(inspect(engine).get_table_names())
    assert remaining <= {"alembic_version"}
    engine.dispose()


def test_worker_executes_research_brainstorm_and_smtp_jobs(app_context: AppContext) -> None:
    client = app_context.client
    account = register(client, email="worker@example.invalid", organization="Worker Lab")
    headers = bearer(account["token"])
    research_run = client.post(
        "/runs",
        headers=headers,
        json={
            "question": "How is adaptation evaluated?",
            "query": "adaptation AND evaluation",
            "limit": 5,
        },
    )
    assert research_run.status_code == 202, research_run.text
    brainstorm = client.post(
        "/brainstorms",
        headers=headers,
        json={"prompt": "Compare three plausible explanations"},
    )
    assert brainstorm.status_code == 202
    message = client.post(
        "/mail/messages",
        headers=headers,
        json={
            "recipient": "recipient@example.invalid",
            "subject": "Research update",
            "text_body": "The local analysis is ready.",
        },
    )
    assert message.status_code == 202

    mail = MemoryTransport()
    worker = Worker(
        app_context.database,
        app_context.settings,
        research=FakeResearchExecutor(),
        ai=DisabledTextProvider(),
        mail=mail,
    )
    claimed = 0
    while worker.run_once():
        claimed += 1
        assert claimed < 10

    assert claimed == 4  # registration welcome, research, brainstorm and explicit mail
    completed_run = client.get(f"/runs/{research_run.json()['id']}", headers=headers).json()
    assert completed_run["status"] == "completed"
    assert completed_run["result"]["record_count"] == 1
    completed_brainstorm = client.get(
        f"/brainstorms/{brainstorm.json()['id']}", headers=headers
    ).json()
    assert completed_brainstorm["status"] == "completed"
    assert completed_brainstorm["result"]["generated_by"] == "deterministic_local_fallback"
    assert len(mail.messages) == 2
    assert mail.messages[-1][0] == "recipient@example.invalid"
