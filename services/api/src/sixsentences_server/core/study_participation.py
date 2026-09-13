"""Version-bound participant information, independent of account contracts.

Research controllers supply study facts; product disclosures are server owned.
Presence validation is not a legal assessment of the controller's chosen basis.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PARTICIPANT_INFORMATION_VERSION = "2026-09-12.1"
# Bump this whenever the AI-interviewer prompt/policy, relay/provider boundary,
# automatic analysis boundary, or other DPIA-relevant platform behavior changes.
# It deliberately invalidates controller approval for every previously assessed
# voice-study scope until the study owner reviews and reapproves it.
VOICE_DPIA_SCOPE_VERSION = "2026-09-12.1"


class ParticipantInformation(BaseModel):
    """Editable draft facts; an explicit researcher review is required to publish."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    language: Literal["de", "en"] = "en"
    controller_name: str = Field(default="", max_length=240)
    controller_address: str = Field(default="", max_length=500)
    contact_email: str = Field(default="", max_length=320)
    data_protection_contact: str = Field(default="", max_length=500)
    purpose: str = Field(default="", max_length=2000)
    data_categories: str = Field(default="", max_length=1500)
    legal_basis: Literal["", "consent", "public_task", "legitimate_interests"] = ""
    legal_basis_details: str = Field(default="", max_length=1500)
    retention_period: str = Field(default="", max_length=1500)
    additional_recipients: str = Field(default="", max_length=1500)
    additional_transfers: str = Field(default="", max_length=1500)
    supervisory_authority: str = Field(default="", max_length=1000)
    privacy_notice_url: str = Field(default="", max_length=2000)
    dpia_status: Literal["", "completed"] = ""
    dpia_reference: str = Field(default="", max_length=1500)
    ai_interview_scope_attested: bool = False
    spoken_processing_approved: bool = False
    dpia_scope_fingerprint: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$", max_length=64)
    dpia_public_scope_token: str = Field(default="", pattern=r"^(?:|[0-9a-f]{32})$", max_length=32)
    researcher_reviewed: bool = False


_VOICE_STUDY_APPROVAL_FIELDS = frozenset(
    {
        "dpia_status",
        "dpia_reference",
        "ai_interview_scope_attested",
        "spoken_processing_approved",
        "dpia_scope_fingerprint",
        "dpia_public_scope_token",
        "researcher_reviewed",
    }
)


def voice_study_assessed_scope(row: Any) -> tuple[Any, ...]:
    """Return every concrete study fact covered by controller approval."""

    participant_facts = {
        key: value
        for key, value in dict(row.participant_information or {}).items()
        if key not in _VOICE_STUDY_APPROVAL_FIELDS
    }
    return (
        PARTICIPANT_INFORMATION_VERSION,
        VOICE_DPIA_SCOPE_VERSION,
        row.project_id,
        row.title,
        row.language,
        row.voice,
        row.tone,
        row.mode,
        row.patience_ms,
        row.max_session_minutes,
        row.retention,
        row.consent_text,
        row.contact_line,
        row.budget_minutes,
        json.dumps(dict(row.guide or {}), ensure_ascii=False, sort_keys=True),
        json.dumps(participant_facts, ensure_ascii=False, sort_keys=True),
    )


