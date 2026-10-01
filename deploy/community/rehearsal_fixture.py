"""Synthetic, provider-free checks executed only inside a disposable CI stack."""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sixsentences_server.core.auth import provision_owner
from sixsentences_server.core.db import User, db_session
from sixsentences_server.core.legal import (
    CURRENT_DPA_VERSION,
    CURRENT_PRIVACY_VERSION,
    CURRENT_TERMS_VERSION,
)
from sqlalchemy import select, text

EMAIL = "release-rehearsal@example.invalid"
ERASURE_EMAIL = "release-erasure@example.invalid"
# Inert fixture credential, never used by an installed application or public host.
PASSWORD = "Disposable-Community-Fixture-42!"
MARKER = Path("/data/community-rehearsal.txt")
ERASURE_STATE = Path("/data/community-erasure-fixture.json")
ERASURE_FILENAME = "synthetic-erasure.csv"
ERASURE_BYTES = b"synthetic_value\n42\n"
NOTE = {
    "id": "00000000-0000-4000-8000-000000000042",
    "text": "Synthetic release note",
    "color": "sage",
    "shape": "note",
    "x": 0.25,
    "y": 0.5,
    "rotation": 0.0,
    "size": 1.8,
}
CORPUS_ID = "SYNTHETIC-REHEARSAL-42"


def bootstrap_corpus() -> None:
    """Build one real, clearly synthetic Parquet record for the legacy API only."""
    if os.environ.get("SIX_COMMUNITY_REHEARSAL") != "DISPOSABLE":
        raise SystemExit("Refusing fixture outside an explicitly disposable container")
    import pyarrow as pa
    import pyarrow.parquet as pq
    from sixsentences_server.config import get_settings
    from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
    from sixsentences_server.corpus.ingest import WORKS_SCHEMA

    corpus_path = get_settings().data_dir / "corpus"
    if corpus_path.is_symlink() or (
        corpus_path.exists() and (not corpus_path.is_dir() or any(corpus_path.iterdir()))
    ):
        raise SystemExit("Refusing to replace any existing corpus content")
    corpus_path.mkdir(parents=True, exist_ok=True)
    store = DuckDBCorpus(corpus_path)
    table = pa.Table.from_pylist(
        [
            {
                "id": CORPUS_ID,
                "doi": None,
                "title": "Synthetic release rehearsal record",
                "abstract": (
                    "Invented fixture content, not a research publication or quality benchmark."
                ),
                "year": 2026,
                "venue": "Synthetic fixture",
                "authors": "[]",
                "cited_by_count": 0,
                "is_retracted": False,
                "source": "synthetic",
                "referenced_works": "[]",
                "open_access": "{}",
                "work_type": "article",
                "corpus_slice": "synthetic-release-rehearsal",
            },
        ],
        schema=WORKS_SCHEMA,
    )
    pq.write_table(table, store.works_path)
    store.write_meta(
        version="synthetic-release-rehearsal-seed-42",
        works=1,
        sources={"synthetic": "release-rehearsal-seed-42"},
    )
    checked = store.verify()
    assert checked["ok"] is True and checked["works"] == 1
    assert checked["release_approved"] is False
    records = store.lookup(work_id=CORPUS_ID)
    assert len(records) == 1 and records[0].id == CORPUS_ID
    assert records[0].source == "synthetic"


def verify_no_corpus() -> None:
    """Prove fresh candidate readiness without either a fixture or imported corpus."""
    from sixsentences_server.config import get_settings

    corpus_path = get_settings().data_dir / "corpus"
    assert not corpus_path.is_symlink()
    # DuckDBCorpus.exists() may create its empty locking directory. Only that
    # inert lock is permitted: no Parquet, metadata, snapshot or release file.
    if corpus_path.exists():
        assert corpus_path.is_dir()
        assert all(
            path.name == ".corpus.lock" and path.is_file() and not path.is_symlink()
            for path in corpus_path.iterdir()
        )
    assert request("/health/ready") == {"status": "ready"}


