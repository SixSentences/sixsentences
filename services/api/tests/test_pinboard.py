"""Real personal-note routes: bounded input, isolation, conflicts and privacy rights."""

import io
import json
import logging
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.core.auth import add_member, authenticate, create_api_key, register
from sixsentences_server.core.db import PersonalPinboardRow, User, db_session, init_db
from sixsentences_server.core.pinboard import PinboardConflictError, PinboardState, write_pinboard
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus

PASSWORD = "SyntheticTestPassword123!"


def note(text: str = "Synthetic private reminder", *, size: float = 1.0) -> dict[str, Any]:
    """Build an inert note without customer data."""
    return {
        "id": str(uuid4()),
        "text": text,
        "color": "sage",
        "shape": "note",
        "x": 0.25,
        "y": 0.5,
        "rotation": -3.0,
        "size": size,
    }


@pytest.fixture
def clients(corpus: DuckDBCorpus) -> tuple[TestClient, TestClient, TestClient]:
    init_db()
    with db_session() as session:
        owner, owner_token = register(session, "desk-owner@example.org", PASSWORD, "Desk tests")
        add_member(session, owner.org_id, "desk-member@example.org", PASSWORD, "member")
        member_token = authenticate(session, "desk-member@example.org", PASSWORD)
        _, outsider_token = register(session, "desk-outsider@example.org", PASSWORD, "Other desk")
    result = (TestClient(create_app()), TestClient(create_app()), TestClient(create_app()))
    for client, token in zip(result, (owner_token, member_token, outsider_token), strict=True):
        client.headers["Authorization"] = f"Bearer {token}"
    return result


