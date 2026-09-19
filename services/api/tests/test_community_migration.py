"""Fresh community installations are created from one clean baseline."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from sixsentences_server.core.db import Base


def test_fresh_alembic_baseline_matches_orm(tmp_path: Path) -> None:
    service = Path(__file__).resolve().parents[1]
    database = tmp_path / "community.db"
    environment = {
        **os.environ,
        "PYTHONPATH": str(service / "src"),
        "SIX_DATABASE_URL": f"sqlite:///{database}",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=service,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    assert tables - {"alembic_version"} == set(Base.metadata.tables)
    assert inspect(engine).get_pk_constraint("users")["constrained_columns"] == ["id"]


def test_rate_limit_downgrade_removes_only_ephemeral_pubmed_windows(tmp_path: Path) -> None:
    service = Path(__file__).resolve().parents[1]
    database = tmp_path / "community-downgrade.db"
    environment = {
        **os.environ,
        "PYTHONPATH": str(service / "src"),
        "SIX_DATABASE_URL": f"sqlite:///{database}",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=service,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rate_limit_buckets (scope, key_hash, window_id, count) "
                "VALUES (:scope, :key_hash, :window_id, 1)"
            ),
            [
                {
                    "scope": "pubmed-egress-keyed-v1",
                    "key_hash": "keyed",
                    "window_id": 9_000_000_000,
                },
                {
                    "scope": "pubmed-egress-unkeyed-v1",
                    "key_hash": "unkeyed",
                    "window_id": 9_000_000_001,
                },
                {
                    "scope": "login",
                    "key_hash": "survives",
                    "window_id": 42,
                },
            ],
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "20260912_0001"],
        cwd=service,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT scope, key_hash, window_id FROM rate_limit_buckets ORDER BY scope")
        ).all()
    assert rows == [("login", "survives", 42)]
    window_column = next(
        column
        for column in inspect(engine).get_columns("rate_limit_buckets")
        if column["name"] == "window_id"
    )
    assert str(window_column["type"]).upper() == "INTEGER"
