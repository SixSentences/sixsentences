"""Deterministic research-plan contracts for bounded multi-angle retrieval.

The plan is deliberately created by server code rather than by another model
call.  It gives an existing research agent a small set of complementary
subquestions, stable identifiers and explicit coverage expectations without
adding a second agent or a provider-specific wire format.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

RESEARCH_PLAN_SCHEMA_VERSION: Final = 1
MIN_RESEARCH_ANGLES: Final = 3
MAX_RESEARCH_ANGLES: Final = 5

_ID = re.compile(r"[a-z][a-z0-9_]{2,63}")
_FINGERPRINT = re.compile(r"[0-9a-f]{16}")
_PLAN_ID = re.compile(r"rp_[0-9a-f]{16}")
_IDENTITY_SEPARATOR = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class ResearchAngleDraft:
    """One caller-supplied search angle before IDs and bounds are applied."""

    label: str
    subquestion: str
    coverage_criterion: str

    def __post_init__(self) -> None:
        for name, value in (
            ("label", self.label),
            ("subquestion", self.subquestion),
            ("coverage_criterion", self.coverage_criterion),
        ):
            if not str(value).strip():
                raise ValueError(f"research angle {name} is required")


@dataclass(frozen=True)
class ResearchAngle:
    """One bounded, persistable subquestion in a research plan."""

    id: str
    label: str
    subquestion: str
    coverage_criterion: str

    def __post_init__(self) -> None:
        if _ID.fullmatch(self.id) is None:
            raise ValueError("research angle id is invalid")
        if self.id != _angle_id(self.subquestion):
            raise ValueError("research angle id is not stable for its subquestion")
        _bounded_text(self.label, name="research angle label", max_chars=120)
        _bounded_text(self.subquestion, name="research angle subquestion", max_chars=800)
        _bounded_text(
            self.coverage_criterion,
            name="research angle coverage criterion",
            max_chars=500,
        )


@dataclass(frozen=True)
class ResearchCoveragePolicy:
    """Observable conditions for deciding whether plan coverage is sufficient."""

    minimum_covered_angles: int = MIN_RESEARCH_ANGLES
    minimum_sources_per_covered_angle: int = 1
    require_independent_source: bool = True
    require_limit_or_conflict_check: bool = True
    require_citation_traceability: bool = True
    allow_explicitly_unresolved: bool = True

    def validate_for(self, angle_count: int) -> None:
        """Validate this policy against a concrete number of plan angles."""

        if not MIN_RESEARCH_ANGLES <= self.minimum_covered_angles <= angle_count:
            raise ValueError(
                "minimum covered angles must be between three and the plan angle count"
            )
        if self.minimum_sources_per_covered_angle < 1:
            raise ValueError("minimum sources per covered angle must be positive")


@dataclass(frozen=True)
class ResearchPlan:
    """A deterministic 3--5 angle plan safe for prompts and JSON persistence."""

    id: str
    request_fingerprint: str
    angles: tuple[ResearchAngle, ...]
    coverage: ResearchCoveragePolicy
    schema_version: int = RESEARCH_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RESEARCH_PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported research plan schema version")
        if _FINGERPRINT.fullmatch(self.request_fingerprint) is None:
            raise ValueError("research plan request fingerprint is invalid")
        if not MIN_RESEARCH_ANGLES <= len(self.angles) <= MAX_RESEARCH_ANGLES:
            raise ValueError("research plans require between three and five angles")
        angle_ids = [angle.id for angle in self.angles]
        if len(angle_ids) != len(set(angle_ids)):
            raise ValueError("research plan angle ids must be unique")
        identities = [_identity_text(angle.subquestion) for angle in self.angles]
        if len(identities) != len(set(identities)):
            raise ValueError("research plan subquestions must be unique")
        self.coverage.validate_for(len(self.angles))
        if _PLAN_ID.fullmatch(self.id) is None or self.id != _plan_id(
            self.request_fingerprint,
            self.angles,
            self.coverage,
        ):
            raise ValueError("research plan id is not stable for its contents")

    def to_metadata(self) -> dict[str, Any]:
        """Return shallow, JSON-safe metadata for prompts and event ledgers.

        Parallel angle arrays keep the public-event representation below the
        event ledger's nesting bound. :meth:`from_metadata` validates their
        lengths before reconstructing typed angle records.
        """

        return {
            "schema_version": self.schema_version,
            "plan_id": self.id,
            "request_fingerprint": self.request_fingerprint,
            "angle_ids": [angle.id for angle in self.angles],
            "angle_labels": [angle.label for angle in self.angles],
            "subquestions": [angle.subquestion for angle in self.angles],
            "angle_coverage_criteria": [angle.coverage_criterion for angle in self.angles],
            "minimum_covered_angles": self.coverage.minimum_covered_angles,
            "minimum_sources_per_covered_angle": (self.coverage.minimum_sources_per_covered_angle),
            "require_independent_source": self.coverage.require_independent_source,
            "require_limit_or_conflict_check": (self.coverage.require_limit_or_conflict_check),
            "require_citation_traceability": (self.coverage.require_citation_traceability),
            "allow_explicitly_unresolved": self.coverage.allow_explicitly_unresolved,
        }

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> ResearchPlan:
        """Restore and validate a plan from persisted JSON metadata."""

        version = _metadata_int(metadata, "schema_version")
        angle_ids = _metadata_strings(metadata, "angle_ids")
        labels = _metadata_strings(metadata, "angle_labels")
        subquestions = _metadata_strings(metadata, "subquestions")
        criteria = _metadata_strings(metadata, "angle_coverage_criteria")
        lengths = {len(angle_ids), len(labels), len(subquestions), len(criteria)}
        if len(lengths) != 1:
            raise ValueError("research plan angle metadata lengths do not match")
        coverage = ResearchCoveragePolicy(
            minimum_covered_angles=_metadata_int(metadata, "minimum_covered_angles"),
            minimum_sources_per_covered_angle=_metadata_int(
                metadata, "minimum_sources_per_covered_angle"
            ),
            require_independent_source=_metadata_bool(metadata, "require_independent_source"),
            require_limit_or_conflict_check=_metadata_bool(
                metadata, "require_limit_or_conflict_check"
            ),
            require_citation_traceability=_metadata_bool(metadata, "require_citation_traceability"),
            allow_explicitly_unresolved=_metadata_bool(metadata, "allow_explicitly_unresolved"),
        )
        return cls(
            id=_metadata_string(metadata, "plan_id"),
            request_fingerprint=_metadata_string(metadata, "request_fingerprint"),
            angles=tuple(
                ResearchAngle(
                    id=angle_id,
                    label=label,
                    subquestion=subquestion,
                    coverage_criterion=criterion,
                )
                for angle_id, label, subquestion, criterion in zip(
                    angle_ids,
                    labels,
                    subquestions,
                    criteria,
                    strict=True,
                )
            ),
            coverage=coverage,
            schema_version=version,
        )


def build_research_plan(
    request: str,
    *,
    candidate_angles: Sequence[ResearchAngleDraft] = (),
    max_angles: int = 4,
) -> ResearchPlan:
    """Build a deterministic, de-duplicated research plan.

    Caller-supplied angles retain their order. Generic complementary angles
    then fill any remaining slots, ensuring the plan always contains at least
    three and never more than five subquestions. No randomness or model call is
    involved, so an experiment seed is not applicable here.
    """

    normalized_request = _bounded_text(
        request,
        name="research request",
        max_chars=12_000,
    )
    if not MIN_RESEARCH_ANGLES <= max_angles <= MAX_RESEARCH_ANGLES:
        raise ValueError("max_angles must be between three and five")
    topic = normalized_request.rstrip(" .?!")[:480]
    defaults = _default_angle_drafts(topic)
    selected: list[ResearchAngle] = []
    seen: set[str] = set()
    for draft in (*candidate_angles, *defaults):
        subquestion = _bounded_text(
            draft.subquestion,
            name="research angle subquestion",
            max_chars=800,
        )
        identity = _identity_text(subquestion)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(
            ResearchAngle(
                id=_angle_id(subquestion),
                label=_bounded_text(
                    draft.label,
                    name="research angle label",
                    max_chars=120,
                ),
                subquestion=subquestion,
                coverage_criterion=_bounded_text(
                    draft.coverage_criterion,
                    name="research angle coverage criterion",
                    max_chars=500,
                ),
            )
        )
        if len(selected) == max_angles:
            break
    if len(selected) < MIN_RESEARCH_ANGLES:  # pragma: no cover - defaults guarantee this
        raise ValueError("research plan could not produce three unique angles")

    angles = tuple(selected)
    coverage = ResearchCoveragePolicy(minimum_covered_angles=min(MIN_RESEARCH_ANGLES, len(angles)))
    request_fingerprint = _digest(_identity_text(normalized_request))
    return ResearchPlan(
        id=_plan_id(request_fingerprint, angles, coverage),
        request_fingerprint=request_fingerprint,
        angles=angles,
        coverage=coverage,
    )


def _default_angle_drafts(topic: str) -> tuple[ResearchAngleDraft, ...]:
    return (
        ResearchAngleDraft(
            label="Scope and primary evidence",
            subquestion=(
                "Which primary or authoritative sources define the relevant scope "
                f"and directly address: {topic}?"
            ),
            coverage_criterion=(
                "Record a relevant primary or authoritative source and the exact "
                "claim or scope boundary it supports."
            ),
        ),
        ResearchAngleDraft(
            label="Independent corroboration",
            subquestion=(
                "Which independent evidence corroborates or challenges the strongest "
                f"finding about: {topic}?"
            ),
            coverage_criterion=(
                "Inspect evidence independent from the first source and record whether "
                "it corroborates, qualifies, or contradicts the leading finding."
            ),
        ),
        ResearchAngleDraft(
            label="Limitations and conflicts",
            subquestion=(
                "Which limitations, conflicting findings, boundary conditions, or "
                f"evidence gaps qualify claims about: {topic}?"
            ),
            coverage_criterion=(
                "Capture at least one material limitation or conflict signal, or "
                "explicitly mark it unresolved after the bounded search."
            ),
        ),
        ResearchAngleDraft(
            label="Current context",
            subquestion=(
                "Which recent or context-specific evidence materially changes the "
                f"answer about: {topic}?"
            ),
            coverage_criterion=(
                "Check the most relevant current context and record whether it changes "
                "the synthesis or adds no material qualification."
            ),
        ),
        ResearchAngleDraft(
            label="Alternative explanations",
            subquestion=(
                "Which plausible alternative explanations or competing approaches "
                f"should be considered for: {topic}?"
            ),
            coverage_criterion=(
                "Inspect one credible alternative where available, or explicitly record "
                "that the bounded evidence did not identify one."
            ),
        ),
    )


def _bounded_text(value: str, *, name: str, max_chars: int) -> str:
    normalized = " ".join(str(value).split()).strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > max_chars:
        raise ValueError(f"{name} exceeds {max_chars} characters")
    return normalized


def _identity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", " ".join(value.split())).casefold()
    return _IDENTITY_SEPARATOR.sub(" ", normalized).strip()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _angle_id(subquestion: str) -> str:
    return f"angle_{_digest(_identity_text(subquestion))}"


def _plan_id(
    request_fingerprint: str,
    angles: Sequence[ResearchAngle],
    coverage: ResearchCoveragePolicy,
) -> str:
    identity = {
        "request_fingerprint": request_fingerprint,
        "angles": [
            {
                "id": angle.id,
                "label": _identity_text(angle.label),
                "criterion": _identity_text(angle.coverage_criterion),
            }
            for angle in angles
        ],
        "coverage": {
            "minimum_covered_angles": coverage.minimum_covered_angles,
            "minimum_sources_per_covered_angle": (coverage.minimum_sources_per_covered_angle),
            "require_independent_source": coverage.require_independent_source,
            "require_limit_or_conflict_check": coverage.require_limit_or_conflict_check,
            "require_citation_traceability": coverage.require_citation_traceability,
            "allow_explicitly_unresolved": coverage.allow_explicitly_unresolved,
        },
    }
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"rp_{_digest(encoded)}"


def _metadata_string(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"research plan metadata {key!r} must be a string")
    return value


def _metadata_strings(metadata: Mapping[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"research plan metadata {key!r} must be a string list")
    return list(value)


def _metadata_int(metadata: Mapping[str, Any], key: str) -> int:
    value = metadata.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"research plan metadata {key!r} must be an integer")
    return value


def _metadata_bool(metadata: Mapping[str, Any], key: str) -> bool:
    value = metadata.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"research plan metadata {key!r} must be a boolean")
    return value
