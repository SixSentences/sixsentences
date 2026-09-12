"""Secure first-owner bootstrap behavior."""

from typer.testing import CliRunner

from sixsentences_server.cli import app
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import authenticate
from sixsentences_server.core.db import User, db_session


def test_create_owner_has_no_password_option() -> None:
    result = CliRunner().invoke(app, ["auth", "create-owner", "--help"])

    assert result.exit_code == 0
    assert "--password" not in result.output


def test_create_owner_prompts_secret_and_creates_only_tenant_owner(
    settings: Settings,
) -> None:
    password = "CommunityOwnerPassword-42!"
    result = CliRunner().invoke(
        app,
        [
            "auth",
            "create-owner",
            "--email",
            "owner@example.org",
            "--org",
            "Example Lab",
            "--first-name",
            "Ada",
        ],
        input=f"{password}\n{password}\n",
    )

    assert result.exit_code == 0, result.output
    lowered = result.output.casefold()
    assert password not in result.output
    assert "token" not in lowered
    assert "api key" not in lowered
    with db_session() as session:
        users = session.query(User).all()
        assert len(users) == 1
        owner = users[0]
        assert owner.email == "owner@example.org"
        assert owner.role == "owner"
        assert owner.email_verified_at is not None
        assert authenticate(session, owner.email, password)
