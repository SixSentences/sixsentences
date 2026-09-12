"""ASGI, authentication and tenant-isolation integration tests."""

from __future__ import annotations

from conftest import AppContext, bearer, password, register


def test_health_import_auth_and_api_keys(app_context: AppContext) -> None:
    client = app_context.client
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    assert client.get("/health").json() == {"status": "ready"}
    assert "/auth/register" in client.get("/openapi.json").json()["paths"]

    account = register(client, email="first@example.invalid", organization="First Lab")
    headers = bearer(account["token"])
    profile = client.get("/auth/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["organization"] == "First Lab"
    assert profile.json()["capabilities"]["mode"] == "community"

    created = client.post("/auth/api-keys", headers=headers, json={"name": "automation"})
    assert created.status_code == 201
    api_key = created.json()["token"]
    listed = client.get("/auth/api-keys", headers=headers).json()
    assert listed == [
        {
            "id": created.json()["id"],
            "name": "automation",
            "created_at": created.json()["created_at"],
            "last_used_at": None,
        }
    ]
    assert "token" not in listed[0]
    assert client.get("/auth/me", headers=bearer(api_key)).status_code == 200

    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401
    login = client.post(
        "/auth/login",
        json={"email": "first@example.invalid", "password": password()},
    )
    assert login.status_code == 200
    assert client.get("/auth/me", headers=bearer(login.json()["token"])).status_code == 200


def test_every_private_lookup_is_scoped_to_the_organization(app_context: AppContext) -> None:
    client = app_context.client
    first = register(client, email="one@example.invalid", organization="One")
    second = register(client, email="two@example.invalid", organization="Two")
    first_headers = bearer(first["token"])
    second_headers = bearer(second["token"])

    project = client.post(
        "/projects",
        headers=first_headers,
        json={"name": "Private project", "description": "First tenant only"},
    )
    assert project.status_code == 201
    project_id = project.json()["id"]

    assert client.get(f"/projects/{project_id}", headers=first_headers).status_code == 200
    assert client.get(f"/projects/{project_id}", headers=second_headers).status_code == 404
    assert (
        client.patch(
            f"/projects/{project_id}",
            headers=second_headers,
            json={"name": "Cross-tenant write"},
        ).status_code
        == 404
    )
    assert client.delete(f"/projects/{project_id}", headers=second_headers).status_code == 404

    page = client.post(
        "/knowledge/pages",
        headers=first_headers,
        json={"project_id": project_id, "title": "Lab note", "body": "Private"},
    )
    assert page.status_code == 201
    assert (
        client.get(f"/knowledge/pages/{page.json()['id']}", headers=second_headers).status_code
        == 404
    )
    assert client.get("/projects", headers=second_headers).json() == []
