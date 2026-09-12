"""Deployment-neutral safeguards for public research participation."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from sixsentences_server.schemas import ParticipantInformation

PARTICIPANT_SCOPE_VERSION = "2026-09-12.1"


def scope_fingerprint(
    *,
    title: str,
    language: str,
    modalities: list[str],
    guide: dict[str, Any],
    information: dict[str, Any],
    max_session_minutes: int,
    max_turns: int,
    retention_days: int,
    provider_disclosure: str,
) -> str:
    """Bind approval to every field that changes participant processing."""

    encoded = json.dumps(
        {
            "version": PARTICIPANT_SCOPE_VERSION,
            "title": title,
            "language": language,
            "modalities": sorted(set(modalities)),
            "guide": guide,
            "participant_information": information,
            "max_session_minutes": max_session_minutes,
            "max_turns": max_turns,
            "retention_days": retention_days,
            "provider_disclosure": provider_disclosure,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def information_gaps(value: dict[str, Any], modalities: list[str]) -> tuple[str, ...]:
    """Return fail-closed publication gaps without making a legal determination."""

    try:
        information = ParticipantInformation.model_validate(value)
    except ValueError:
        return ("complete participant information",)
    gaps: list[str] = []
    if not information.impact_assessment_completed:
        gaps.append("completed impact assessment")
    if not information.prohibited_use_attested:
        gaps.append("prohibited-use attestation")
    if not information.researcher_reviewed:
        gaps.append("researcher review")
    if "voice" in modalities and not information.spoken_processing_approved:
        gaps.append("spoken-processing approval")
    if information.privacy_notice_url:
        parsed = urlsplit(information.privacy_notice_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            gaps.append("safe HTTPS privacy notice URL")
    return tuple(gaps)


def public_notice(
    information: dict[str, Any],
    *,
    modality: str,
    scope: str,
    provider_disclosure: str,
) -> dict[str, Any]:
    """Create the exact immutable information snapshot accepted by a participant."""

    validated = ParticipantInformation.model_validate(information)
    return {
        "version": PARTICIPANT_SCOPE_VERSION,
        "scope_fingerprint": scope,
        "modality": modality,
        "controller": {
            "name": validated.controller_name,
            "address": validated.controller_address,
            "contact_email": validated.contact_email,
            "data_protection_contact": validated.data_protection_contact,
        },
        "purpose": validated.purpose,
        "data_categories": validated.data_categories,
        "legal_basis": validated.legal_basis,
        "legal_basis_details": validated.legal_basis_details,
        "retention_period": validated.retention_period,
        "recipients": validated.recipients,
        "international_transfers": validated.international_transfers,
        "supervisory_authority": validated.supervisory_authority,
        "privacy_notice_url": validated.privacy_notice_url,
        "provider_disclosure": provider_disclosure if modality == "voice" else "",
        "audio_stored": False,
        "ai_disclosed": True,
        "adult_only": modality == "voice",
        "participant_may_stop": True,
        "human_review_required": True,
    }
