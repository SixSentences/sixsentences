"""PostgreSQL-only migration and concurrent queue contract.

The regular suite intentionally uses isolated SQLite files. CI supplies a real
PostgreSQL service for this test so dialect-specific row locks, sequences and
the one-time migration cannot regress unnoticed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import sixsentences_server.core.db as dbmod
import sixsentences_server.jobs as jobs
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import BackgroundJobRow, Base, Org, Project


@pytest.mark.skipif(
    not os.environ.get("SIX_TEST_POSTGRES_URL"),
    reason="requires the dedicated CI PostgreSQL service",
)
def test_postgres_cutover_and_skip_locked_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_url = os.environ["SIX_TEST_POSTGRES_URL"]
    source_path = tmp_path / "source.db"
    source_url = f"sqlite:///{source_path}"
    source_engine = create_engine(source_url)
    Base.metadata.create_all(source_engine)
    with Session(source_engine) as session:
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
    source_engine.dispose()

    report = tmp_path / "cutover.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/migrate_sqlite_to_postgres.py",
            "--source",
            source_url,
            "--target",
            target_url,
            "--report",
            str(report),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert evidence["table_counts"]["orgs"] == 1
    assert evidence["table_counts"]["projects"] == 1

    target_engine = create_engine(target_url)
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
