"""Workspace roles, conflict-safe Writer saves and venue retargeting."""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _register(app: FastAPI, email: str, org: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "StrongPass123!",
            "org_name": org,
            "name": email.split("@")[0],
        },
    )
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _login(app: FastAPI, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": "StrongPass123!"})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client
