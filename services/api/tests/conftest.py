"""Shared isolated application fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from sixsentences_server.app import create_app
from sixsentences_server.config import Settings
from sixsentences_server.database import Database, create_database


@dataclass(slots=True)
class FakeTextProvider:
    calls: list[list[dict[str, str]]] = field(default_factory=list)

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        assert "do not diagnose" in system
        self.calls.append(messages)
        return "What would you like the research team to understand next?"


@dataclass(slots=True)
class FakeSpeechProvider:
    received: list[tuple[bytes, str]] = field(default_factory=list)

    def transcribe(self, *, content: bytes, media_type: str) -> str:
        self.received.append((content, media_type))
        return "The participant's spoken answer."


@dataclass(slots=True)
class AppContext:
    settings: Settings
    database: Database
    client: TestClient
    text: FakeTextProvider
    speech: FakeSpeechProvider


@pytest.fixture
def app_context(tmp_path: object) -> Iterator[AppContext]:
    database_path = str(tmp_path) + "/community-test.sqlite3"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database_path}",
        auto_create_schema=True,
        allowed_hosts=["testserver"],
        voice_provider="openai_compatible",
        voice_base_url="https://speech.example.invalid/v1",
        voice_model="test-transcriber",
    )
    database = create_database(settings)
    text = FakeTextProvider()
    speech = FakeSpeechProvider()
    app = create_app(settings, database=database, ai=text, speech=speech)
    with TestClient(app) as client:
        yield AppContext(settings, database, client, text, speech)
    database.dispose()


def password() -> str:
    return "community-passphrase-42"


def register(client: TestClient, *, email: str, organization: str) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password(),
            "organization": organization,
            "locale": "en",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
