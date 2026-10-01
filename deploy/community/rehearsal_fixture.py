"""Synthetic, provider-free checks executed only inside a disposable CI stack."""

import json
import os
import sys
from pathlib import Path
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
# Inert fixture credential, never used by an installed application or public host.
PASSWORD = "Disposable-Community-Fixture-42!"
MARKER = Path("/data/community-rehearsal.txt")
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


def request(path: str, *, token: str = "", body: object = None, method: str = "GET") -> object:
    """Exercise the running API; retain no response or credential in logs."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None if body is None else json.dumps(body).encode()
    with urlopen(
        Request(f"http://127.0.0.1:8000{path}", payload, headers, method=method), timeout=20
    ) as response:
        return json.load(response)


def main() -> None:
    """Seed, mutate, or verify state in a deliberately isolated test database."""
    if os.environ.get("SIX_COMMUNITY_REHEARSAL") != "DISPOSABLE":
        raise SystemExit("Refusing fixture outside an explicitly disposable container")
    phase = sys.argv[1]
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
