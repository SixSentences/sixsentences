"""Private GitHub repository connection, egress, and privacy contracts."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from sixsentences_server.api.app import _privacy_export_tables, create_app
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.credentials import decrypt_credential
from sixsentences_server.core.db import (
    BackgroundJobRow,
    CapacityReservationRow,
    FigureRow,
    LLMCallRow,
    Org,
    ProviderCostRow,
    RepositoryAnalysisRow,
    RepositoryConnectionRow,
    User,
    db_session,
)
from sixsentences_server.core.entitlements import finish_ai_action
from sixsentences_server.core.ratelimit import RateLimiter
from sixsentences_server.core.security_logging import install_sensitive_log_filter, redact_log_value
from sixsentences_server.jobs import cancel_repository_analysis_jobs
from sixsentences_server.repositories.analyze import analyze_repository_archive
from sixsentences_server.repositories.ingest import (
    RepositoryIngestError,
    _redact_content,
    download_github_archive,
    inspect_repository_archive,
    parse_public_github_repository,
)

PASSWORD = "StrongPass123!"
PRIVATE_TOKEN = "github_pat_private.Credential-0123456789abcdef"
REPOSITORY_URL = "https://github.com/acme/private-repository-canary"
REPOSITORY_ID = 9876543210
COMMIT_SHA = "a" * 40


def _database_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("SIX_JOBS_BACKEND", "database")
    get_settings.cache_clear()
    api_module = importlib.import_module("sixsentences_server.api.app")
    monkeypatch.setattr(
        api_module, "_repository_connection_limiter", RateLimiter(max_hits=100, window_seconds=300)
    )
    return create_app()


def _register(app: FastAPI, *, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "org_name": org,
            "name": "Private repository tester",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    with db_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        tenant = session.get(Org, user.org_id)
        assert tenant is not None
        tenant.plan = "community"
    return client


def _member(app: FastAPI, owner: TestClient, *, email: str) -> TestClient:
    added = owner.post(
        "/orgs/current/users", json={"email": email, "password": PASSWORD, "role": "member"}
    )
    assert added.status_code == 201, added.text
    member = TestClient(app)
    login = member.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    member.headers["Authorization"] = f"Bearer {login.json()['token']}"
    return member


def _github_record() -> dict[str, Any]:
    return {
        "id": REPOSITORY_ID,
        "full_name": "acme/private-repository-canary",
        "owner": "acme",
        "name": "private-repository-canary",
        "repository_url": REPOSITORY_URL,
        "visibility": "private",
        "default_branch": "main",
    }


def _stub_connection_probe(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    api_module = importlib.import_module("sixsentences_server.api.app")
    observed_tokens: list[str] = []

    def repository_record(
        _repository: object, *, access_token: str, **_kwargs: object
    ) -> dict[str, Any]:
        observed_tokens.append(access_token)
        return _github_record()

    def resolve_ref(
        _repository: object, _ref: object, *, access_token: str, **_kwargs: object
    ) -> tuple[str, str]:
        observed_tokens.append(access_token)
        return ("main", COMMIT_SHA)

    def probe_archive(
        _repository: object, _commit_sha: str, *, access_token: str, **_kwargs: object
    ) -> None:
        observed_tokens.append(access_token)

    monkeypatch.setattr(api_module, "github_repository_record", repository_record)
    monkeypatch.setattr(api_module, "resolve_github_ref", resolve_ref)
    monkeypatch.setattr(api_module, "probe_github_archive_access", probe_archive)
    return observed_tokens


def _connect(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    observed_tokens = _stub_connection_probe(monkeypatch)
    response = client.post(
        "/repository-connections",
        json={
            "repository_url": REPOSITORY_URL,
            "access_token": PRIVATE_TOKEN,
            "credential_storage_confirmed": True,
        },
    )
    assert response.status_code == 201, response.text
    assert observed_tokens == [PRIVATE_TOKEN, PRIVATE_TOKEN, PRIVATE_TOKEN]
    return response.json()


def _analysis_request(request_id: str, connection_id: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "repository_url": REPOSITORY_URL,
        "goal": "Explain the private repository architecture",
        "diagram_kind": "architecture",
        "language": "en",
        "rights_confirmed": True,
    }
    if connection_id is not None:
        payload["repository_connection_id"] = connection_id
    return payload


def _archive() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "private-repository-canary/src/main.py",
            "from .worker import run\n\ndef main():\n    return run()\n",
        )
        archive.writestr("private-repository-canary/src/worker.py", "def run():\n    return 1\n")
    return payload.getvalue()


@pytest.mark.parametrize(
    "body",
    [
        {"access_token": PRIVATE_TOKEN, "credential_storage_confirmed": True},
        {"repository_url": REPOSITORY_URL, "access_token": PRIVATE_TOKEN},
        {
            "repository_url": REPOSITORY_URL,
            "access_token": PRIVATE_TOKEN,
            "credential_storage_confirmed": False,
        },
        {
            "repository_url": REPOSITORY_URL,
            "access_token": PRIVATE_TOKEN,
            "credential_storage_confirmed": True,
            "unexpected": PRIVATE_TOKEN,
        },
    ],
)
def test_connection_validation_never_reflects_write_only_token(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="validation@example.org", org="PAT validation")
    response = owner.post("/repository-connections", json=body)
    assert response.status_code == 422
    assert PRIVATE_TOKEN not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_connection_malformed_json_and_manual_token_errors_are_secret_free(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="malformed@example.org", org="PAT malformed")
    malformed = owner.post(
        "/repository-connections",
        content=f'{{"repository_url":"https://github.com/acme/repo","access_token":"{PRIVATE_TOKEN}"',
        headers={"Content-Type": "application/json"},
    )
    non_ascii_token = f"{PRIVATE_TOKEN}🚫"
    invalid = owner.post(
        "/repository-connections",
        json={
            "repository_url": REPOSITORY_URL,
            "access_token": non_ascii_token,
            "credential_storage_confirmed": True,
        },
    )
    assert malformed.status_code == 422
    assert PRIVATE_TOKEN not in malformed.text
    assert invalid.status_code == 422
    assert non_ascii_token not in invalid.text
    assert invalid.json()["detail"]["code"] == "github_connection_failed"


def test_connection_crud_is_creator_private_and_ciphertext_only(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="owner@example.org", org="Private repositories")
    member = _member(app, owner, email="member@example.org")
    connection = _connect(owner, monkeypatch)
    assert PRIVATE_TOKEN not in json.dumps(connection)
    assert connection == {
        "id": connection["id"],
        "provider": "github",
        "repository_url": REPOSITORY_URL,
        "owner": "acme",
        "name": "private-repository-canary",
        "created_at": connection["created_at"],
        "updated_at": connection["updated_at"],
        "last_used_at": None,
    }
    listed = owner.get("/repository-connections")
    assert listed.status_code == 200
    assert listed.json() == [connection]
    assert PRIVATE_TOKEN not in listed.text
    assert member.get("/repository-connections").json() == []
    assert member.delete(f"/repository-connections/{connection['id']}").status_code == 404
    with db_session() as session:
        row = session.scalar(select(RepositoryConnectionRow))
        assert row is not None
        assert PRIVATE_TOKEN not in row.credential_encrypted
        assert row.github_repository_id == REPOSITORY_ID
        assert row.credential_storage_version == "github-pat-storage-v1"
        assert row.credential_storage_confirmed_at is not None
        envelope = json.loads(decrypt_credential(row.credential_encrypted))
        assert envelope == {
            "access_token": PRIVATE_TOKEN,
            "creator_user_id": row.creator_user_id,
            "github_repository_id": REPOSITORY_ID,
            "org_id": row.org_id,
            "version": 1,
        }


def test_connection_routes_reject_machine_credentials_and_kill_switch_prevents_probe(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="machine@example.org", org="Machine connection")
    connection = _connect(owner, monkeypatch)
    key_response = owner.post("/auth/api-keys", json={"name": "repository robot"})
    assert key_response.status_code == 201, key_response.text
    robot = TestClient(app)
    robot.headers["Authorization"] = f"Bearer {key_response.json()['api_key']}"
    valid_body = {
        "repository_url": REPOSITORY_URL,
        "access_token": PRIVATE_TOKEN,
        "credential_storage_confirmed": True,
    }
    assert robot.get("/repository-connections").status_code == 403
    assert robot.post("/repository-connections", json=valid_body).status_code == 403
    assert robot.delete(f"/repository-connections/{connection['id']}").status_code == 403
    api_module = importlib.import_module("sixsentences_server.api.app")
    disabled = get_settings().model_copy(update={"repository_analysis_enabled": False})
    monkeypatch.setattr(api_module, "get_settings", lambda: disabled)
    probe_calls: list[str] = []
    monkeypatch.setattr(
        api_module,
        "github_repository_record",
        lambda *_args, **_kwargs: probe_calls.append("metadata"),
    )
    disabled_response = owner.post("/repository-connections", json=valid_body)
    assert disabled_response.status_code == 409
    assert disabled_response.json()["detail"]["code"] == "repository_analysis_disabled"
    assert probe_calls == []


def test_creator_export_and_log_redaction_never_include_pat_or_ciphertext(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="export@example.org", org="Connection export")
    _connect(owner, monkeypatch)
    with db_session() as session:
        owner_row = session.scalar(select(User).where(User.email == "export@example.org"))
        connection_row = session.scalar(select(RepositoryConnectionRow))
        assert owner_row is not None and connection_row is not None
        ciphertext = connection_row.credential_encrypted
        tables, _inventory = _privacy_export_tables(session, user=owner_row)
    exported = json.dumps(tables)
    assert PRIVATE_TOKEN not in exported
    assert ciphertext not in exported
    assert "credential_encrypted" not in exported
    assert tables[RepositoryConnectionRow.__tablename__][0]["repository_url"] == REPOSITORY_URL
    for token in (PRIVATE_TOKEN, "gho_A1b2.C3d4-E5f6_7890token", "ghu_A1b2.C3d4-E5f6_7890token"):
        assert token not in redact_log_value(f"GitHub credential {token}")


def test_owner_export_excludes_orphaned_member_repository_auxiliary_rows(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="orphan-owner@example.org", org="Orphan export")
    member = _member(app, owner, email="orphan-member@example.org")
    connection = _connect(member, monkeypatch)
    created = member.post(
        "/repository-analyses", json=_analysis_request("orphan-analysis", connection["id"])
    )
    assert created.status_code == 202, created.text
    with db_session() as session:
        analysis = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == created.json()["public_id"]
            )
        )
        assert analysis is not None
        analysis_id = analysis.id
        action_id = analysis.action_id
        job_ids = {
            row.id
            for row in session.scalars(
                select(BackgroundJobRow).where(
                    BackgroundJobRow.task == "_execute_repository_analysis"
                )
            ).all()
            if str(analysis_id) in {str(argument) for argument in list(row.args or [])}
        }
        session.add(
            LLMCallRow(
                org_id=analysis.org_id,
                action_id=action_id,
                resource_type="repository_analysis",
                resource_id=str(analysis_id),
                task="repository_analysis",
                provider="test",
                model="test-model",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
                cost_source="provider",
                duration_ms=1,
            )
        )
        session.add(
            ProviderCostRow(
                org_id=analysis.org_id,
                action_id=action_id,
                resource_type="repository_analysis",
                resource_id=str(analysis_id),
                task="repository_analysis",
                provider="test",
                model="test-model",
                cost_usd=0.0,
                cost_source="provider",
                request_count=1,
            )
        )
    deleted = member.request("DELETE", "/auth/account", json={"password": PASSWORD})
    assert deleted.status_code == 200, deleted.text
    with db_session() as session:
        assert session.get(RepositoryAnalysisRow, analysis_id) is None
        owner_row = session.scalar(select(User).where(User.email == "orphan-owner@example.org"))
        assert owner_row is not None
        tables, _inventory = _privacy_export_tables(session, user=owner_row)
    assert all(row["id"] not in job_ids for row in tables.get(BackgroundJobRow.__tablename__, []))
    assert all(
        row.get("action_id") != action_id
        for row in tables.get(CapacityReservationRow.__tablename__, [])
    )
    assert all(
        row.get("action_id") != action_id for row in tables.get(LLMCallRow.__tablename__, [])
    )
    assert all(
        row.get("action_id") != action_id for row in tables.get(ProviderCostRow.__tablename__, [])
    )


def test_private_analysis_binds_connection_identity_and_fingerprint(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="analysis@example.org", org="Private analysis")
    member = _member(app, owner, email="analysis-member@example.org")
    connection = _connect(owner, monkeypatch)
    created = owner.post(
        "/repository-analyses", json=_analysis_request("private-analysis-request", connection["id"])
    )
    assert created.status_code == 202, created.text
    assert created.json()["repository_access"] == "private"
    replay_as_public = owner.post(
        "/repository-analyses", json=_analysis_request("private-analysis-request", None)
    )
    assert replay_as_public.status_code == 409
    mismatch = owner.post(
        "/repository-analyses",
        json={
            **_analysis_request("private-analysis-mismatch", connection["id"]),
            "repository_url": "https://github.com/acme/another-private-repository",
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "repository_connection_mismatch"
    isolated = member.post(
        "/repository-analyses", json=_analysis_request("private-analysis-member", connection["id"])
    )
    assert isolated.status_code == 404
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == created.json()["public_id"]
            )
        )
        connection_row = session.scalar(select(RepositoryConnectionRow))
        assert row is not None and connection_row is not None
        assert row.repository_connection_id == connection_row.id
        assert row.repository_access == "private"
        assert connection["id"] in json.dumps(
            {"connection_id": connection["id"], "fingerprint": row.request_fingerprint}
        )


def test_disconnect_cancels_active_work_and_detaches_ready_private_analysis(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="disconnect@example.org", org="Disconnect repository")
    connection = _connect(owner, monkeypatch)
    active_response = owner.post(
        "/repository-analyses", json=_analysis_request("disconnect-active", connection["id"])
    )
    ready_response = owner.post(
        "/repository-analyses", json=_analysis_request("disconnect-ready", connection["id"])
    )
    assert active_response.status_code == ready_response.status_code == 202
    with db_session() as session:
        ready = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == ready_response.json()["public_id"]
            )
        )
        assert ready is not None
        ready.status = "ready"
        ready.ref = "main"
        ready.commit_sha = COMMIT_SHA
        ready.archive_sha256 = "b" * 64
        ready.finished_at = datetime.now(UTC)
        cancel_repository_analysis_jobs(session, [ready.id])
        finish_ai_action(session, ready.action_id, status="settled")
    deleted = owner.delete(f"/repository-connections/{connection['id']}")
    assert deleted.status_code == 204, deleted.text
    assert owner.get("/repository-connections").json() == []
    with db_session() as session:
        assert session.scalar(select(RepositoryConnectionRow)) is None
        active = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == active_response.json()["public_id"]
            )
        )
        ready = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == ready_response.json()["public_id"]
            )
        )
        assert active is not None and ready is not None
        assert active.status == "cancelled"
        assert active.error_code == "repository_connection_revoked"
        assert active.repository_connection_id is None
        assert active.repository_access == "private"
        assert ready.status == "ready"
        assert ready.repository_connection_id is None
        assert ready.repository_access == "private"
        jobs = session.scalars(
            select(BackgroundJobRow).where(BackgroundJobRow.task == "_execute_repository_analysis")
        ).all()
        assert jobs and all(job.status == "cancelled" for job in jobs)
        active_reservation = session.scalar(
            select(CapacityReservationRow).where(
                CapacityReservationRow.action_id == active.action_id
            )
        )
        assert active_reservation is not None
        assert active_reservation.status == "cancelled"


def test_private_worker_pins_sha_before_archive_egress_and_never_falls_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="worker@example.org", org="Private worker")
    connection = _connect(owner, monkeypatch)
    created = owner.post(
        "/repository-analyses",
        json={**_analysis_request("private-worker", connection["id"]), "ref": "moving-branch"},
    )
    assert created.status_code == 202, created.text
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == created.json()["public_id"]
            )
        )
        assert row is not None
        analysis_id = row.id
    api_module = importlib.import_module("sixsentences_server.api.app")
    record_tokens: list[str] = []
    resolve_tokens: list[str] = []
    archive_calls: list[tuple[str, str]] = []

    def record(_repository: object, *, access_token: str, **_kwargs: object) -> dict[str, Any]:
        record_tokens.append(access_token)
        return _github_record()

    def resolve(
        _repository: object, ref: str, *, access_token: str, **_kwargs: object
    ) -> tuple[str, str]:
        assert ref == "moving-branch"
        resolve_tokens.append(access_token)
        return (ref, COMMIT_SHA)

    def download(
        _repository: object, commit_sha: str, *, access_token: str, **_kwargs: object
    ) -> bytes:
        archive_calls.append((commit_sha, access_token))
        if len(archive_calls) == 1:
            raise KeyboardInterrupt("synthetic worker crash")
        raise RepositoryIngestError("synthetic stop", code="synthetic_stop")

    monkeypatch.setattr(api_module, "github_repository_record", record)
    monkeypatch.setattr(api_module, "resolve_github_ref", resolve)
    monkeypatch.setattr(api_module, "download_github_archive", download)
    with pytest.raises(KeyboardInterrupt, match="synthetic worker crash"):
        api_module._execute_repository_analysis(analysis_id)
    with db_session() as session:
        row = session.get(RepositoryAnalysisRow, analysis_id)
        assert row is not None
        assert row.ref == "moving-branch"
        assert row.commit_sha == COMMIT_SHA
        assert row.status == "fetching"
    api_module._execute_repository_analysis(analysis_id)
    assert record_tokens == [PRIVATE_TOKEN, PRIVATE_TOKEN]
    assert resolve_tokens == [PRIVATE_TOKEN]
    assert archive_calls == [(COMMIT_SHA, PRIVATE_TOKEN), (COMMIT_SHA, PRIVATE_TOKEN)]
    with db_session() as session:
        row = session.get(RepositoryAnalysisRow, analysis_id)
        assert row is not None and row.error_code == "synthetic_stop"


@pytest.mark.parametrize("record_change", [{"id": REPOSITORY_ID + 1}, {"visibility": "internal"}])
def test_private_worker_fails_identity_or_visibility_change_before_archive(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, record_change: dict[str, Any]
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="identity@example.org", org="Private identity")
    connection = _connect(owner, monkeypatch)
    created = owner.post(
        "/repository-analyses", json=_analysis_request("private-identity", connection["id"])
    )
    assert created.status_code == 202, created.text
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == created.json()["public_id"]
            )
        )
        assert row is not None
        analysis_id = row.id
    api_module = importlib.import_module("sixsentences_server.api.app")
    changed_record = {**_github_record(), **record_change}
    observed_tokens: list[str] = []
    downstream_calls: list[str] = []

    def record(_repository: object, *, access_token: str, **_kwargs: object) -> dict[str, Any]:
        observed_tokens.append(access_token)
        return changed_record

    monkeypatch.setattr(api_module, "github_repository_record", record)
    monkeypatch.setattr(
        api_module,
        "resolve_github_ref",
        lambda *_args, **_kwargs: downstream_calls.append("resolve"),
    )
    monkeypatch.setattr(
        api_module,
        "download_github_archive",
        lambda *_args, **_kwargs: downstream_calls.append("archive"),
    )
    api_module._execute_repository_analysis(analysis_id)
    assert observed_tokens == [PRIVATE_TOKEN]
    assert downstream_calls == []
    with db_session() as session:
        row = session.get(RepositoryAnalysisRow, analysis_id)
        assert row is not None
        assert row.status == "error"
        assert row.error_code == "repository_identity_changed"


def test_private_worker_uses_neutral_analysis_title_and_shared_figure_provenance(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _database_app(monkeypatch)
    owner = _register(app, email="privacy-owner@example.org", org="Private provenance")
    member = _member(app, owner, email="privacy-member@example.org")
    connection = _connect(member, monkeypatch)
    created = member.post(
        "/repository-analyses",
        json={
            **_analysis_request("private-provenance", connection["id"]),
            "ref": "private-ref-canary",
        },
    )
    assert created.status_code == 202, created.text
    with db_session() as session:
        row = session.scalar(
            select(RepositoryAnalysisRow).where(
                RepositoryAnalysisRow.public_id == created.json()["public_id"]
            )
        )
        assert row is not None
        analysis_id = row.id
    archive_blob = _archive()
    archive = inspect_repository_archive(archive_blob)
    result = analyze_repository_archive(
        archive,
        repository_name="Private repository",
        goal="Explain the private repository architecture",
        diagram_kind="architecture",
        language="en",
        pool=None,
    )
    api_module = importlib.import_module("sixsentences_server.api.app")
    captured_names: list[str] = []
    captured_opaque_provider_ids: list[bool] = []

    class SilentPool:
        def __init__(self) -> None:
            self.on_usage = None
            self.cancel_check = None
            self.budget = None

    monkeypatch.setattr(api_module, "_build_pool", lambda _settings: SilentPool())
    monkeypatch.setattr(
        api_module, "github_repository_record", lambda _repository, **_kwargs: _github_record()
    )
    monkeypatch.setattr(
        api_module,
        "resolve_github_ref",
        lambda _repository, _ref, **_kwargs: ("private-ref-canary", COMMIT_SHA),
    )
    monkeypatch.setattr(
        api_module, "download_github_archive", lambda _repository, _sha, **_kwargs: archive_blob
    )

    def analyze_stub(_archive: object, *, repository_name: str, **_kwargs: object) -> object:
        captured_names.append(repository_name)
        captured_opaque_provider_ids.append(bool(_kwargs.get("opaque_provider_ids")))
        return result

    monkeypatch.setattr(api_module, "analyze_repository_archive", analyze_stub)
    api_module._execute_repository_analysis(analysis_id)
    assert captured_names == ["Private repository"]
    assert captured_opaque_provider_ids == [True]
    exact_path_canary = "client/src/index.ts"
    derived_hash_canaries = (
        hashlib.sha256(b"password.txt").hexdigest()[:8],
        hashlib.sha256(b"src/index.ts").hexdigest()[:10],
        hashlib.sha256(b"private-dependency").hexdigest()[:10],
    )
    neutralized_label_canary = f"client/Component-{derived_hash_canaries[0]}/file-{derived_hash_canaries[1]}/dep-{derived_hash_canaries[2]}"
    with db_session() as session:
        row = session.get(RepositoryAnalysisRow, analysis_id)
        assert row is not None and row.status == "ready"
        assert "private-repository-canary" not in json.dumps(row.diagram_spec)
        diagram = dict(row.diagram_spec or {})
        nodes = list(diagram.get("nodes") or [])
        evidence = list(row.evidence or [])
        assert len(nodes) >= 2
        evidence_by_id = {str(record["id"]): record for record in evidence}
        nodes[0]["label"] = exact_path_canary
        exact_record = evidence_by_id[str(nodes[0]["evidence_ids"][0])]
        exact_record["path"] = exact_path_canary
        exact_record["summary"] = f"Source module; canonical display label {exact_path_canary};"
        nodes[1]["label"] = neutralized_label_canary
        neutral_record = evidence_by_id[str(nodes[1]["evidence_ids"][0])]
        neutral_record["summary"] = (
            f"Source module; canonical display label {neutralized_label_canary};"
        )
        diagram["nodes"] = nodes
        row.diagram_spec = diagram
        row.evidence = evidence
        canonical_provider_ids = {
            *(str(node["id"]) for node in nodes),
            *(str(edge["id"]) for edge in list(diagram.get("edges") or [])),
        }
        archive_sha256 = row.archive_sha256
        repository_action_id = row.action_id
        analysis_job_ids = {
            job.id
            for job in session.scalars(
                select(BackgroundJobRow).where(
                    BackgroundJobRow.task == "_execute_repository_analysis"
                )
            ).all()
            if str(analysis_id) in {str(argument) for argument in list(job.args or [])}
        }
        session.add(
            LLMCallRow(
                org_id=row.org_id,
                action_id=row.action_id,
                resource_type="repository_analysis",
                resource_id=str(row.id),
                task="repository_analysis",
                provider="test",
                model="test-model",
                input_tokens=7,
                output_tokens=3,
                cost_usd=0.0,
                cost_source="provider",
                duration_ms=1,
            )
        )
        session.add(
            ProviderCostRow(
                org_id=row.org_id,
                action_id=row.action_id,
                resource_type="repository_analysis",
                resource_id=str(row.id),
                task="repository_analysis",
                provider="test",
                model="test-model",
                cost_usd=0.0,
                cost_source="provider",
                request_count=1,
            )
        )
        reservations_before = len(session.scalars(select(CapacityReservationRow)).all())
        jobs_before = len(session.scalars(select(BackgroundJobRow)).all())
    figure_settings = get_settings().model_copy(
        update={"gemini_api_key": "test-google-key", "gemini_data_processing_confirmed": True}
    )
    monkeypatch.setattr(api_module, "get_settings", lambda: figure_settings)
    stale_goal = member.post(
        "/figures",
        json={
            "prompt": "A changed visual explanation",
            "repository_analysis_id": created.json()["public_id"],
        },
    )
    assert stale_goal.status_code == 409
    assert stale_goal.json()["detail"]["code"] == "repository_goal_mismatch"
    other_project = member.post("/projects", json={"name": "Unrelated project"})
    assert other_project.status_code == 200, other_project.text
    wrong_project = member.post(
        "/figures",
        json={
            "prompt": "Explain the private repository architecture",
            "repository_analysis_id": created.json()["public_id"],
            "project_id": other_project.json()["id"],
        },
    )
    assert wrong_project.status_code == 409
    with db_session() as session:
        assert len(session.scalars(select(CapacityReservationRow)).all()) == reservations_before
        assert len(session.scalars(select(BackgroundJobRow)).all()) == jobs_before
    figure_response = member.post(
        "/figures",
        json={
            "prompt": "Explain the private repository architecture",
            "repository_analysis_id": created.json()["public_id"],
        },
    )
    assert figure_response.status_code == 202, figure_response.text
    figure = figure_response.json()
    assert figure["repository_analysis_id"] is None
    assert set(figure["config"]["repository"]) == {
        "repository_access",
        "diagram_kind",
        "verification_status",
        "spec_node_count",
        "spec_edge_count",
    }
    assert figure["config"]["repository"]["repository_access"] == "private"
    with db_session() as session:
        figure_row = session.scalar(
            select(FigureRow).where(FigureRow.public_id == figure["public_id"])
        )
        assert figure_row is not None
        figure_id = figure_row.id
    rendered_contexts: list[str] = []

    def render_private_repository_figure(*_args: object, **kwargs: object) -> bytes:
        rendered_contexts.append(str(kwargs.get("context") or ""))
        return b"private-repository-figure"

    monkeypatch.setattr(api_module, "render_figure", render_private_repository_figure)
    api_module._execute_figure(figure_id)
    assert len(rendered_contexts) == 1
    renderer_context = rendered_contexts[0]
    assert exact_path_canary not in renderer_context
    assert "password.txt" not in renderer_context
    assert "private-dependency" not in renderer_context
    assert neutralized_label_canary not in renderer_context
    assert all(value not in renderer_context for value in derived_hash_canaries)
    assert all(identifier not in renderer_context for identifier in canonical_provider_ids)
    renderer_topology = json.loads(renderer_context)["diagram"]
    assert all(str(node["node_id"]).startswith("n_") for node in renderer_topology["nodes"])
    assert all(str(edge["edge_id"]).startswith("e_") for edge in renderer_topology["edges"])
    assert owner.get(f"/repository-analyses/{created.json()['public_id']}").status_code == 404
    collaborator_figure = owner.get(f"/figures/{figure['public_id']}")
    assert collaborator_figure.status_code == 200
    assert collaborator_figure.json()["repository_analysis_id"] is None
    collaborator_figures = owner.get("/figures")
    assert collaborator_figures.status_code == 200
    source = owner.get(f"/figures/{figure['public_id']}/source")
    assert source.status_code == 200, source.text
    forbidden = {
        "acme",
        "private-repository-canary",
        REPOSITORY_URL,
        "private-ref-canary",
        COMMIT_SHA,
        archive_sha256,
        "src/main.py",
        exact_path_canary,
        "password.txt",
        "private-dependency",
        neutralized_label_canary,
        *derived_hash_canaries,
        *canonical_provider_ids,
        "grounding_sha256",
    }
    assert all(value not in figure_response.text for value in forbidden)
    assert all(value not in collaborator_figure.text for value in forbidden)
    assert all(value not in collaborator_figures.text for value in forbidden)
    assert all(value not in source.text for value in forbidden)
    with db_session() as session:
        owner_row = session.scalar(select(User).where(User.email == "privacy-owner@example.org"))
        assert owner_row is not None
        tables, _inventory = _privacy_export_tables(session, user=owner_row)
        exported_figure = next(
            item
            for item in tables[FigureRow.__tablename__]
            if item["public_id"] == figure["public_id"]
        )
        assert exported_figure["repository_analysis_id"] is None
        serialized_export = json.dumps(exported_figure)
        assert all(value not in serialized_export for value in forbidden)
        assert RepositoryConnectionRow.__tablename__ not in tables
        assert RepositoryAnalysisRow.__tablename__ not in tables
        assert all(
            row["id"] not in analysis_job_ids
            for row in tables.get(BackgroundJobRow.__tablename__, [])
        )
        assert all(
            row.get("action_id") != repository_action_id
            for row in tables.get(CapacityReservationRow.__tablename__, [])
        )
        assert all(
            row.get("action_id") != repository_action_id
            for row in tables.get(LLMCallRow.__tablename__, [])
        )
        assert all(
            row.get("action_id") != repository_action_id
            for row in tables.get(ProviderCostRow.__tablename__, [])
        )


def test_private_archive_redirect_is_auth_separated_and_exactly_allowlisted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ingest_module = importlib.import_module("sixsentences_server.repositories.ingest")
    monkeypatch.setattr(ingest_module, "is_public_http_url", lambda _url: True)
    install_sensitive_log_filter()
    caplog.set_level(logging.INFO)
    repository = parse_public_github_repository(REPOSITORY_URL)
    observed: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(
            (str(request.url), request.headers.get("authorization"), request.headers.get("cookie"))
        )
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={
                    "Location": f"https://codeload.github.com/acme/private-repository-canary/legacy.zip/{COMMIT_SHA}?download=1"
                },
                request=request,
            )
        return httpx.Response(200, content=b"safe-archive", request=request)

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        auth=("default-auth-canary", "must-not-egress"),
        headers={"Cookie": "session=must-not-egress"},
    ) as client:
        payload = download_github_archive(
            repository, COMMIT_SHA, access_token=PRIVATE_TOKEN, http=client
        )
    assert payload == b"safe-archive"
    assert observed == [
        (
            f"https://api.github.com/repos/acme/private-repository-canary/zipball/{COMMIT_SHA}",
            f"Bearer {PRIVATE_TOKEN}",
            None,
        ),
        (
            f"https://codeload.github.com/acme/private-repository-canary/legacy.zip/{COMMIT_SHA}?download=1",
            None,
            None,
        ),
    ]
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert all(
        canary not in rendered_logs
        for canary in ("acme", "private-repository-canary", "download=1", PRIVATE_TOKEN)
    )


@pytest.mark.parametrize(
    "location",
    [
        f"http://codeload.github.com/acme/private-repository-canary/zip/{COMMIT_SHA}",
        f"https://evil.example/acme/private-repository-canary/zip/{COMMIT_SHA}",
        f"https://user@codeload.github.com/acme/private-repository-canary/zip/{COMMIT_SHA}",
        f"https://codeload.github.com:443/acme/private-repository-canary/zip/{COMMIT_SHA}",
        f"https://codeload.github.com/other/private-repository-canary/zip/{COMMIT_SHA}",
        f"https://codeload.github.com/acme/other/zip/{COMMIT_SHA}",
        f"https://codeload.github.com/acme/private-repository-canary/zip/{'b' * 40}",
        f"https://codeload.github.com/acme/private-repository-canary/zip/{COMMIT_SHA}#fragment",
        f"https://codeload.github.com/acme/private-repository-canary/zip/{COMMIT_SHA}?url=https%3A%2F%2Fevil.example",
        f"https://codeload.github.com/acme/private-repository-canary/zip/{COMMIT_SHA}?next=%2F%2Fevil.example",
    ],
)
def test_private_archive_redirect_attacks_fail_closed(
    monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    ingest_module = importlib.import_module("sixsentences_server.repositories.ingest")
    monkeypatch.setattr(ingest_module, "is_public_http_url", lambda _url: True)
    repository = parse_public_github_repository(REPOSITORY_URL)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": location}, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RepositoryIngestError) as raised,
    ):
        download_github_archive(repository, COMMIT_SHA, access_token=PRIVATE_TOKEN, http=client)
    assert raised.value.code == "github_redirect_refused"
    assert location not in str(raised.value)


def test_dotted_github_tokens_are_removed_from_repository_content() -> None:
    dotted_tokens = (
        "github_pat_A1b2.C3d4-E5f6_7890token",
        "gho_A1b2.C3d4-E5f6_7890token",
        "ghu_A1b2.C3d4-E5f6_7890token",
    )
    screened, redacted, hard_secret = _redact_content("\n".join(dotted_tokens))
    assert redacted is True
    assert hard_secret is False
    assert all(token not in screened for token in dotted_tokens)
