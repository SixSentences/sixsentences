"""Local account and API-token routes for self-hosted workspaces."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sixsentences_server.dependencies import (
    Principal,
    get_principal,
    get_session,
    token_record,
)
from sixsentences_server.models import AuthToken, Job, Organization, OutboxEmail, User
from sixsentences_server.schemas import ApiKeyRequest, LoginRequest, RegisterRequest
from sixsentences_server.security import (
    hash_password,
    issue_auth_token,
    normalize_email,
    verify_password,
)
from sixsentences_server.serialization import timestamp

router = APIRouter(tags=["authentication"])


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Create an isolated organization and its first local user."""

    settings = request.app.state.settings
    if not settings.registration_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "registration is disabled")
    try:
        email = normalize_email(payload.email)
        encoded = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "account already exists")
    try:
        organization = Organization(name=payload.organization)
        session.add(organization)
        session.flush()
        user = User(
            org_id=organization.id,
            email=email,
            password_hash=encoded,
            locale=payload.locale,
        )
        session.add(user)
        session.flush()
        _record, raw = issue_auth_token(session, user, hours=settings.session_hours)
        message = OutboxEmail(
            org_id=organization.id,
            recipient=email,
            subject="Your community workspace is ready",
            text_body=(
                "Your local research workspace has been created. This message contains no "
                "login link; sign in through the URL operated by your deployment."
            ),
        )
        session.add(message)
        session.flush()
        session.add(
            Job(
                org_id=organization.id,
                kind="mail.send",
                payload={"message_id": message.public_id},
                max_attempts=settings.worker_max_attempts,
            )
        )
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "account already exists") from exc
    return {
        "token": raw,
        "email": user.email,
        "user_id": user.public_id,
        "org_id": organization.public_id,
    }


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Exchange local credentials for a short-lived opaque token."""

    try:
        email = normalize_email(payload.email)
    except ValueError:
        email = "invalid@example.invalid"
    user = session.scalar(select(User).where(User.email == email))
    if user is None or user.disabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    _record, raw = issue_auth_token(
        session,
        user,
        hours=request.app.state.settings.session_hours,
    )
    organization = session.get(Organization, user.org_id)
    return {
        "token": raw,
        "email": user.email,
        "user_id": user.public_id,
        "org_id": organization.public_id if organization is not None else "",
    }


@router.get("/auth/me")
def me(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Return only identity and local community capability data."""

    organization = session.get(Organization, principal.org_id)
    return {
        "id": principal.user_public_id,
        "email": principal.email,
        "org_id": organization.public_id if organization is not None else "",
        "organization": organization.name if organization is not None else "",
        "capabilities": community_capabilities(),
    }


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    """Revoke the exact session or API token used by the caller."""

    session.delete(token_record(session, principal))


@router.post("/auth/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Create a non-expiring local API token and show it once."""

    user = session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    record, raw = issue_auth_token(
        session,
        user,
        hours=request.app.state.settings.session_hours,
        kind="api",
        name=payload.name,
    )
    return {
        "id": record.public_id,
        "name": record.name,
        "token": raw,
        "created_at": timestamp(record.created_at),
    }


@router.get("/auth/api-keys")
def list_api_keys(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    """List metadata without ever returning stored token digests."""

    records = session.scalars(
        select(AuthToken)
        .where(AuthToken.user_id == principal.user_id, AuthToken.kind == "api")
        .order_by(AuthToken.created_at)
    )
    return [
        {
            "id": row.public_id,
            "name": row.name,
            "created_at": timestamp(row.created_at),
            "last_used_at": timestamp(row.last_used_at),
        }
        for row in records
    ]


@router.delete("/auth/api-keys/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    token_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> None:
    """Revoke only a token owned by the current user."""

    record = session.scalar(
        select(AuthToken).where(
            AuthToken.public_id == token_id,
            AuthToken.user_id == principal.user_id,
            AuthToken.kind == "api",
        )
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource not found")
    session.delete(record)


@router.get("/community/capabilities")
def community_capabilities() -> dict[str, object]:
    """Describe the locally available product surface without account tiers."""

    return {
        "mode": "community",
        "features": [
            "research_pipeline",
            "library",
            "research_data",
            "figures",
            "surveys",
            "text_interviews",
            "voice_interviews",
            "writer",
            "knowledge",
            "brainstorming",
            "smtp_outbox",
        ],
        "limits": "deployment_configured_safety_limits_only",
    }
