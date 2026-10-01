"""PostgreSQL-only public migration and concurrent queue contract.

The regular suite intentionally uses isolated SQLite files. CI supplies a real
PostgreSQL service for this test so dialect-specific row locks, sequences and
the public Alembic migration chain cannot regress unnoticed. A private legacy
SQLite cutover utility is not part of the community installation or this test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

import sixsentences_server.core.db as dbmod
import sixsentences_server.jobs as jobs
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import BackgroundJobRow, Org, Project


def _postgres_target_url() -> str:
    """Refuse any target outside the explicit disposable runner-local database."""
    value = os.environ["SIX_TEST_POSTGRES_URL"]
    target = make_url(value)
    if (
        target.drivername != "postgresql+psycopg"
        or target.host not in {"127.0.0.1", "localhost"}
        or target.port != 5432
        or target.database != "community_ci"
        or target.username != "postgres"
    ):
        raise ValueError("PostgreSQL contracts require the dedicated runner-local community_ci DB")
    return value


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres@database.example.invalid:5432/community_ci",
        "postgresql+psycopg://postgres@127.0.0.1:5432/production",
        "postgresql+psycopg://operator@127.0.0.1:5432/community_ci",
        "postgresql+psycopg://postgres@127.0.0.1:5433/community_ci",
        "sqlite:///unexpected.db",
    ],
)
def test_postgres_contract_refuses_targets_outside_its_ci_scope(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("SIX_TEST_POSTGRES_URL", url)
    with pytest.raises(ValueError, match="dedicated runner-local"):
        _postgres_target_url()


@pytest.mark.skipif(
    not os.environ.get("SIX_TEST_POSTGRES_URL"),
    reason="requires the dedicated CI PostgreSQL service",
)
def test_postgres_boolean_default_contract() -> None:
    """Reproduce the old baseline's invalid SQL using only temporary tables."""
    engine = create_engine(_postgres_target_url())
    try:
        with pytest.raises(ProgrammingError) as failure, engine.begin() as connection:
            connection.execute(
                text("CREATE TEMP TABLE six_boolean_invalid (value BOOLEAN DEFAULT 0)")
            )
        assert getattr(failure.value.orig, "sqlstate", None) == "42804"
        print("baseline_boolean_default_sqlstate=42804")
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TEMP TABLE six_boolean_valid (value BOOLEAN DEFAULT false)")
            )
            connection.execute(text("INSERT INTO six_boolean_valid DEFAULT VALUES"))
            assert connection.scalar(text("SELECT value FROM six_boolean_valid")) is False
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("SIX_TEST_POSTGRES_URL"),
    reason="requires the dedicated CI PostgreSQL service",
)
def test_postgres_migrations_and_skip_locked_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_url = _postgres_target_url()
    target_engine = create_engine(target_url)
    assert inspect(target_engine).get_table_names() == [], "Refusing a nonempty PostgreSQL test DB"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "SIX_DATABASE_URL": target_url,
            "SIX_DATA_DIR": str(tmp_path / "data"),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    with Session(target_engine) as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "20260928_0003"
        org = Org(name="migration-fixture")
        session.add(org)
        session.flush()
        session.add(
            Project(
                org_id=org.id,
                name="Migration fixture",
                metadata_json={"nested": {"verified": True}, "items": [1, 2, 3]},
            )
        )
        session.commit()
    with Session(target_engine) as session:
        project = session.scalar(select(Project).where(Project.name == "Migration fixture"))
        assert project is not None
        assert project.metadata_json["nested"]["verified"] is True
        next_org = Org(name="sequence-after-migration")
        session.add(next_org)
        session.commit()
        assert next_org.id > 1
    target_engine.dispose()

    monkeypatch.setenv("SIX_DATABASE_URL", target_url)
    monkeypatch.setenv("SIX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:
        with dbmod.db_session() as session:
            org = session.scalar(select(Org).where(Org.name == "migration-fixture"))
            assert org is not None
            session.add_all(
                [
                    BackgroundJobRow(
                        org_id=org.id,
                        task="_execute",
                        lane="research",
                        args=[101],
                        kwargs={},
                    ),
                    BackgroundJobRow(
                        org_id=org.id,
                        task="_execute",
                        lane="research",
                        args=[102],
                        kwargs={},
                    ),
                ]
            )

        workers = [jobs.DatabaseWorker(run_in_subprocess=False, lane="research") for _ in range(2)]
        claims: list[tuple[jobs.DatabaseWorker, BackgroundJobRow]] = []
        lock = threading.Lock()

        def claim(worker: jobs.DatabaseWorker) -> None:
            row = worker._claim()
            assert row is not None
            with lock:
                claims.append((worker, row))

        threads = [threading.Thread(target=claim, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()

        assert len({row.id for _worker, row in claims}) == 2
        for worker, row in claims:
            assert row.lease_token is not None
            assert worker._complete(row.id, row.lease_token)
    finally:
        if dbmod._engine is not None:
            dbmod._engine.dispose()
        dbmod._engine = None
        dbmod._session_factory = None
        get_settings.cache_clear()
