"""Shared fixtures: an in-memory-ish corpus and isolated settings per test."""

import json
import os
import re
import secrets
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine, make_url

import sixsentences_server.core.db as dbmod
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.corpus.ingest import WORKS_SCHEMA

pytest_plugins = ("_pytest_sharding",)

SAMPLE_WORKS = [
    {
        "id": "W1",
        "doi": "10.1/alpha",
        "title": "Attention is all you need: transformer architectures",
        # W1 cites W2: lets the snowball tests resolve a reference offline
        "abstract": "We introduce the transformer, based on self-attention.",
        "year": 2017,
        "venue": "NeurIPS",
        "authors": json.dumps(["A. Vaswani"]),
        "cited_by_count": 90000,
        "is_retracted": False,
        "source": "openalex",
        "referenced_works": json.dumps(["W2"]),
        "work_type": "article",
    },
    {
        "id": "W2",
        "doi": "10.1/beta",
        "title": "A survey of active learning for text classification",
        "abstract": "Active learning reduces screening effort in reviews.",
        "year": 2020,
        "venue": "ACM CSUR",
        "authors": json.dumps(["B. Author"]),
        "cited_by_count": 500,
        "is_retracted": False,
        "source": "openalex",
        "referenced_works": json.dumps(["W3"]),  # cites the retracted work -> zombie citation
        "work_type": "article",
    },
    {
        "id": "W3",
        "doi": "10.1/gamma",
        "title": "Retracted: fabricated results on deep learning screening",
        "abstract": "This paper about deep learning screening was retracted.",
        "year": 2021,
        "venue": "Predatory Letters",
        "authors": json.dumps(["C. Fraud"]),
        "cited_by_count": 12,
        "is_retracted": True,
        "source": "openalex",
        "referenced_works": json.dumps([]),
        "work_type": "preprint",  # excluded by --peer-reviewed
    },
]

_SAFE_POSTGRES_DATABASE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _isolated_postgres_database(base_url: str) -> tuple[URL, Engine, str]:
    """Create one disposable PostgreSQL database for a single test.

    The opt-in runner supplies an RC-only base database. Requiring a test-like
    database name prevents a developer shell variable from ever turning this
    fixture into a production database creator.
    """

    url = make_url(base_url)
    database = (url.database or "").lower()
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("SIX_TEST_ISOLATED_POSTGRES_URL must use PostgreSQL")
    if not any(marker in database for marker in ("test", "staging", "load")):
        raise RuntimeError(
            "refusing PostgreSQL test fixture outside a test, staging or load database"
        )
    test_database = f"six_pytest_{secrets.token_hex(8)}"
    if not _SAFE_POSTGRES_DATABASE.fullmatch(test_database):
        raise RuntimeError("generated PostgreSQL test database name is invalid")
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{test_database}" TEMPLATE template0'))
    return url.set(database=test_database), admin_engine, test_database


def _drop_isolated_postgres_database(admin_engine: Engine, database: str) -> None:
    """Drop an exact fixture-owned database after all pooled handles close."""

    if not database.startswith("six_pytest_") or not _SAFE_POSTGRES_DATABASE.fullmatch(database):
        raise RuntimeError("refusing to drop a database not owned by the test fixture")
    with admin_engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(autouse=True)
def _fast_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    # keep the suite fast: PBKDF2 at production work factor is intentionally slow
    monkeypatch.setattr("sixsentences_server.core.auth.PBKDF2_ITERATIONS", 1000)


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # tests must never inherit a developer's real provider keys (no live calls)
    from sixsentences_server.llm.providers import PROVIDERS

    for spec in PROVIDERS.values():
        # An explicit empty process value must outrank pydantic-settings'
        # ``.env`` source. Merely deleting the variable would reload a real
        # developer key from disk on the next Settings() construction.
        monkeypatch.setenv(spec.key_env, "")
    monkeypatch.setenv("SIX_WEBSEARCH_API_KEY", "")


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    monkeypatch.setenv("SIX_DATA_DIR", str(tmp_path / "data"))
    # Tests may execute from a production-shaped container. Keep process-local
    # behavior deterministic while still allowing the database backend below
    # to opt into an isolated PostgreSQL database.
    monkeypatch.setenv("SIX_JOBS_BACKEND", "inline")
    monkeypatch.setenv("SIX_RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("SIX_STORAGE_RESERVE_BYTES", "0")
    postgres_base_url = os.environ.get("SIX_TEST_ISOLATED_POSTGRES_URL", "").strip()
    postgres_admin: Engine | None = None
    postgres_database = ""
    if postgres_base_url:
        database_url, postgres_admin, postgres_database = _isolated_postgres_database(
            postgres_base_url
        )
        monkeypatch.setenv("SIX_DATABASE_URL", database_url.render_as_string(False))
    else:
        monkeypatch.setenv("SIX_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    # self-signup defaults OFF (closed beta); the suite exercises the open
    # state, the closed default has its own dedicated test
    monkeypatch.setenv("SIX_SELF_SIGNUP", "1")
    # Most API tests exercise downstream product behavior after immediate
    # registration. The dedicated verification tests turn this launch setting on.
    monkeypatch.setenv("SIX_REQUIRE_EMAIL_VERIFICATION", "0")
    # Existing product tests create accounts with the pre-launch request
    # shape. Dedicated legal-acceptance tests enable the launch gate.
    monkeypatch.setenv("SIX_ENFORCE_LEGAL_ACCEPTANCE", "0")
    # Tests that explicitly configure the inert fixture web-search key model a
    # deployed account whose processor/transfer terms were already verified.
    monkeypatch.setenv("SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED", "1")
    # Test-only Fernet key (32 bytes, URL-safe base64). Production injects a
    # distinct key through the deployment secret file.
    monkeypatch.setenv(
        "SIX_CONNECTOR_ENCRYPTION_KEY",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setenv(
        "SIX_ERASURE_LEDGER_HMAC_KEY",
        "test-only-erasure-ledger-key-00000000000000000000000000000000",
    )
    monkeypatch.setenv(
        "SIX_ERASURE_LEDGER_PATH",
        str(tmp_path / "privacy" / "erasure-ledger.jsonl"),
    )
    get_settings.cache_clear()
    # reset the lazily created engine so each test gets its own database
    dbmod._engine = None
    dbmod._session_factory = None
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()
        if dbmod._engine is not None:
            dbmod._engine.dispose()
        dbmod._engine = None
        dbmod._session_factory = None
        if postgres_admin is not None:
            _drop_isolated_postgres_database(postgres_admin, postgres_database)


@pytest.fixture()
def enforced_foreign_keys(settings: Settings) -> Iterator[None]:
    """Make SQLite reject a dangling reference the way PostgreSQL always does.

    SQLite ignores foreign keys unless a connection asks for them, so a flush
    that stages a child row ahead of its parent passes here and fails in
    production. Writers that create both rows in one unit of work take this
    fixture so the ordering is tested, not assumed.
    """
    engine = dbmod.get_engine()
    if engine.dialect.name != "sqlite":
        yield
        return

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    engine.dispose()  # pooled connections predate the listener
    yield


@pytest.fixture()
def corpus(settings: Settings) -> DuckDBCorpus:
    store = DuckDBCorpus(settings.corpus_dir)
    table = pa.Table.from_pylist(SAMPLE_WORKS, schema=WORKS_SCHEMA)
    pq.write_table(table, store.works_path)
    store.write_meta(version="micro-test-3", works=3, sources={"openalex": "fixture"})
    return store
