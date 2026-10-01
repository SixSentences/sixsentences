"""Real application erasure and restored-state checks in a disposable SQLite fixture."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
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
    "community_rehearsal_fixture",
    ROOT / "deploy/community/rehearsal_fixture.py",
)
assert SPEC is not None and SPEC.loader is not None
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
DRIVER_SPEC = importlib.util.spec_from_file_location(
    "community_rehearsal_driver",
    ROOT / "deploy/community/rehearse.py",
)
assert DRIVER_SPEC is not None and DRIVER_SPEC.loader is not None
DRIVER = importlib.util.module_from_spec(DRIVER_SPEC)
DRIVER_SPEC.loader.exec_module(DRIVER)


def test_fixture_refuses_missing_disposable_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIX_COMMUNITY_REHEARSAL", raising=False)
    with pytest.raises(SystemExit, match="explicitly disposable"):
        FIXTURE.main()


@pytest.mark.parametrize("preupgrade", [False, True])
def test_post_backup_erasure_replays_without_resurrection(
    settings: Settings,
    corpus: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    preupgrade: bool,
) -> None:
    del corpus
    init_db()
    client = TestClient(create_app())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(FIXTURE, "MARKER", settings.data_dir / "rehearsal.txt")
    monkeypatch.setattr(FIXTURE, "ERASURE_STATE", settings.data_dir / "erasure-fixture.json")
    monkeypatch.setenv("SIX_COMMUNITY_REHEARSAL", "DISPOSABLE")

    def request(
        path: str,
        *,
        token: str = "",
        body: object = None,
        method: str = "GET",
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
    if preupgrade:
        phase("prepare-rollback")
    else:
        phase("upgrade")
        phase("prepare-erasure")
    state = FIXTURE.erasure_state()
    files = (FIXTURE.MARKER, FIXTURE.ERASURE_STATE, *FIXTURE.erasure_files(state))
    saved_files = {path: path.read_bytes() for path in files}
    database = get_engine().url.database
    assert database is not None and get_engine().dialect.name == "sqlite"
    snapshot = tmp_path / "synthetic-before-erasure.sqlite"
    with sqlite3.connect(database) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    assert read_events(settings) == []

    if preupgrade:
        phase("upgrade")
        phase("prepare-erasure-pinboard")
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
    if preupgrade:
        phase("verify-rollback", signature)
    else:
        phase("verify")
        phase("verify-erasure", signature)
    with db_session() as session:
        again = replay_erasure_events(session, settings=settings)
    cleanup_replayed_erasure_artifacts(again, settings)
    assert again["already_absent"]["workspace"] == 1
    phase("verify-erasure", signature)


@pytest.mark.parametrize("fail_rollback", [False, True])
def test_driver_restores_distinct_snapshots_with_matching_api_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fail_rollback: bool,
) -> None:
    """Verify orchestration only; actual released-image compatibility needs Docker CI."""
    report = tmp_path / "rehearsal.json"
    old_image = "ghcr.io/sixsentences/community-api@sha256:" + "1" * 64
    new_image = "sixsentences-community-api:ci"
    history: list[tuple[str, str]] = []
    snapshots: list[Path] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        environment = kwargs["env"]
        image = environment.get("SIX_API_IMAGE", "")
        output = ""
        returncode = 0
        if command[0] == "bash":
            script = Path(command[1]).name
            if script == "init-env.sh":
                env_file = Path(command[command.index("--output") + 1])
                env_file.write_text("SIX_BACKUP_DIR=/unused\n", encoding="utf-8")
            elif script == "backup.sh":
                config = Path(environment["SIX_SELFHOST_ENV_FILE"]).read_text()
                backup_root = Path(config.strip().split("=", 1)[1])
                snapshot = backup_root / f"20261001T00000{len(snapshots)}Z"
                snapshot.mkdir(parents=True)
                snapshots.append(snapshot)
                history.append(("backup", image))
            elif script == "restore.sh":
                restored = Path(command[command.index("--backup") + 1])
                history.append((f"restore-{snapshots.index(restored)}", image))
        elif "exec" in command:
            phase = command[command.index("python") + 2]
            history.append((phase, image))
            if phase == "database-revision":
                output = "alpha1_revision\n"
            elif phase == "erase":
                output = json.dumps({"signature": "a" * 64})
            elif phase == "verify-rollback" and fail_rollback:
                returncode = 1
                output = "private-output-must-not-escape"
        elif command[1:3] == ["image", "inspect"]:
            output = "sha256:" + "2" * 64
        elif command[0] == "git":
            output = "c" * 40
        elif "ps" in command and "--format" in command:
            output = json.dumps(
                [
                    {
                        "Service": "api",
                        "State": "exited",
                        "Health": "unhealthy",
                        "ExitCode": 1,
                        "Command": "private-command-must-not-escape",
                        "Labels": "secret-label",
                    }
                ]
            )
        elif "down" in command:
            history.append(("cleanup", image))
        return subprocess.CompletedProcess(command, returncode, output.encode(), b"")

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(DRIVER.subprocess, "run", run)
    monkeypatch.setattr(DRIVER.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        "sys.argv",
        [
            "rehearse",
            "--confirm",
            "DISPOSABLE",
            "--api-image",
            new_image,
            "--web-image",
            "sixsentences-community-web:ci",
            "--upgrade-from",
            old_image,
            "--report",
            str(report),
        ],
    )
    if fail_rollback:
        with pytest.raises(RuntimeError, match="verify-rollback"):
            DRIVER.main()
        assert not report.exists()
        output = capsys.readouterr().out
        assert '"service": "api"' in output and '"exit_code": 1' in output
        assert "private" not in output and "secret" not in output
    else:
        DRIVER.main()
        receipt = json.loads(report.read_text())
        assert receipt["preupgrade_snapshot_rollback"] is True
        assert receipt["rollback_database_revision"] == "alpha1_revision"
        assert receipt["rollback_data_lossless"] is False
        assert receipt["schema_downgrade"] is False
    expected = [
        ("seed", old_image),
        ("prepare-rollback", old_image),
        ("database-revision", old_image),
        ("backup", old_image),
        ("upgrade", new_image),
        ("prepare-erasure-pinboard", new_image),
        ("backup", new_image),
        ("mutate", new_image),
        ("erase", new_image),
        ("restore-1", new_image),
        ("verify", new_image),
        ("verify-erasure", new_image),
        ("restore-0", old_image),
        ("database-revision", old_image),
        ("verify-rollback", old_image),
    ]
    assert history[: len(expected)] == expected
    assert history[-1][0] == "cleanup"


@pytest.mark.parametrize(
    "payload",
    [
        '{"Service":"api","State":"running","Health":"healthy","ExitCode":0}',
        '[{"Service":"api","State":"running","Health":"healthy","ExitCode":0}]',
    ],
)
def test_service_diagnostics_allowlist_fields(payload: str) -> None:
    assert DRIVER.sanitized_service_states(payload) == [
        {
            "service": "api",
            "state": "running",
            "health": "healthy",
            "exit_code": 0,
        }
    ]


@pytest.mark.parametrize("payload", ["not-json", "null", "42", '{"Service":[]}', "[]"])
def test_service_diagnostics_reject_unexpected_records(payload: str) -> None:
    assert DRIVER.sanitized_service_states(payload) == []
