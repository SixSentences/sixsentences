"""Figure attachment enforces manuscript roles without HTTP or provider calls."""

from collections.abc import Callable
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import select

from sixsentences_server.api.app import FigureAttach, create_app
from sixsentences_server.config import Settings
from sixsentences_server.core.auth import AuthContext
from sixsentences_server.core.db import (
    FigureRow,
    Org,
    User,
    WriterAssetRow,
    WriterCollaboratorRow,
    WriterDocumentRow,
    db_session,
)


@pytest.mark.parametrize("document_ref", ["public", "numeric"])
@pytest.mark.parametrize(
    ("role", "expected_status"),
    [("owner", None), ("editor", None), ("reviewer", 403), ("viewer", 403), ("none", 404)],
)
def test_figure_attach_requires_editor_without_changing_denied_documents(
    settings: Settings, role: str, expected_status: int | None, document_ref: str
) -> None:
    """Owners/editors attach; read-only and unshared members leave no asset or receipt."""
    _exercise_attach(settings, role, expected_status, document_ref=document_ref)


@pytest.mark.parametrize("foreign_resource", ["document", "figure"])
def test_figure_attach_keeps_cross_tenant_resources_hidden(
    settings: Settings, foreign_resource: str
) -> None:
    """An editor cannot attach a foreign figure or write to a foreign manuscript."""
    _exercise_attach(settings, "editor", 404, foreign_resource=foreign_resource)


def _exercise_attach(
    settings: Settings,
    role: str,
    expected_status: int | None,
    *,
    document_ref: str = "public",
    foreign_resource: str = "",
) -> None:
    app = create_app()
    endpoint = cast(
        Callable[..., dict[str, Any]],
        next(
            route.endpoint
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path == "/figures/{figure_id}/attach"
            and "POST" in (route.methods or set())
        ),
    )
    with db_session() as session:
        session.add_all([Org(id=1, name="Synthetic lab"), Org(id=2, name="Other synthetic lab")])
        session.flush()
        session.add_all(
            [
                User(
                    id=1,
                    org_id=1,
                    email="owner@example.invalid",
                    password_hash="not-a-password",
                    role="owner",
                ),
                User(
                    id=2, org_id=1, email="member@example.invalid", password_hash="not-a-password"
                ),
            ]
        )
        session.flush()
        document = WriterDocumentRow(
            id=42,
            public_id="testdocument",
            org_id=2 if foreign_resource == "document" else 1,
            created_by=1,
            title="Synthetic manuscript",
            content="Unchanged synthetic source.",
        )
        figure = FigureRow(
            id=42,
            public_id="testfigure",
            org_id=2 if foreign_resource == "figure" else 1,
            prompt="Synthetic figure",
            status="ok",
            config={"format_version": 2},
        )
        session.add_all([document, figure])
        session.flush()
        if role not in {"owner", "none"}:
            session.add(
                WriterCollaboratorRow(
                    org_id=document.org_id,
                    document_id=document.id,
                    user_id=2,
                    role=role,
                )
            )
    figure_dir = settings.data_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    figure_bytes = b"synthetic-figure-fixture"
    (figure_dir / "42.png").write_bytes(figure_bytes)
    ctx = AuthContext(
        user_id=1 if role == "owner" else 2,
        org_id=1,
        token_id=0,
        role="owner" if role == "owner" else "member",
        email="actor@example.invalid",
    )
    body = FigureAttach(document_id="42" if document_ref == "numeric" else "testdocument")
    if expected_status is not None:
        with pytest.raises(HTTPException) as denied:
            endpoint(figure_id="testfigure", body=body, ctx=ctx)
        assert denied.value.status_code == expected_status
    else:
        result = endpoint(figure_id="testfigure", body=body, ctx=ctx)
        assert result["document_public_id"] == "testdocument"
        assert result["filename"] == "synthetic-figure.png"

    with db_session() as session:
        assets = list(session.scalars(select(WriterAssetRow)).all())
        document = session.get(WriterDocumentRow, 42)
        figure = session.get(FigureRow, 42)
        assert document is not None and figure is not None
        assert document.content == "Unchanged synthetic source."
        assert document.revision == 1
        if expected_status is not None:
            assert assets == []
            assert "writer_attachments" not in figure.config
            assert not (settings.data_dir / "writer" / "42" / "assets").exists()
        else:
            assert [asset.filename for asset in assets] == ["synthetic-figure.png"]
            assert figure.config["writer_attachments"] == {"testdocument": "synthetic-figure.png"}
            assert (
                settings.data_dir / "writer" / "42" / "assets" / "synthetic-figure.png"
            ).read_bytes() == figure_bytes
