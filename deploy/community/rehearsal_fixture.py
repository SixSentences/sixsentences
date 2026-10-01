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
from sqlalchemy import select

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


def prepare_erasure() -> None:
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
            org_id=user.org_id, name="Synthetic erasure dataset", filename=ERASURE_FILENAME,
            format="csv", row_count=1, byte_size=len(ERASURE_BYTES),
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
    login = request(
        "/auth/login", method="POST", body={"email": ERASURE_EMAIL, "password": PASSWORD},
    )
    note = {**NOTE, "text": "Synthetic note that must remain erased"}
    saved = request(
        "/auth/pinboard", method="PUT", token=login["token"],
        body={"revision": 0, "notes": [note]},
    )
    assert saved == {"revision": 1, "notes": [note]}
    assert all(path.read_bytes() == ERASURE_BYTES for path in erasure_files(state))


def verify_erased(signature: str) -> None:
    """Require journal continuity, absent owned state and denied authentication."""
    from sixsentences_server.config import get_settings
    from sixsentences_server.core.db import AuthToken, Org, PersonalPinboardRow, ResearchDatasetRow
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
        assert session.get(PersonalPinboardRow, state["user_id"]) is None
        assert session.get(ResearchDatasetRow, state["dataset_id"]) is None
        assert session.scalar(
            select(AuthToken.id).where(AuthToken.org_id == state["org_id"])
        ) is None
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
        "/auth/login", method="POST", body={"email": ERASURE_EMAIL, "password": PASSWORD},
    )
    assert request("/auth/me", token=login["token"])["email"] == ERASURE_EMAIL
    deleted = request(
        "/auth/account", method="DELETE", token=login["token"], body={"password": PASSWORD},
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