@pytest.mark.parametrize("size", [0.6, 1.0, 2.4])
def test_roundtrip_and_creator_isolation(clients: tuple[TestClient, ...], size: float) -> None:
    owner, member, outsider = clients
    payload = {
        "revision": 0,
        "notes": [note("<script>inert text</script> # reminder", size=size)],
    }
    saved = member.put("/auth/pinboard", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {**payload, "revision": 1}
    assert saved.headers["cache-control"] == "no-store"
    assert member.get("/auth/pinboard").json() == saved.json()
    assert owner.get("/auth/pinboard").json() == {"revision": 0, "notes": []}
    assert outsider.get("/auth/pinboard").json() == {"revision": 0, "notes": []}
    # Caller-supplied ownership cannot widen the personal endpoint.
    assert owner.put("/auth/pinboard", json={**payload, "user_id": 2}).status_code == 422
    assert owner.get("/auth/pinboard?user_id=2").json()["notes"] == []


def test_legacy_note_input_normalizes_size_in_response_and_storage(
    clients: tuple[TestClient, ...],
) -> None:
    legacy = note()
    del legacy["size"]
    response = clients[0].put("/auth/pinboard", json={"revision": 0, "notes": [legacy]})
    assert response.status_code == 200, response.text
    expected = {**legacy, "size": 1.0}
    assert response.json() == {"revision": 1, "notes": [expected]}
    assert clients[0].get("/auth/pinboard").json() == response.json()
    with db_session() as session:
        row = session.scalar(select(PersonalPinboardRow))
        assert row is not None
        assert row.notes == [expected]
    # A legacy client may omit the new field on later full-board updates too.
    response = clients[0].put(
        "/auth/pinboard",
        json={"revision": 1, "notes": [{**legacy, "text": "Updated synthetic reminder"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["notes"][0]["size"] == 1.0


def test_reading_legacy_stored_notes_defaults_size_without_rewriting_data(
    clients: tuple[TestClient, ...],
) -> None:
    legacy = note()
    del legacy["size"]
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "desk-owner@example.org"))
        assert user is not None
        session.add(
            PersonalPinboardRow(user_id=user.id, org_id=user.org_id, revision=1, notes=[legacy])
        )
    assert clients[0].get("/auth/pinboard").json() == {
        "revision": 1,
        "notes": [{**legacy, "size": 1.0}],
    }
    with db_session() as session:
        row = session.scalar(select(PersonalPinboardRow))
        assert row is not None
        assert row.revision == 1
        assert row.notes == [legacy]


def test_legacy_client_preserves_existing_sizes_and_can_add_default_size_notes(
    clients: tuple[TestClient, ...],
) -> None:
    original = note(size=1.8)
    saved = clients[0].put("/auth/pinboard", json={"revision": 0, "notes": [original]}).json()
    legacy = {key: value for key, value in original.items() if key != "size"}
    legacy["text"] = "Synthetic legacy edit"
    added = {key: value for key, value in note().items() if key != "size"}
    response = clients[0].put(
        "/auth/pinboard", json={"revision": saved["revision"], "notes": [legacy, added]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "revision": 2,
        "notes": [{**legacy, "size": 1.8}, {**added, "size": 1.0}],
    }
    assert clients[0].get("/auth/pinboard").json() == response.json()
    with db_session() as session:
        row = session.scalar(select(PersonalPinboardRow))
        assert row is not None
        assert row.notes == response.json()["notes"]
    # An explicit size is a deliberate update, including reset to the default.
    reset = clients[0].put(
        "/auth/pinboard", json={"revision": 2, "notes": [{**legacy, "size": 1.0}]},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["notes"][0]["size"] == 1.0
    assert clients[0].put(
        "/auth/pinboard", json={"revision": 2, "notes": [legacy]},
    ).status_code == 409
    assert clients[0].get("/auth/pinboard").json() == reset.json()


def test_legacy_size_lookup_never_reads_another_creators_note(
    clients: tuple[TestClient, ...],
) -> None:
    original = note(size=2.4)
    legacy = {key: value for key, value in original.items() if key != "size"}
    assert clients[0].put(
        "/auth/pinboard", json={"revision": 0, "notes": [original]},
    ).status_code == 200
    for other in clients[1:]:
        assert other.put("/auth/pinboard", json={"revision": 0, "notes": []}).status_code == 200
        response = other.put("/auth/pinboard", json={"revision": 1, "notes": [legacy]})
        assert response.status_code == 200, response.text
        assert response.json()["notes"][0]["size"] == 1.0
    assert clients[0].get("/auth/pinboard").json()["notes"][0]["size"] == 2.4


def test_stale_writes_and_first_save_cannot_overwrite(clients: tuple[TestClient, ...]) -> None:
    client = clients[0]
    original = {"revision": 0, "notes": [note()]}
    saved = client.put("/auth/pinboard", json=original).json()
    conflict = client.put("/auth/pinboard", json=original)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "pinboard_conflict"
    assert "notes" not in conflict.json()["detail"]
    assert client.put("/auth/pinboard", json={"revision": 1, "notes": []}).json() == {
        "revision": 2,
        "notes": [],
    }
    assert client.put("/auth/pinboard", json=saved).status_code == 409
    assert client.get("/auth/pinboard").json() == {"revision": 2, "notes": []}


@pytest.mark.parametrize("revision", [True, -1, 1.5, "0", 2_147_483_648])
def test_invalid_revision(clients: tuple[TestClient, ...], revision: Any) -> None:
    assert (
        clients[0].put("/auth/pinboard", json={"revision": revision, "notes": []}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "not-a-uuid"},
        {"text": "x" * 2001},
        {"text": 42},
        {"x": -0.01},
        {"y": 1.01},
        {"rotation": 13},
        {"size": 0.5999},
        {"size": 2.4001},
        {"size": True},
        {"size": "1.0"},
        {"size": None},
        {"color": "arbitrary-css"},
        {"shape": "html"},
        {"x": True},
        {"x": "0.5"},
        {"html": "<iframe>"},
    ],
)
def test_bounded_note_schema(clients: tuple[TestClient, ...], change: dict[str, Any]) -> None:
    response = clients[0].put(
        "/auth/pinboard", json={"revision": 0, "notes": [{**note(), **change}]}
    )
    assert response.status_code == 422, response.text
    assert clients[0].get("/auth/pinboard").json() == {"revision": 0, "notes": []}


@pytest.mark.parametrize("size", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_note_size_is_rejected_without_writes(
    clients: tuple[TestClient, ...], size: float,
) -> None:
    # Raw JSON deliberately exercises permissive JSON parsers with non-finite
    # input rather than relying on the HTTP client's strict JSON encoder.
    response = clients[0].put(
        "/auth/pinboard",
        content=json.dumps({"revision": 0, "notes": [note(size=size)]}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert clients[0].get("/auth/pinboard").json() == {"revision": 0, "notes": []}


def test_count_and_duplicate_limits(clients: tuple[TestClient, ...]) -> None:
    client = clients[0]
    one = note()
    for notes in ([note() for _ in range(25)], [one, {**one, "id": one["id"].upper()}]):
        assert client.put("/auth/pinboard", json={"revision": 0, "notes": notes}).status_code == 422


def test_session_only(clients: tuple[TestClient, ...]) -> None:
    anonymous = TestClient(create_app())
    assert anonymous.get("/auth/pinboard").status_code == 401
    assert anonymous.put("/auth/pinboard", json={"revision": 0, "notes": []}).status_code == 401
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "desk-owner@example.org"))
        assert user is not None
        token = create_api_key(session, user, "Synthetic key", scopes=["research:write"])
    anonymous.headers["Authorization"] = f"Bearer {token}"
    assert anonymous.get("/auth/pinboard").status_code == 403
    assert anonymous.put("/auth/pinboard", json={"revision": 0, "notes": []}).status_code == 403


def test_export_and_erasure_are_personal_even_for_workspace_owner(
    clients: tuple[TestClient, ...],
    enforced_foreign_keys: None,
) -> None:
    owner, member, outsider = clients
    for client, text, size in zip(
        clients, ("Owner canary", "Member canary", "Outsider canary"), (1.4, 0.6, 2.4), strict=True
    ):
        assert (
            client.put("/auth/pinboard", json={"revision": 0, "notes": [note(text, size=size)]})
            .status_code
            == 200
        )
    for client, expected, size in ((owner, "Owner canary", 1.4), (member, "Member canary", 0.6)):
        exported = client.post("/auth/account/export", json={"password": PASSWORD})
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            rows = json.loads(archive.read("data/personal_pinboards.json"))
            assert len(rows) == 1
            assert rows[0]["notes"][0]["text"] == expected
            assert rows[0]["notes"][0]["size"] == size
    assert member.request("DELETE", "/auth/account", json={"password": PASSWORD}).status_code == 200
    with db_session() as session:
        assert len(session.scalars(select(PersonalPinboardRow)).all()) == 2
    assert owner.get("/auth/pinboard").json()["notes"][0]["text"] == "Owner canary"
    assert owner.request("DELETE", "/auth/account", json={"password": PASSWORD}).status_code == 200
    with db_session() as session:
        assert len(session.scalars(select(PersonalPinboardRow)).all()) == 1
    assert outsider.get("/auth/pinboard").json()["notes"][0]["text"] == "Outsider canary"
    assert outsider.get("/auth/pinboard").json()["notes"][0]["size"] == 2.4


@pytest.mark.parametrize("starting_revision", [0, 1])
def test_concurrent_compare_and_swap_has_one_winner(
    clients: tuple[TestClient, ...],
    starting_revision: int,
) -> None:
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "desk-owner@example.org"))
        assert user is not None
        user_id, org_id = user.id, user.org_id
        if starting_revision:
            write_pinboard(
                session, user_id=user_id, org_id=org_id, state=PinboardState(revision=0, notes=[])
            )

    def save(label: str) -> bool:
        try:
            with db_session() as session:
                write_pinboard(
                    session,
                    user_id=user_id,
                    org_id=org_id,
                    state=PinboardState.model_validate(
                        {
                            "revision": starting_revision,
                            "notes": [note(label, size=0.75 if label == "First draft" else 1.8)],
                        }
                    ),
                )
            return True
        except PinboardConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, ["First draft", "Second draft"]))
    assert sorted(outcomes) == [False, True]
    current = clients[0].get("/auth/pinboard").json()
    assert current["revision"] == starting_revision + 1
    assert len(current["notes"]) == 1
    winner = current["notes"][0]
    assert winner["size"] == (0.75 if winner["text"] == "First draft" else 1.8)


def test_concurrent_legacy_edit_and_resize_cannot_overwrite_each_other(
    clients: tuple[TestClient, ...],
) -> None:
    original = note(size=1.8)
    assert clients[0].put(
        "/auth/pinboard", json={"revision": 0, "notes": [original]},
    ).status_code == 200
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == "desk-owner@example.org"))
        assert user is not None
        user_id, org_id = user.id, user.org_id
    legacy = {key: value for key, value in original.items() if key != "size"}
    drafts = [
        {**legacy, "text": "Legacy edit"},
        {**original, "text": "Resized edit", "size": 2.4},
    ]

    def save(draft: dict[str, Any]) -> bool:
        try:
            with db_session() as session:
                write_pinboard(
                    session, user_id=user_id, org_id=org_id,
                    state=PinboardState.model_validate({"revision": 1, "notes": [draft]}),
                )
            return True
        except PinboardConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, drafts))
    assert sorted(outcomes) == [False, True]
    current = clients[0].get("/auth/pinboard").json()
    assert current["revision"] == 2
    assert len(current["notes"]) == 1
    winner = current["notes"][0]
    assert winner["size"] == (1.8 if winner["text"] == "Legacy edit" else 2.4)


