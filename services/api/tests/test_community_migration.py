"""Fresh community installations are created from one clean baseline."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

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
