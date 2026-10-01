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
from sqlalchemy import select

from sixsentences_server.api.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.db import User, UserLegalEventRow, db_session, get_engine, init_db
from sixsentences_server.core.legal import legal_reaccept_required
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
    with pytest.raises(SystemExit, match="explicitly disposable"):
        FIXTURE.bootstrap_corpus()


def test_legacy_corpus_bootstrap_is_real_synthetic_and_never_replaces_content(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sixsentences_server.corpus.duckdb_store import DuckDBCorpus

    monkeypatch.setenv("SIX_COMMUNITY_REHEARSAL", "DISPOSABLE")
    FIXTURE.bootstrap_corpus()
    corpus = DuckDBCorpus(settings.corpus_dir)
    assert corpus.exists()
    checked = corpus.verify()
    assert checked["ok"] is True and checked["works"] == 1
    assert checked["release_approved"] is False
    assert corpus.info()["sources"] == {"synthetic": "release-rehearsal-seed-42"}
    record = corpus.lookup(work_id=FIXTURE.CORPUS_ID)[0]
    assert record.source == "synthetic"
    assert record.doi is None and record.authors == []
    assert record.title == "Synthetic release rehearsal record"
    before = {path.name: path.read_bytes() for path in settings.corpus_dir.iterdir()}
    with pytest.raises(SystemExit, match="replace any existing corpus"):
        FIXTURE.bootstrap_corpus()
    assert {path.name: path.read_bytes() for path in settings.corpus_dir.iterdir()} == before


def test_fresh_candidate_requires_no_corpus_and_real_readiness(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def ready(path: str) -> dict[str, str]:
        calls.append(path)
        return {"status": "ready"}

    monkeypatch.setattr(FIXTURE, "request", ready)
    FIXTURE.verify_no_corpus()
    assert calls == ["/health/ready"]
    settings.corpus_dir.joinpath(".corpus.lock").touch()
    FIXTURE.verify_no_corpus()
    settings.corpus_dir.joinpath("unexpected-fixture.parquet").write_bytes(b"synthetic sentinel")
    with pytest.raises(AssertionError):
        FIXTURE.verify_no_corpus()
    assert calls == ["/health/ready", "/health/ready"]


def test_fresh_candidate_cannot_claim_ready_from_a_nonready_response(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del settings
    monkeypatch.setattr(FIXTURE, "request", lambda _path: {"status": "not_ready"})
    with pytest.raises(AssertionError):
        FIXTURE.verify_no_corpus()


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
    # Match the real rehearsal Compose gate. Version strings alone are not
    # acceptance: age confirmation and the owner agreement event are required.
    settings.enforce_legal_acceptance = True
    # The actual restore also restores the database-backed rate-limit windows;
    # a process-local limiter would incorrectly survive the snapshot rollback.
    settings.rate_limit_backend = "database"
    init_db()
    client = TestClient(create_app())
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(FIXTURE, "MARKER", settings.data_dir / "rehearsal.txt")
    monkeypatch.setattr(FIXTURE, "ERASURE_STATE", settings.data_dir / "erasure-fixture.json")
    monkeypatch.setenv("SIX_COMMUNITY_REHEARSAL", "DISPOSABLE")
    acceptance_calls: list[str] = []

    def request(
        path: str,
        *,
        token: str = "",
        body: object = None,
        method: str = "GET",
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if path == "/auth/legal-acceptance":
            # Neither newly provisioned owner can use protected product routes
            # until the real acceptance endpoint records all declarations.
            assert client.get("/auth/pinboard", headers=headers).status_code == 428
            acceptance_calls.append(path)
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
    assert acceptance_calls == ["/auth/legal-acceptance", "/auth/legal-acceptance"]
    with db_session() as session:
        for email in (FIXTURE.EMAIL, FIXTURE.ERASURE_EMAIL):
            user = session.scalar(select(User).where(User.email == email))
            assert user is not None and user.age_requirement_confirmed_at is not None
            assert not legal_reaccept_required(user)
            events = session.scalars(
                select(UserLegalEventRow).where(UserLegalEventRow.user_id == user.id)
            ).all()
            assert {(event.document_id, event.event_kind) for event in events} == {
                ("minimum_age", "age_confirmed"),
                ("terms", "contract_accepted"),
                ("privacy", "notice_presented"),
                ("operator_agreement", "contract_accepted"),
            }
            agreement = next(event for event in events if event.document_id == "operator_agreement")
            assert agreement.actor_role == "owner"
            assert agreement.controller_name.startswith("Synthetic rehearsal controller")
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


@pytest.mark.parametrize("failed_phase", [None, "verify-rollback", "verify-no-corpus"])
def test_driver_restores_distinct_snapshots_with_matching_api_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_phase: str | None,
) -> None:
    """Verify orchestration only; actual released-image compatibility needs Docker CI."""
    report = tmp_path / "rehearsal.json"
    old_image = (
        "ghcr.io/sixsentences/community-api@sha256:"
        "31b374cfb4b45c2cceb6a609d3b0ec8853ee47cd0a3d1588dfa48305bcb3498d"
    )
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
        elif "python" in command and ("exec" in command or "run" in command):
            phase = command[command.index("python") + 2]
            history.append((phase, image))
            if phase == "bootstrap-corpus":
                assert "run" in command and "--no-deps" in command
                assert "SIX_COMMUNITY_REHEARSAL=DISPOSABLE" in command
                assert (
                    kwargs["input"] == (ROOT / "deploy/community/rehearsal_fixture.py").read_bytes()
                )
            if phase == "verify-root-permission-denied":
                assert command[command.index("--user") + 1] == "0:0"
                assert "--cap-add" not in command
            if phase == "database-revision":
                output = "20260912_0001\n"
            elif phase == "erase":
                output = json.dumps({"signature": "a" * 64})
            elif phase == failed_phase:
                returncode = 1
                output = "private-output-must-not-escape"
        elif command[1:3] == ["image", "inspect"]:
            output = "sha256:" + "2" * 64
        elif command[0] == "git":
            output = "c" * 40
        elif command[-3:] == ["alembic", "upgrade", "20260912_0001"]:
            history.append(("bootstrap-original-revision", image))
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
    if failed_phase:
        with pytest.raises(RuntimeError, match=failed_phase):
            DRIVER.main()
        assert not report.exists()
        output = capsys.readouterr().out
        assert '"service": "api"' in output and '"exit_code": 1' in output
        assert "private" not in output and "secret" not in output
    else:
        DRIVER.main()
        receipt = json.loads(report.read_text())
        assert receipt["preupgrade_snapshot_rollback"] is True
        assert receipt["rollback_database_revision"] == "20260912_0001"
        assert receipt["baseline_bootstrap"] == DRIVER.baseline_bootstrap_evidence(old_image)
        assert receipt["rollback_data_lossless"] is False
        assert receipt["schema_downgrade"] is False
        assert receipt["synthetic_corpus_bootstrap"] is True
        assert receipt["fresh_candidate_without_corpus"] is True
    expected = [
        ("bootstrap-original-revision", new_image),
        ("bootstrap-corpus", old_image),
        ("seed", old_image),
        ("prepare-rollback", old_image),
        ("database-revision", old_image),
        ("backup", old_image),
        ("upgrade", new_image),
        ("prepare-erasure-pinboard", new_image),
        ("backup", new_image),
        ("mutate", new_image),
        ("erase", new_image),
        ("verify-root-permission-denied", new_image),
        ("restore-1", new_image),
        ("verify", new_image),
        ("verify-erasure", new_image),
        ("restore-0", old_image),
        ("database-revision", old_image),
        ("verify-rollback", old_image),
    ]
    assert history[: len(expected)] == expected
    assert history[-1][0] == "cleanup"
    assert history.count(("bootstrap-corpus", old_image)) == 1
    assert ("bootstrap-corpus", new_image) not in history
    if failed_phase != "verify-rollback":
        no_corpus = history.index(("verify-no-corpus", new_image))
        assert history[no_corpus - 1] == ("cleanup", old_image)


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


def test_migration_diagnostics_never_export_messages_queries_or_private_paths() -> None:
    payload = (
        'File "/workspace/services/api/alembic/env.py", line 42, in run_migrations\n'
        'File "/private/secret/customer.py", line 7, in private_function\n'
        "sqlalchemy.exc.ProgrammingError: private query and private credential\n"
        "psycopg.errors.DuplicateTable: private table exists\n"
        "SECRET_VALUE=must-not-escape\n"
    )
    assert DRIVER.sanitized_migration_failure(payload) == {
        "exception_types": ["ProgrammingError", "DuplicateTable"],
        "public_migration_frames": [{"file": "env.py", "line": 42}],
    }


def test_migration_diagnostics_ignore_unknown_exception_text() -> None:
    assert DRIVER.sanitized_migration_failure("UnknownCustomerError: private-content") == {
        "exception_types": [],
        "public_migration_frames": [],
    }


def test_restore_diagnostics_only_include_fixed_phase_labels() -> None:
    assert DRIVER.sanitized_restore_failure(
        "SIX_RESTORE_FAILURE_PHASE=live_journal_selection\n"
        "SIX_RESTORE_FAILURE_PHASE=private_secret\n"
        "SIX_RESTORE_FAILURE_PHASE=erasure_replay credentials\n"
        "private command and journal bytes\n"
    ) == ["live_journal_selection"]


@pytest.mark.parametrize("outcome", ["denied", "readable", "missing"])
def test_unprivileged_root_probe_requires_permission_denial(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    monkeypatch.setenv("SIX_COMMUNITY_REHEARSAL", "DISPOSABLE")
    monkeypatch.setattr(FIXTURE.os, "geteuid", lambda: 0)
    monkeypatch.setattr("sys.argv", ["fixture", "verify-root-permission-denied"])

    def read_bytes(path: Path) -> bytes:
        assert path == settings.resolved_erasure_ledger_path
        if outcome == "denied":
            raise PermissionError("synthetic permission refusal")
        if outcome == "missing":
            raise FileNotFoundError("synthetic absent journal")
        return b"synthetic-readable-journal"

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    if outcome == "denied":
        FIXTURE.main()
    else:
        with pytest.raises(AssertionError if outcome == "readable" else FileNotFoundError):
            FIXTURE.main()


def test_historical_bootstrap_changes_only_the_verified_boolean_default() -> None:
    """Bind the fix to the actual alpha.1 file hash, not just a matching filename."""
    old_image = (
        "ghcr.io/sixsentences/community-api@sha256:"
        "31b374cfb4b45c2cceb6a609d3b0ec8853ee47cd0a3d1588dfa48305bcb3498d"
    )
    evidence = DRIVER.baseline_bootstrap_evidence(old_image)
    assert evidence["original_source_revision"] == "f307c39b68038a4baf639e82cee673a536184ec6"
    assert evidence["original_migration_sha256"] == (
        "0b2acc3adc93a5e9a465d97f9ab0f21b33139aefb2337c945803fbf47fd04a27"
    )
    assert evidence["compatible_migration_sha256"] == (
        "fc429fc83fbf510829241310e410900a5f6d18f0b38545d6e1095f22ba795af8"
    )
    assert evidence["unmodified_original_installer"] is False
    with pytest.raises(RuntimeError, match="Unsupported historical baseline"):
        DRIVER.baseline_bootstrap_evidence("example.invalid/unreviewed:latest")


@pytest.mark.parametrize("change", [b"\n", b"# unexpected migration change\n"])
def test_historical_bootstrap_rejects_any_other_migration_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: bytes
) -> None:
    relative = Path("services/api/alembic/versions/20260912_0001_community_baseline.py")
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / relative).read_bytes() + change)
    monkeypatch.setattr(DRIVER, "ROOT", tmp_path)
    old_image = json.loads((DRIVER.COMMUNITY / "alpha1-baseline-compatibility.json").read_text())[
        "original_api_reference"
    ]
    with pytest.raises(RuntimeError, match="differs beyond the reviewed Boolean literal"):
        DRIVER.baseline_bootstrap_evidence(old_image)