def voice_study_scope_fingerprint(row: Any) -> str:
    """Hash the versioned study scope without exposing its private guide."""

    encoded = json.dumps(
        voice_study_assessed_scope(row),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def invalidate_voice_study_approval(row: Any) -> None:
    """Clear every approval claim after the assessed scope changes."""

    information = dict(row.participant_information or {})
    information["dpia_status"] = ""
    information["dpia_reference"] = ""
    information["ai_interview_scope_attested"] = False
    information["spoken_processing_approved"] = False
    information["dpia_scope_fingerprint"] = ""
    information["dpia_public_scope_token"] = ""
    information["researcher_reviewed"] = False
    row.participant_information = information


def participant_information_gaps(value: Any) -> tuple[str, ...]:
    """Check required facts without inventing a legal basis for a research team."""
    try:
        info = ParticipantInformation.model_validate(value or {})
    except ValidationError:
        return ("Review the participant information fields.",)
    required = (
        "controller_name",
        "controller_address",
        "purpose",
        "data_categories",
        "legal_basis",
        "legal_basis_details",
        "retention_period",
        "additional_recipients",
        "additional_transfers",
        "supervisory_authority",
    )
    gaps = [name.replace("_", " ") for name in required if len(getattr(info, name)) < 3]
    if not _is_reachable_email(info.contact_email):
        gaps.append("reachable study contact email")
    if info.privacy_notice_url:
        try:
            parsed = urlsplit(info.privacy_notice_url)
            safe_url = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            safe_url = False
        if not safe_url:
            gaps.append("HTTPS privacy notice URL")
    if not info.researcher_reviewed:
        gaps.append("researcher review of the legal basis and participant information")
    return tuple(gaps)


def _is_reachable_email(value: str) -> bool:
    """Apply a bounded, linear syntax check without attempting mailbox validation."""

    if not 3 <= len(value) <= 320 or any(character.isspace() for character in value):
        return False
    local, separator, domain = value.rpartition("@")
    if separator != "@" or not local or len(local) > 64 or "@" in local:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    labels = domain.split(".")
    if len(labels) < 2 or any(not label or len(label) > 63 for label in labels):
        return False
    return all(
        not label.startswith("-")
        and not label.endswith("-")
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def voice_participant_information_gaps(
    value: Any, *, expected_scope_fingerprint: str
) -> tuple[str, ...]:
    """Require a controller-owned DPIA decision before publishing an AI interview."""
    gaps = list(participant_information_gaps(value))
    try:
        info = ParticipantInformation.model_validate(value or {})
    except ValidationError:
        return tuple(gaps)
    if info.dpia_status != "completed":
        gaps.append("completed data protection impact assessment")
    if len(info.dpia_reference.strip()) < 20:
        gaps.append("DPIA decision reference and reasoning")
    if not info.ai_interview_scope_attested:
        gaps.append("AI interview prohibited-use attestation")
    if (
        not expected_scope_fingerprint
        or info.dpia_scope_fingerprint != expected_scope_fingerprint
        or not info.dpia_public_scope_token
    ):
        gaps.append("DPIA approval bound to the current study scope")
    return tuple(gaps)


def voice_study_participation_ready(row: Any) -> bool:
    """Return whether the current exact study scope has valid approval."""

    return not voice_participant_information_gaps(
        row.participant_information,
        expected_scope_fingerprint=voice_study_scope_fingerprint(row),
    )


def voice_study_spoken_processing_ready(row: Any) -> bool:
    """Require exact-scope approval that explicitly covers spoken processing."""

    try:
        information = ParticipantInformation.model_validate(row.participant_information or {})
    except ValidationError:
        return False
    return bool(information.spoken_processing_approved and voice_study_participation_ready(row))


def voice_session_scope_revision(consent_snapshot: Any) -> str:
    """Return the opaque study revision captured by a voice session."""

    if not isinstance(consent_snapshot, dict):
        return ""
    surface = consent_snapshot.get("surface")
    if isinstance(surface, dict):
        return str(surface.get("study_scope_revision") or "")
    return str(consent_snapshot.get("study_scope_revision") or "")


def voice_session_scope_matches(row: Any, consent_snapshot: Any) -> bool:
    """Bind continued provider processing to the session's approved scope."""

    session_revision = voice_session_scope_revision(consent_snapshot)
    current_revision = str((row.participant_information or {}).get("dpia_public_scope_token") or "")
    return bool(session_revision and session_revision == current_revision)


def participant_notice(
    value: dict[str, Any],
    *,
    mode: Literal["survey", "text", "live"],
    language: str | None = None,
    recording: bool = False,
) -> dict[str, Any]:
    """Render all mandatory product facts and study facts into the shown snapshot."""
    info = ParticipantInformation.model_validate(value or {})
    de = (language or info.language) == "de"

    def t(german: str, english: str) -> str:
        return german if de else english

    consent = info.legal_basis == "consent"
    basis = {
        "consent": "Art. 6(1)(a) GDPR",
        "public_task": "Art. 6(1)(e) GDPR",
        "legitimate_interests": "Art. 6(1)(f) GDPR",
        "": "",
    }[info.legal_basis]
    declaration = (
        t(
            "Ich willige freiwillig in die beschriebene Verarbeitung meiner Angaben für diese "
            "Studie ein.",
            "I freely consent to the described processing of my responses for this study.",
        )
        if consent
        else t(
            "Mit dem Absenden nehme ich freiwillig an dieser Studie teil. Die genannte "
            "Rechtsgrundlage ist keine Einwilligung.",
            "By submitting, I voluntarily participate in this study. The stated legal basis is "
            "not consent.",
        )
    )
    audio_declaration = (
        t(
            "Ich stimme der Audioverarbeitung durch SixSentences und den in der verlinkten "
            "Datenschutzerklärung ausgewiesenen Live-KI-Anbieter für dieses KI-Interview zu"
            + (
                " und bin mit der Speicherung einer Gesprächsaufnahme durch das Forschungsteam "
                "einverstanden."
                if recording
                else ". Es wird keine Gesprächsaufnahme im Workspace gespeichert."
            ),
            "I agree to audio processing by SixSentences and the live AI provider identified "
            "in the linked privacy notice for this AI interview"
            + (
                " and to the research team storing a recording of the conversation."
                if recording
                else ". No conversation recording is stored in the workspace."
            ),
        )
        if mode == "live"
        else ""
    )
    rights = t(
        "Sie können das Forschungsteam über den oben genannten Kontakt um Auskunft, "
        "Berichtigung, Löschung und Einschränkung bitten. Rechte gelten unter ihren gesetzlichen "
        "Voraussetzungen. Sie können sich bei der genannten Datenschutzaufsicht beschweren.",
        "Contact the research team above to request access, correction, erasure or restriction. "
        "Rights apply subject to their legal conditions. You may complain to the named "
        "supervisory authority.",
    )
    rights += (
        t(
            " Ihre Einwilligung können Sie dort jederzeit für die Zukunft widerrufen; die "
            "Rechtmäßigkeit früherer Verarbeitung bleibt unberührt. Soweit anwendbar besteht ein "
            "Recht auf Datenübertragbarkeit.",
            " You may withdraw consent there at any time for the future without affecting "
            "earlier lawful processing. Data portability applies where its conditions are met.",
        )
        if consent
        else t(
            " Sie können aus Gründen Ihrer besonderen Situation der Verarbeitung widersprechen; "
            "das Forschungsteam prüft die gesetzlichen Voraussetzungen.",
            " You may object to processing on grounds relating to your particular situation; the "
            "research team assesses the applicable legal conditions.",
        )
    )
    processing = t(
        "Der Betreiber dieser SixSentences-Instanz speichert Antworten und Studieninhalte für "
        "das Forschungsteam. Das Forschungsteam muss Speicherort, Unterauftragnehmer, "
        "Sicherungen und Löschfristen in seiner verlinkten Datenschutzerklärung vollständig und "
        "aktuell beschreiben. Eine Fristangabe allein bewirkt keine automatische Löschung.",
        "The operator of this SixSentences instance stores responses and study content for the "
        "research team. The research team must describe storage locations, subprocessors, "
        "backups and deletion schedules completely and accurately in its linked privacy notice. "
        "Entering a retention period does not itself schedule automatic deletion.",
    )
    processing += t(
        " Das Forschungsteam kann Antworten im Workspace exportieren und KI-gestützt auswerten. "
        "Wenn der Betreiber externe KI-Dienste aktiviert, können die dafür erforderlichen "
        "Inhalte an die ausgewiesenen Anbieter übermittelt werden. Deren Verarbeitungsorte, "
        "Aufbewahrung, Prüfzugriffe, Unterauftragnehmer, Vertragsbedingungen und "
        "Transfergarantien muss das Forschungsteam vor der Freigabe prüfen und in der verlinkten "
        "Datenschutzerklärung beschreiben. Eine Workspace-Löschung verkürzt keine getrennten "
        "Anbieterfristen. Besondere Kategorien nach Art. 9 DSGVO, Daten nach Art. 10 DSGVO und "
        "vertrauliche Daten Dritter setzen die erforderliche Rechtsgrundlage, Berechtigung, "
        "geeignete Schutzmaßnahmen und einen geeigneten Funktionsumfang voraus; dafür ist das "
        "Forschungsteam verantwortlich. Die Teilnahme allein erlaubt keine zusätzlichen "
        "Zwecke oder Veröffentlichung personenbezogener Daten.",
        " The research team can export responses and use AI-assisted analysis in the workspace. "
        "If the operator enables external AI services, the required content may be sent to the "
        "identified providers. Before publication, the research team must review and describe "
        "their processing locations, retention, human review access, subprocessors, contractual "
        "terms and transfer safeguards in the linked privacy notice. Workspace deletion does not "
        "shorten separate provider retention. GDPR Article 9 special-category data, Article 10 "
        "data and confidential third-party "
        "information require the applicable legal basis, authority, suitable safeguards and "
        "an appropriate feature configuration; the research team is responsible for these. "
        "Participation alone does not authorize additional purposes or publication of "
        "personal data.",
    )
    if mode in {"text", "live"}:
        processing += t(
            " Nach Abschluss kann automatisch eine erste textbasierte qualitative Auswertung "
            "erstellt werden. Sie dient ausschließlich als überprüfbares Forschungsmaterial und "
            "erfordert menschliche Prüfung. Der freigegebene Funktionsumfang darf weder "
            "biometrische Identifikation noch die Ableitung oder Bewertung von Persönlichkeit, "
            "Emotion, Gesundheit, Glaubwürdigkeit, Eignung, Leistung oder geschützten Merkmalen "
            "vornehmen und darf keine rechtlich oder ähnlich erhebliche Entscheidung über "
            "Teilnehmende treffen oder empfehlen.",
            " After completion, an initial text-based qualitative analysis may be generated "
            "automatically. It is reviewable research material only and requires human review. "
            "The released feature must not perform biometric identification; infer or score "
            "personality, emotion, health, credibility, suitability, performance or protected "
            "traits; or make or recommend a legal or similarly significant decision about a "
            "participant.",
        )
    sections = [
        {
            "title": t("Verantwortliche Stelle und Kontakt", "Controller and contact"),
            "body": "\n".join(
                filter(
                    None,
                    [
                        info.controller_name,
                        info.controller_address,
                        info.contact_email,
                        info.data_protection_contact,
                    ],
                )
            ),
        },
        {
            "title": t("Zweck und Daten", "Purpose and data"),
            "body": f"{info.purpose}\n{info.data_categories}",
        },
        {
            "title": t("Rechtsgrundlage", "Legal basis"),
            "body": f"{basis}\n{info.legal_basis_details}",
        },
        {
            "title": t("Aufbewahrung und Empfänger", "Retention and recipients"),
            "body": (
                f"{info.retention_period}\n{info.additional_recipients}\n"
                f"{info.additional_transfers}"
            ),
        },
        {
            "title": t("Verarbeitung durch SixSentences", "Processing by SixSentences"),
            "body": processing,
        },
        {
            "title": t("Ihre Rechte", "Your rights"),
            "body": f"{rights}\n{info.supervisory_authority}",
        },
    ]
    core = [
        info.purpose,
        f"{info.controller_name} · {info.contact_email}",
        info.retention_period,
        t(
            "Freiwillige Teilnahme: Sie können jederzeit aufhören, ohne Nachteile. Keine Pflicht "
            "zur Bereitstellung Ihrer Antworten.",
            "Participation is voluntary: you can stop at any time without disadvantage. You are "
            "not required to provide responses.",
        ),
    ]
    if mode != "survey":
        core.insert(
            0,
            t(
                "Sie sprechen mit einer KI, nicht mit einem Menschen.",
                "You are interacting with an AI, not a human.",
            ),
        )
    if mode == "live":
        core.append(
            t(
                "Audio wird über SixSentences an den vom Instanzbetreiber konfigurierten "
                "Live-KI-Anbieter übermittelt. Das Forschungsteam muss den Anbieter, dessen "
                "aktuelle Aufbewahrung und mögliche Drittlandtransfers vor der Freigabe in der "
                "verlinkten Datenschutzerklärung ausweisen.",
                "Audio is relayed through SixSentences to the live AI provider configured by the "
                "instance operator. Before publication, the research team must identify the "
                "provider and disclose its current retention and possible international transfers "
                "in the linked privacy notice.",
            )
        )
        sections.append(
            {
                "title": t("Sprachverarbeitung und Aufnahme", "Voice processing and recording"),
                "body": t(
                    "SixSentences leitet Audio kurzzeitig gepuffert an den vom Instanzbetreiber "
                    "konfigurierten Live-KI-Anbieter weiter und "
                    "speichert vom Anbieter erzeugte Transkriptfragmente mit Rollen/Zeitangaben "
                    "sowie zusammengefasste Nutzungsmessungen. Der Relay selbst erzeugt kein "
                    "Audioarchiv. "
                    + (
                        "Bei erfolgreichem Abschluss kann der Browser eine gemischte Aufnahme "
                        "aus Mikrofon und KI-Antworten im Workspace speichern. "
                        if recording
                        else "Im Workspace wird keine Gesprächsaufnahme gespeichert. "
                    )
                    + (
                        "Das Forschungsteam muss Anbieter, Zweck, Verarbeitungsorte, "
                        "Aufbewahrung, mögliche Prüfzugriffe, Unterauftragnehmer, "
                        "Vertragsbedingungen und Transfergarantien vor der Freigabe prüfen und "
                        "aktuell in seiner verlinkten Datenschutzerklärung beschreiben. Die "
                        "Auswahl nur Transkript und eine Workspace-Löschung verkürzen keine "
                        "getrennten Anbieterfristen. Dieser Hinweis selbst begründet oder ersetzt "
                        "keine erforderliche Vertragsfreigabe."
                    ),
                    "SixSentences transiently relays audio to the live AI provider configured by "
                    "the instance operator and stores "
                    "provider-generated transcript fragments with roles/timestamps and aggregate "
                    "usage measurements. The relay itself creates no audio archive. "
                    + (
                        "On successful completion the browser may store a mixed "
                        "microphone/AI-response recording in the workspace. "
                        if recording
                        else "No conversation recording is stored in the workspace. "
                    )
                    + (
                        "Before publication, the research team must review the provider, purpose, "
                        "processing locations, retention, possible human review, subprocessors, "
                        "contractual terms and transfer safeguards, and describe them accurately "
                        "in its linked privacy notice. Transcript-only selection and workspace "
                        "deletion do not shorten separate provider retention. This notice does not "
                        "itself establish or replace required contract approval."
                    ),
                ),
            }
        )
    return {
        "version": PARTICIPANT_INFORMATION_VERSION,
        "language": "de" if de else "en",
        "mode": mode,
        "core": core,
        "sections": sections,
        "declaration": declaration,
        "audio_declaration": audio_declaration,
        "requires_consent": consent,
        "privacy_notice_url": info.privacy_notice_url,
        "platform_privacy_url": info.privacy_notice_url,
    }


def notice_fingerprint(surface: dict[str, Any]) -> str:
    """Identify the entire participant-facing surface, not mutable references."""
    return hashlib.sha256(
        json.dumps(surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def participation_snapshot(
    surface: dict[str, Any],
    *,
    consent: bool,
    audio_consent: bool = False,
    age_confirmed: bool = False,
    mode: str,
) -> dict[str, Any]:
    """Freeze only the declarations actually given; never fabricate legacy receipts."""
    return {
        "schema_version": 1,
        "surface": deepcopy(surface),
        "fingerprint": notice_fingerprint(surface),
        "recorded_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "participation_confirmed": True,
        "consent_given": consent,
        "audio_consent_given": audio_consent,
        "age_confirmed": age_confirmed,
    }
