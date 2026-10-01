"""Real application erasure and restored-state checks in a disposable SQLite fixture."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import User, db_session, get_engine, init_db
from sixsentences_server.ops.erasure import (
    cleanup_replayed_erasure_artifacts,
    replay_erasure_events,
)
from sixsentences_server.ops.erasure_ledger import read_events

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "community_rehearsal_fixture", ROOT / "deploy/community/rehearsal_fixture.py",
)
assert SPEC is not None and SPEC.loader is not None
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def test_fixture_refuses_missing_disposable_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIX_COMMUNITY_REHEARSAL", raising=False)
    with pytest.raises(SystemExit, match="explicitly disposable"):
        FIXTURE.main()


def test_post_backup_erasure_replays_without_resurrection(
    settings: Settings, corpus: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del corpus
    init_db()
    client = TestClient(create_app())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(FIXTURE, "MARKER", settings.data_dir / "rehearsal.txt")
    monkeypatch.setattr(FIXTURE, "ERASURE_STATE", settings.data_dir / "erasure-fixture.json")
    monkeypatch.setenv("SIX_COMMUNITY_REHEARSAL", "DISPOSABLE")

    def request(
        path: str, *, token: str = "", body: object = None, method: str = "GET",
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = client.request(method, path, headers=headers, json=body)
        if response.status_code >= 400:
            raise HTTPError(path, response.status_code, "synthetic API refusal", None, None)
        return response.json()

    monkeypatch.setattr(FIXTURE, "request", request)

    def phase(name: str, *arguments: str) -> str:
        monkeypatch.setattr("sys.argv", ["fixture", name, *arguments])
        capsys.readouterr()
        FIXTURE.main()
        return capsys.readouterr().out

    phase("seed")
    phase("upgrade")
    phase("prepare-erasure")
    state = FIXTURE.erasure_state()
    files = (FIXTURE.MARKER, *FIXTURE.erasure_files(state))
    saved_files = {path: path.read_bytes() for path in files}
    database = get_engine().url.database
    assert database is not None and get_engine().dialect.name == "sqlite"
    snapshot = tmp_path / "synthetic-before-erasure.sqlite"
    with sqlite3.connect(database) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    assert read_events(settings) == []

    phase("mutate")
    erasure_receipt = phase("erase")
    assert FIXTURE.PASSWORD not in erasure_receipt
    assert FIXTURE.ERASURE_EMAIL not in erasure_receipt
    signature = json.loads(erasure_receipt)["signature"]
    FIXTURE.verify_erased(signature)
    with pytest.raises(AssertionError):
        FIXTURE.verify_erased("0" * 64)

    # Restore only the old synthetic DB/files, retaining the newer journal as
    # restore.sh does. An empty journal or skipped replay must not pass.
    get_engine().dispose()
    with sqlite3.connect(snapshot) as source, sqlite3.connect(database) as target:
        source.backup(target)
    for path, content in saved_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with db_session() as session:
        assert session.get(User, state["user_id"]) is not None
    with pytest.raises(AssertionError):
        FIXTURE.verify_erased(signature)
    with db_session() as session:
        replay = replay_erasure_events(session, settings=settings)
    cleanup_replayed_erasure_artifacts(replay, settings)
    assert replay["applied"]["workspace"] == 1
    phase("verify")
    phase("verify-erasure", signature)
    with db_session() as session:
        again = replay_erasure_events(session, settings=settings)
    cleanup_replayed_erasure_artifacts(again, settings)
    assert again["already_absent"]["workspace"] == 1
    phase("verify-erasure", signature)


def test_driver_orders_post_backup_deletion_before_restore_and_receipt() -> None:
    driver = (ROOT / "deploy/community/rehearse.py").read_text(encoding="utf-8")
    assert driver.index('phase("prepare-erasure")') < driver.index('"backup.sh"')
    assert driver.index('"backup.sh"') < driver.index('phase("erase")')
    assert driver.index('phase("erase")') < driver.index('"restore.sh"')
    assert driver.index('"restore.sh"') < driver.index('phase("verify-erasure",')
    assert driver.index('phase("verify-erasure",') < driver.index('"erasure_replay": True')
