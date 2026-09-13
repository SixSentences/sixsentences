"""Zotero and Citavi connector persistence, round trips and API surfaces."""

import httpx
import pytest
from fastapi.testclient import TestClient

from sixsentences_server.api.app import create_app
from sixsentences_server.connectors.references import (
    citavi_records,
    connector_records,
    mark_deleted,
    upsert_connector_items,
)
from sixsentences_server.connectors.refimport import parse_endnote_tagged, parse_ris
from sixsentences_server.core.credentials import (
    CredentialError,
    decrypt_credential,
    encrypt_credential,
)
from sixsentences_server.core.db import Org, ReferenceConnectorRow, User, db_session, init_db
from sixsentences_server.reporting.citavi import records_to_endnote, records_to_ris
from sixsentences_server.reporting.zotero import ZoteroClient


def _authed_scholar() -> TestClient:
    client = TestClient(create_app())
    registered = client.post(
        "/auth/register",
        json={
            "name": "Researcher",
            "email": "connector@example.org",
            "password": "ResearchReady42!",
            "org_name": "Connector lab",
        },
    ).json()
    client.headers["Authorization"] = f"Bearer {registered['token']}"
    with db_session() as session:
        user = session.query(User).filter_by(email="connector@example.org").one()
        org = session.get(Org, user.org_id)
        assert org is not None
        org.plan = "community"
    return client


def test_connector_credentials_are_authenticated_and_reversible(settings) -> None:
    encrypted = encrypt_credential("zotero-secret")
    assert encrypted != "zotero-secret"
    assert decrypt_credential(encrypted) == "zotero-secret"
    legacy_fernet_token = "gAAAAABqdKJMLbmAE3vcTOQz4PdVdW_IqlgFsetuwJsePpKu6buoc_bPD96Oy0ofTOWjLuo2ocp2YnI6aaPaWSC50hBAkTm8Sg=="
    assert decrypt_credential(legacy_fernet_token) == "zotero-secret"
    tampered = f"{encrypted[:-2]}AA"
    with pytest.raises(CredentialError, match="cannot be decrypted"):
        decrypt_credential(tampered)


def test_citavi_formats_preserve_notes_keywords_and_attachments() -> None:
    source = "TY  - JOUR\nTI  - Evidence synthesis\nAU  - Ada Researcher\nPY  - 2026\nKW  - screening\nN1  - Read methods closely\nL1  - file:///paper.pdf\nER  - \n"
    record = parse_ris(source)[0]
    assert record["keywords"] == ["screening"]
    assert record["notes"] == ["Read methods closely"]
    assert record["attachments"] == ["file:///paper.pdf"]
    ris = records_to_ris([record])
    enw = records_to_endnote([record])
    assert "KW  - screening" in ris and "N1  - Read methods closely" in ris
    reparsed = parse_endnote_tagged(enw)[0]
    assert reparsed["keywords"] == ["screening"]
    assert reparsed["notes"] == ["Read methods closely"]


def test_connector_items_sync_versions_and_deletions(settings) -> None:
    init_db()
    with db_session() as session:
        org = Org(name="sync", plan="community")
        session.add(org)
        session.flush()
        user = User(org_id=org.id, email="sync@example.org", password_hash="unused", role="owner")
        session.add(user)
        session.flush()
        connector = ReferenceConnectorRow(
            org_id=org.id,
            user_id=user.id,
            provider="citavi",
            name="Thesis",
            library_type="project",
            library_id="project-1",
        )
        session.add(connector)
        session.flush()
        records = citavi_records(parse_ris("TY  - JOUR\nTI  - First title\nER  - \n"))
        assert upsert_connector_items(session, connector, records) == (1, 0)
        key = records[0][0]
        changed = [(key, 2, {**records[0][2], "title": "Updated title"})]
        assert upsert_connector_items(session, connector, changed) == (0, 1)
        assert connector_records(session, connector.id)[0]["title"] == "Updated title"
        assert mark_deleted(session, connector.id, [key]) == 1
        assert connector_records(session, connector.id) == []


def test_citavi_connector_api_imports_and_exports(settings) -> None:
    client = _authed_scholar()
    connector = client.post("/reference-connectors/citavi", json={"name": "Dissertation"}).json()
    synced = client.post(
        f"/reference-connectors/{connector['id']}/import",
        json={
            "filename": "citavi.ris",
            "text": "TY  - JOUR\nTI  - Imported paper\nKW  - useful\nER  - \n",
        },
    )
    assert synced.status_code == 200
    assert synced.json()["item_count"] == 1
    items = client.get(f"/reference-connectors/{connector['id']}/items").json()
    assert items[0]["title"] == "Imported paper" and items[0]["keywords"] == ["useful"]
    export = client.get(f"/reference-connectors/{connector['id']}/export?format=endnote")
    assert export.status_code == 200 and "%T Imported paper" in export.text
    imports = client.get("/imports").json()
    assert imports[0]["count"] == 1 and "Dissertation" in imports[0]["label"]


def test_zotero_client_reads_incremental_versions_and_deletions() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/deleted"):
            return httpx.Response(
                200, headers={"Last-Modified-Version": "12"}, json={"items": ["OLD"]}
            )
        return httpx.Response(
            200,
            headers={"Last-Modified-Version": "11"},
            json=[
                {
                    "key": "A1",
                    "version": 11,
                    "data": {"itemType": "journalArticle", "title": "A paper"},
                }
            ],
        )

    client = ZoteroClient(
        "KEY", "user", "123", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    page = client.read_items(since=10)
    deleted, version = client.deleted_since(10)
    assert page.library_version == 11 and page.items[0]["key"] == "A1"
    assert deleted == ["OLD"] and version == 12
    assert all(request.url.params.get("since") == "10" for request in seen)
