"""Answer whether a deployment can do the work it is configured for.

``deploy/community/preflight.sh`` proves a configuration is internally
consistent *before* the stack starts. Nothing answered the question afterwards:
is the database there and migrated, can the service write where it keeps
documents, and can it reach the mail host it was given. An operator found each
of those out through a failed user action.

Two rules shape the output. A feature that is switched off is reported as off
rather than as broken — an empty provider key is a deployment decision, not a
fault. And no configured secret, key fragment or credential is printed, so the
result can be pasted into an issue.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import inspect, text

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.core.db import Base, db_session

CheckState = Literal["ok", "off", "failed"]
_PROBE_NAME = ".sixsentences-doctor-probe"
_MAIL_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Check:
    """One answerable question about this deployment."""

    name: str
    state: CheckState
    detail: str


def _database_check() -> Check:
    """Reachable, and carrying the tables this build expects."""

    try:
        with db_session() as session:
            session.execute(text("SELECT 1"))
            present = set(inspect(session.get_bind()).get_table_names())
    except Exception as error:  # noqa: BLE001 - the reason is the answer here
        return Check("database", "failed", f"not reachable: {type(error).__name__}")
    expected = set(Base.metadata.tables)
    missing = sorted(expected - present)
    if missing:
        return Check(
            "database",
            "failed",
            f"{len(missing)} of {len(expected)} tables are missing, starting with"
            f" {missing[0]!r}; run the migrations",
        )
    return Check("database", "ok", f"reachable with all {len(expected)} tables")


def _storage_check(data_dir: Path) -> Check:
    """Writable, because every upload and figure lands here."""

    probe = data_dir / _PROBE_NAME
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"doctor")
        probe.unlink()
    except OSError as error:
        return Check("storage", "failed", f"not writable: {error.strerror or 'unknown error'}")
    return Check("storage", "ok", "writable")


def _mail_check(settings: Settings) -> Check:
    """Reachable when configured; registration depends on it."""

    if not settings.smtp_host.strip():
        return Check(
            "mail",
            "off",
            "no SMTP host configured; public registration and verification stay unavailable",
        )
    try:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=_MAIL_TIMEOUT_SECONDS
        ) as client:
            client.ehlo()
            if settings.smtp_starttls:
                client.starttls()
                client.ehlo()
    except (OSError, smtplib.SMTPException) as error:
        # The host is part of the configuration the operator wrote; the
        # credentials are not, and are never sent by this check.
        return Check(
            "mail",
            "failed",
            f"{settings.smtp_host}:{settings.smtp_port} did not accept a session:"
            f" {type(error).__name__}",
        )
    return Check("mail", "ok", f"{settings.smtp_host}:{settings.smtp_port} accepted a session")


def _provider_checks(settings: Settings) -> list[Check]:
    """Report what each optional integration would do, never its credential."""

    if not settings.pubmed_enabled:
        pubmed = Check("PubMed", "off", "disabled; no scholarly query is sent to NCBI")
    elif settings.pubmed_ready:
        pubmed = Check(
            "PubMed",
            "ok",
            "enabled; translated scholarly queries are sent to NCBI",
        )
    else:
        pubmed = Check(
            "PubMed",
            "failed",
            "enabled but the NCBI contact email or optional API key is invalid",
        )

    return [
        Check(
            "metadata",
            "ok" if settings.openalex_mailto.strip() else "off",
            "scholarly metadata identifies this deployment by contact address"
            if settings.openalex_mailto.strip()
            else "no contact address; scholarly metadata calls stay unidentified",
        ),
        Check(
            "web search",
            "ok" if settings.websearch_enabled else "off",
            "enabled"
            if settings.websearch_enabled
            else "needs a provider key and the data-processing confirmation",
        ),
        pubmed,
        Check(
            "speech",
            "ok" if settings.gemini_enabled else "off",
            "enabled"
            if settings.gemini_enabled
            else "needs a provider key and the data-processing confirmation",
        ),
        Check(
            "public spoken interviews",
            "ok" if settings.public_gemini_live_enabled else "off",
            "enabled"
            if settings.public_gemini_live_enabled
            else "needs speech processing and the explicit release gate",
        ),
        Check(
            "public registration",
            "ok" if settings.self_signup else "off",
            "open"
            if settings.self_signup
            else "closed; create accounts with 'six-community auth create-owner'",
        ),
    ]


def run_doctor(settings: Settings | None = None) -> list[Check]:
    """Return one check per answerable question, in the order an operator reads."""

    active = settings or get_settings()
    return [
        _database_check(),
        _storage_check(active.data_dir),
        _mail_check(active),
        *_provider_checks(active),
    ]


def failed(checks: list[Check]) -> list[Check]:
    """Return only the checks that name a problem to fix."""

    return [check for check in checks if check.state == "failed"]
