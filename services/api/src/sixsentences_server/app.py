"""ASGI application for the self-hosted community API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text

from sixsentences_server.auth_routes import router as auth_router
from sixsentences_server.config import Settings, get_settings
from sixsentences_server.database import Database, create_database
from sixsentences_server.models import Base
from sixsentences_server.participation_routes import router as participation_router
from sixsentences_server.providers import (
    SpeechProvider,
    TextProvider,
    speech_provider,
    text_provider,
)
from sixsentences_server.resource_routes import router as resource_router


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    ai: TextProvider | None = None,
    speech: SpeechProvider | None = None,
) -> FastAPI:
    """Build an application whose infrastructure can be replaced in tests."""

    resolved_settings = settings or get_settings()
    resolved_database = database or create_database(resolved_settings)
    owns_database = database is None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        if resolved_settings.sqlite_path is not None:
            resolved_settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        if resolved_settings.auto_create_schema:
            Base.metadata.create_all(resolved_database.engine)
        try:
            yield
        finally:
            if owns_database:
                resolved_database.dispose()

    application = FastAPI(
        title="SixSentences Community API",
        version="0.1.0-alpha.1",
        description=(
            "A self-hosted API for auditable research workflows. Optional external AI and "
            "speech processing is disabled until the deployment operator configures it."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = resolved_database
    application.state.text_provider = ai or text_provider(resolved_settings)
    application.state.speech_provider = speech or speech_provider(resolved_settings)

    if resolved_settings.allowed_hosts:
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=resolved_settings.allowed_hosts,
        )
    if resolved_settings.allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # The community web client already supplies its deployment API base URL and
    # calls these paths directly; an extra version prefix would break that contract.
    application.include_router(auth_router)
    application.include_router(resource_router)
    application.include_router(participation_router)

    @application.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"])
    def ready(request: Request) -> dict[str, str]:
        try:
            with request.app.state.database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"database unavailable: {type(exc).__name__}",
            ) from exc
        return {"status": "ready"}

    @application.get("/health", tags=["health"])
    def health(request: Request) -> dict[str, str]:
        return ready(request)

    return application


app = create_app()
