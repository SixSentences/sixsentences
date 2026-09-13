"""Safety guards for the opt-in PostgreSQL test fixture."""

from typing import cast

import pytest
from conftest import _drop_isolated_postgres_database, _isolated_postgres_database
from sqlalchemy.engine import Engine


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///tmp/test.db",
        "postgresql+psycopg://user:secret@database.example.invalid/sixsentences",
        "postgresql+psycopg://user:secret@database.example.invalid/production",
    ],
)
def test_isolated_postgres_fixture_rejects_unsafe_databases(database_url: str) -> None:
    with pytest.raises(RuntimeError):
        _isolated_postgres_database(database_url)


def test_postgres_fixture_cleanup_rejects_unowned_database() -> None:
    with pytest.raises(RuntimeError, match="not owned"):
        _drop_isolated_postgres_database(cast(Engine, object()), "sixsentences_staging")
