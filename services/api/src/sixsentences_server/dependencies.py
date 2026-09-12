"""FastAPI request dependencies and tenant-safe lookup helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from sixsentences_server.models import AuthToken, TenantRecord, User
from sixsentences_server.security import authenticate_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated local identity and its tenant boundary."""

    user_id: int
    user_public_id: str
    org_id: int
    email: str
    token_id: int


def get_session(request: Request) -> Iterator[Session]:
    """Provide one transaction per request."""

    with request.app.state.database.session() as session:
        yield session


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> Principal:
    """Authenticate a bearer token without revealing lookup details."""

    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    authenticated = authenticate_token(session, credentials.credentials)
    if authenticated is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    user, token = authenticated
    return Principal(
        user_id=user.id,
        user_public_id=user.public_id,
        org_id=user.org_id,
        email=user.email,
        token_id=token.id,
    )


def tenant_query[T: TenantRecord](model: type[T], principal: Principal) -> Select[tuple[T]]:
    """Start a query that cannot cross the authenticated organization."""

    return select(model).where(model.org_id == principal.org_id)


def tenant_record[T: TenantRecord](
    session: Session,
    model: type[T],
    record_id: str,
    principal: Principal,
) -> T:
    """Resolve by public ID inside the tenant or return the same 404 as absence."""

    record = session.scalar(
        tenant_query(model, principal).where(model.public_id == record_id)
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource not found")
    return record


def user_record(session: Session, principal: Principal) -> User:
    """Load the current user after token authentication."""

    user = session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return user


def token_record(session: Session, principal: Principal) -> AuthToken:
    """Load the exact token used for the current request."""

    token = session.get(AuthToken, principal.token_id)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return token