def test_pinboard_migration_roundtrip_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    """Upgrade an existing schema and prove only the new table is removed on undo."""
    service = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'pinboard-migration.db'}"
    environment = {**os.environ, "PYTHONPATH": str(service / "src"), "SIX_DATABASE_URL": url}
    logger = logging.getLogger("sixsentences_server.api.app")
    logger_state = (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))
    root_handlers = tuple(logging.getLogger().handlers)

    def migrate(action: str, revision: str) -> None:
        # Alembic configures logging; a child process preserves pytest's capture
        # handlers and application loggers for later security regression tests.
        subprocess.run(
            [sys.executable, "-m", "alembic", action, revision],
            cwd=service,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    engine = sa.create_engine(url)
    try:
        migrate("upgrade", "20260919_0002")
        before = set(sa.inspect(engine).get_table_names())
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO orgs (id, name, plan, created_at) "
                    "VALUES (9001, 'Synthetic migration canary', 'community', CURRENT_TIMESTAMP)"
                )
            )
        migrate("upgrade", "20260928_0003")
        assert set(sa.inspect(engine).get_table_names()) == before | {"personal_pinboards"}
        assert {
            column["name"] for column in sa.inspect(engine).get_columns("personal_pinboards")
        } == {
            "user_id",
            "org_id",
            "revision",
            "notes",
            "updated_at",
        }
        migrate("downgrade", "20260919_0002")
        assert set(sa.inspect(engine).get_table_names()) == before
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT name FROM orgs WHERE id=9001")
            ).scalar_one() == ("Synthetic migration canary")
        migrate("upgrade", "20260928_0003")
        assert (
            logger.disabled,
            logger.level,
            logger.propagate,
            tuple(logger.handlers),
        ) == logger_state
        assert tuple(logging.getLogger().handlers) == root_handlers
    finally:
        engine.dispose()
