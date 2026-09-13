"""Regression coverage for large corpus-result persistence."""

from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from sixsentences_server.pipeline import run as run_pipeline


def _postgres_session() -> MagicMock:
    session = MagicMock(spec=Session)
    session.get_bind.return_value.dialect.name = "postgresql"
    return session


def test_postgres_work_upserts_are_batched_below_bind_limit(monkeypatch) -> None:  # noqa: ANN001
    session = _postgres_session()
    monkeypatch.setattr(run_pipeline, "POSTGRES_WORK_UPSERT_BATCH_SIZE", 2)
    values = [
        {"id": f"W{index}", "doi": None, "title": "Title", "year": 2026, "payload": {}}
        for index in range(5)
    ]

    run_pipeline._upsert_work_values(session, values)

    assert session.execute.call_count == 3


def test_postgres_source_lookups_are_batched_below_bind_limit(monkeypatch) -> None:  # noqa: ANN001
    session = _postgres_session()
    monkeypatch.setattr(run_pipeline, "POSTGRES_SOURCE_LOOKUP_BATCH_SIZE", 2)
    session.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=[("W0", "corpus")])),
        MagicMock(all=MagicMock(return_value=[("W2", "corpus")])),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    existing = run_pipeline._existing_source_keys(
        session,
        run_id=7,
        record_ids={"W0", "W1", "W2", "W3", "W4"},
    )

    assert existing == {("W0", "corpus"), ("W2", "corpus")}
    assert session.execute.call_count == 3