def request(path: str, *, token: str = "", body: object = None, method: str = "GET") -> Any:
    """Exercise the running API; retain no response or credential in logs."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None if body is None else json.dumps(body).encode()
    with urlopen(
        Request(f"http://127.0.0.1:8000{path}", payload, headers, method=method), timeout=20
    ) as response:
        return json.load(response)


def erasure_state() -> dict[str, int]:
    """Read only the synthetic identities frozen into the pre-erasure backup."""
    state = json.loads(ERASURE_STATE.read_text(encoding="utf-8"))
    assert isinstance(state, dict) and set(state) == {"user_id", "org_id", "dataset_id"}
    assert all(type(value) is int and value > 0 for value in state.values())
    return state


def erasure_files(state: dict[str, int]) -> tuple[Path, Path]:
    """Resolve fixed fixture-owned dataset locations, never caller-supplied paths."""
    from sixsentences_server.config import get_settings

    data = get_settings().data_dir / "datasets"
    return (
        data / f"{state['dataset_id']}-{ERASURE_FILENAME}",
        data / "versions" / str(state["dataset_id"]) / "1.csv",
    )


def prepare_erasure(*, pinboard_supported: bool = True) -> None:
    """Create a second synthetic owner and real persisted state before backup."""
    from sixsentences_server.config import get_settings
    from sixsentences_server.core.db import ResearchDatasetRow
    from sixsentences_server.ops.erasure_ledger import read_events

    assert not ERASURE_STATE.exists()
    assert not read_events(get_settings())
    with db_session() as session:
        assert session.scalars(select(User.email)).all() == [EMAIL]
        user = provision_owner(session, ERASURE_EMAIL, PASSWORD, "Synthetic erasure workspace")
        user.terms_version = CURRENT_TERMS_VERSION
        user.privacy_version = CURRENT_PRIVACY_VERSION
        user.dpa_version = CURRENT_DPA_VERSION
        dataset = ResearchDatasetRow(
            org_id=user.org_id,
            name="Synthetic erasure dataset",
            filename=ERASURE_FILENAME,
            format="csv",
            row_count=1,
            byte_size=len(ERASURE_BYTES),
        )
        session.add(dataset)
        session.flush()
        state = {"user_id": user.id, "org_id": user.org_id, "dataset_id": dataset.id}
    for path in erasure_files(state):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ERASURE_BYTES)
    # Only synthetic numeric IDs are retained; no password or bearer token.
    with ERASURE_STATE.open("x", encoding="utf-8") as handle:
        json.dump(state, handle)
    if pinboard_supported:
        prepare_erasure_pinboard()
    assert all(path.read_bytes() == ERASURE_BYTES for path in erasure_files(state))


def prepare_erasure_pinboard() -> None:
    """Add candidate-only state after the released schema has been upgraded."""
    login = request(
        "/auth/login",
        method="POST",
        body={"email": ERASURE_EMAIL, "password": PASSWORD},
    )
    note = {**NOTE, "text": "Synthetic note that must remain erased"}
    saved = request(
        "/auth/pinboard",
        method="PUT",
        token=login["token"],
        body={"revision": 0, "notes": [note]},
    )
    assert saved == {"revision": 1, "notes": [note]}


def verify_erased(signature: str, *, pinboard_supported: bool = True) -> None:
    """Require journal continuity, absent owned state and denied authentication."""
    from sixsentences_server.config import get_settings
    from sixsentences_server.core.db import AuthToken, Org, ResearchDatasetRow
    from sixsentences_server.ops.erasure_ledger import email_digest, read_events

    assert re.fullmatch(r"[0-9a-f]{64}", signature)
    state = erasure_state()
    settings = get_settings()
    events = read_events(settings)
    assert len(events) == 1
    event = events[0]
    assert event.signature == signature
    assert event.subject == "workspace" and event.reason == "account_request"
    assert event.user_id == state["user_id"] and event.org_id == state["org_id"]
    assert event.email_digest == email_digest(ERASURE_EMAIL, settings)
    with db_session() as session:
        assert session.get(User, state["user_id"]) is None
        assert session.scalar(select(User.id).where(User.email == ERASURE_EMAIL)) is None
        assert session.get(Org, state["org_id"]) is None
        if pinboard_supported:
            from sixsentences_server.core.db import PersonalPinboardRow

            assert session.get(PersonalPinboardRow, state["user_id"]) is None
        assert session.get(ResearchDatasetRow, state["dataset_id"]) is None
        assert (
            session.scalar(select(AuthToken.id).where(AuthToken.org_id == state["org_id"])) is None
        )
        assert session.scalars(select(User.email)).all() == [EMAIL]
    assert all(not path.exists() for path in erasure_files(state))
    try:
        request("/auth/login", method="POST", body={"email": ERASURE_EMAIL, "password": PASSWORD})
    except HTTPError as exc:
        assert exc.code == 401
    else:
        raise AssertionError("Erased fixture account authenticated")


def erase_after_backup() -> str:
    """Exercise normal authenticated self-service erasure, not a direct DB purge."""
    from sixsentences_server.config import get_settings
    from sixsentences_server.core.db import AuthToken, PersonalPinboardRow, ResearchDatasetRow
    from sixsentences_server.ops.erasure_ledger import read_events

    state = erasure_state()
    assert not read_events(get_settings())
    assert all(path.read_bytes() == ERASURE_BYTES for path in erasure_files(state))
    with db_session() as session:
        user = session.get(User, state["user_id"])
        assert user is not None and user.email == ERASURE_EMAIL and user.org_id == state["org_id"]
        assert set(session.scalars(select(User.email)).all()) == {EMAIL, ERASURE_EMAIL}
        assert session.get(PersonalPinboardRow, state["user_id"]) is not None
        assert session.get(ResearchDatasetRow, state["dataset_id"]) is not None
        assert session.scalar(select(AuthToken.id).where(AuthToken.org_id == state["org_id"]))
    login = request(
        "/auth/login",
        method="POST",
        body={"email": ERASURE_EMAIL, "password": PASSWORD},
    )
    assert request("/auth/me", token=login["token"])["email"] == ERASURE_EMAIL
    deleted = request(
        "/auth/account",
        method="DELETE",
        token=login["token"],
        body={"password": PASSWORD},
    )
    assert deleted == {"deleted": "workspace", "storage_cleanup": "complete"}
    events = read_events(get_settings())
    assert len(events) == 1
    signature = events[0].signature
    assert isinstance(signature, str)
    verify_erased(signature)
    return signature


def main() -> None:
    """Seed, mutate, or verify state in a deliberately isolated test database."""
    if os.environ.get("SIX_COMMUNITY_REHEARSAL") != "DISPOSABLE":
        raise SystemExit("Refusing fixture outside an explicitly disposable container")
    phase = sys.argv[1]
    if phase == "bootstrap-corpus":
        bootstrap_corpus()
        print("Synthetic legacy corpus built and verified; no research-quality approval")
        return
    if phase == "verify-no-corpus":
        verify_no_corpus()
        print("Fresh candidate ready without a corpus")
        return
    if phase == "database-revision":
        with db_session() as session:
            revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
        assert len(revisions) == 1 and re.fullmatch(r"[a-zA-Z0-9_]+", revisions[0])
        print(revisions[0])
        return
    if phase == "prepare-rollback":
        prepare_erasure(pinboard_supported=False)
        print("Synthetic pre-upgrade rollback fixture prepared")
        return
    if phase == "prepare-erasure-pinboard":
        prepare_erasure_pinboard()
        print("Synthetic candidate-only erasure fixture prepared")
        return
    if phase == "prepare-erasure":
        prepare_erasure()
        print("Synthetic pre-backup erasure fixture prepared")
        return
    if phase == "erase":
        print(json.dumps({"signature": erase_after_backup()}))
        return
    if phase == "verify-erasure":
        verify_erased(sys.argv[2])
        print("Synthetic post-restore erasure verification passed")
        return
    if phase == "seed":
        with db_session() as session:
            if session.scalar(select(User.id).limit(1)) is not None:
                raise SystemExit("Refusing to seed a database that already contains users")
            user = provision_owner(session, EMAIL, PASSWORD, "Synthetic release workspace")
            user.terms_version = CURRENT_TERMS_VERSION
            user.privacy_version = CURRENT_PRIVACY_VERSION
            user.dpa_version = CURRENT_DPA_VERSION
        MARKER.write_text("synthetic-state-42\n", encoding="utf-8")
        return
    login = request("/auth/login", method="POST", body={"email": EMAIL, "password": PASSWORD})
    token = login["token"]
    assert request("/auth/me", token=token)["email"] == EMAIL
    if phase == "verify-rollback":
        assert MARKER.read_text(encoding="utf-8") == "synthetic-state-42\n"
        verify_erased(sys.argv[2], pinboard_supported=False)
        print("Synthetic pre-upgrade snapshot rollback verified")
        return
    current = request("/auth/pinboard", token=token)
    if phase == "upgrade":
        assert MARKER.read_text(encoding="utf-8") == "synthetic-state-42\n"
        assert current == {"revision": 0, "notes": []}
        saved = request(
            "/auth/pinboard", method="PUT", token=token, body={"revision": 0, "notes": [NOTE]}
        )
        assert saved == {"revision": 1, "notes": [NOTE]}
    elif phase == "mutate":
        request(
            "/auth/pinboard",
            method="PUT",
            token=token,
            body={
                "revision": current["revision"],
                "notes": [{**NOTE, "text": "Post-backup mutation"}],
            },
        )
        MARKER.write_text("post-backup-mutation\n", encoding="utf-8")
    elif phase == "verify":
        assert MARKER.read_text(encoding="utf-8") == "synthetic-state-42\n"
        assert current == {"revision": 1, "notes": [NOTE]}
    else:
        raise SystemExit("Unknown rehearsal phase")
    print(f"Synthetic {phase} check passed")


if __name__ == "__main__":
    main()
