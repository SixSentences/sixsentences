"""Operator-owned account notices for a self-hosted community deployment.

The project does not ship SixSentences hosted terms, controller identities or
data-processing agreements. Account notice enforcement is disabled by default.
An operator that enables it must publish and version its own documents.
Participant information and study consent are handled separately by the study
participation subsystem and remain mandatory for voice-derived processing.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import object_session

from sixsentences_server.config import get_settings
from sixsentences_server.core.db import User, UserLegalEventRow

CURRENT_TERMS_VERSION = "community-operator-v1"
CURRENT_PRIVACY_VERSION = "community-operator-v1"
CURRENT_DPA_VERSION = "community-operator-v1"
CURRENT_MARKETING_CONSENT_VERSION = "disabled"


class LegalAcceptanceError(ValueError):
    """Raised when an enabled operator notice gate is incomplete."""


def validate_signup_acceptance(
    *,
    age_requirement_confirmed: bool,
    terms_accepted: bool,
    terms_version: str,
    privacy_acknowledged: bool,
    privacy_version: str,
    dpa_accepted: bool,
    dpa_version: str,
) -> None:
    """Validate the generic operator-owned account notice contract."""
    del privacy_acknowledged
    if not age_requirement_confirmed:
        raise LegalAcceptanceError("Confirm the deployment's minimum account age.")
    if not terms_accepted or terms_version != CURRENT_TERMS_VERSION:
        raise LegalAcceptanceError("Accept the current operator terms.")
    if privacy_version != CURRENT_PRIVACY_VERSION:
        raise LegalAcceptanceError("Open the current operator privacy notice.")
    if dpa_accepted and dpa_version != CURRENT_DPA_VERSION:
        raise LegalAcceptanceError("Use the current operator agreement version.")


def legal_reaccept_required(user: User) -> bool:
    """Return whether the enabled generic account-notice gate needs action."""
    if not get_settings().enforce_legal_acceptance:
        return False
    required = legal_requirements(user)
    return required["age"] or required["terms"] or required["dpa"]


def latest_confirmed_controller_name(user: User) -> str | None:
    """Return the latest explicit operator-agreement controller declaration."""
    session = object_session(user)
    if session is None or user.role != "owner":
        return None
    name = session.scalar(
        select(UserLegalEventRow.controller_name)
        .where(
            UserLegalEventRow.org_id == user.org_id,
            UserLegalEventRow.document_id == "operator_agreement",
            UserLegalEventRow.event_kind == "contract_accepted",
            UserLegalEventRow.actor_role == "owner",
        )
        .order_by(UserLegalEventRow.created_at.desc(), UserLegalEventRow.id.desc())
        .limit(1)
    )
    confirmed = (name or "").strip()
    return confirmed if len(confirmed) >= 2 else None


def legal_requirements(user: User) -> dict[str, bool]:
    """Resolve generic account declarations without asserting hosted roles."""
    if not get_settings().enforce_legal_acceptance:
        return {
            "age": False,
            "terms": False,
            "dpa": False,
            "dpa_can_accept": user.role == "owner",
            "privacy": False,
        }
    session = object_session(user)
    agreement = None
    if session is not None:
        agreement = session.scalar(
            select(UserLegalEventRow.id)
            .where(
                UserLegalEventRow.org_id == user.org_id,
                UserLegalEventRow.document_id == "operator_agreement",
                UserLegalEventRow.version == CURRENT_DPA_VERSION,
                UserLegalEventRow.event_kind == "contract_accepted",
                UserLegalEventRow.actor_role == "owner",
            )
            .limit(1)
        )
    return {
        "age": user.age_requirement_confirmed_at is None,
        "terms": user.terms_version != CURRENT_TERMS_VERSION,
        "dpa": agreement is None,
        "dpa_can_accept": user.role == "owner",
        "privacy": user.privacy_version != CURRENT_PRIVACY_VERSION,
    }


def append_legal_event(
    user: User,
    *,
    document_id: str,
    version: str,
    event_kind: str,
    declaration: str,
    document_content: str = "",
    controller_name: str = "",
    timestamp: datetime | None = None,
) -> None:
    """Store a minimal account-notice event in the active transaction."""
    session = object_session(user)
    if session is None:
        raise LegalAcceptanceError("Account declarations require an active transaction.")
    session.add(
        UserLegalEventRow(
            user_id=user.id,
            org_id=user.org_id,
            document_id=document_id,
            version=version,
            event_kind=event_kind,
            language=user.language or "en",
            declaration=declaration,
            document_content=document_content,
            content_sha256=hashlib.sha256(document_content.encode("utf-8")).hexdigest(),
            controller_name=controller_name,
            actor_role=user.role,
            created_at=timestamp or datetime.now(UTC),
        )
    )


def record_contract_acceptance(
    user: User,
    kind: str,
    *,
    controller_name: str = "",
    timestamp: datetime | None = None,
) -> None:
    """Record acceptance without bundling any hosted legal document."""
    if kind not in {"terms", "dpa"}:
        raise LegalAcceptanceError(f"Unsupported operator document: {kind}")
    version = CURRENT_TERMS_VERSION if kind == "terms" else CURRENT_DPA_VERSION
    document_id = "terms" if kind == "terms" else "operator_agreement"
    append_legal_event(
        user,
        document_id=document_id,
        version=version,
        event_kind="contract_accepted",
        declaration=f"Accepted operator-provided {document_id} version {version}.",
        controller_name=controller_name,
        timestamp=timestamp,
    )


def record_privacy_notice(user: User, *, timestamp: datetime | None = None) -> None:
    """Record presentation of the operator's notice without claiming consent."""
    timestamp = timestamp or datetime.now(UTC)
    if user.privacy_version == CURRENT_PRIVACY_VERSION:
        return
    notice_url = f"{get_settings().site_url.rstrip('/')}/privacy"
    append_legal_event(
        user,
        document_id="privacy",
        version=CURRENT_PRIVACY_VERSION,
        event_kind="notice_presented",
        declaration="Operator privacy information made available; this is not consent.",
        document_content=notice_url,
        timestamp=timestamp,
    )
    user.privacy_version = CURRENT_PRIVACY_VERSION
    user.privacy_acknowledged_at = timestamp


def record_age_confirmation(user: User, *, timestamp: datetime | None = None) -> None:
    """Record the operator-configured minimum-age confirmation once."""
    if user.age_requirement_confirmed_at is not None:
        return
    timestamp = timestamp or datetime.now(UTC)
    append_legal_event(
        user,
        document_id="minimum_age",
        version="operator-age-v1",
        event_kind="age_confirmed",
        declaration="I confirm that I meet this deployment's minimum account age.",
        timestamp=timestamp,
    )
    user.age_requirement_confirmed_at = timestamp


def record_signup_acceptance(
    user: User,
    *,
    terms_version: str,
    privacy_version: str,
    dpa_version: str,
    marketing_consent: bool,
    accepted_at: datetime | None = None,
) -> None:
    """Persist enabled account notices; ignore hosted-marketing compatibility input."""
    del dpa_version, marketing_consent
    timestamp = accepted_at or datetime.now(UTC)
    record_age_confirmation(user, timestamp=timestamp)
    record_contract_acceptance(user, "terms", timestamp=timestamp)
    record_privacy_notice(user, timestamp=timestamp)
    user.terms_version = terms_version
    user.terms_accepted_at = timestamp
    user.privacy_version = privacy_version
