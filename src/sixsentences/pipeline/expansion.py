"""Provider-neutral query expansion validation and saturation mechanics."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from sixsentences.core.models import ReviewProtocol
from sixsentences.querylang.ast import to_display
from sixsentences.querylang.parser import QueryParseError, parse_query

DEFAULT_VARIANT_LIMIT = 5
DEFAULT_NOVELTY_THRESHOLD = 0.05
MAX_QUERY_LENGTH = 2_000


@dataclass(frozen=True)
class ExpansionContext:
    """Inputs a caller-owned expansion strategy may inspect."""

    protocol: ReviewProtocol
    existing_queries: tuple[str, ...]
    sample_titles: tuple[str, ...] = ()


class QueryVariantProvider(Protocol):
    """Small port for a human, thesaurus, model, or other strategy."""

    def propose(self, context: ExpansionContext) -> Sequence[str]:
        """Return candidate query strings for validation."""

        ...


class ExpansionValidation(BaseModel):
    """Accepted normalized queries plus rejected proposals and reasons."""

    model_config = ConfigDict(extra="forbid")

    accepted: list[str] = Field(default_factory=list)
    rejected: dict[str, str] = Field(default_factory=dict)


class SaturationReport(BaseModel):
    """Observable novelty for one query-expansion round."""

    model_config = ConfigDict(extra="forbid")

    previously_seen: int = Field(ge=0)
    newly_seen: int = Field(ge=0)
    novelty: float = Field(ge=0.0)
    threshold: float = Field(ge=0.0, le=1.0)
    saturated: bool
    stopped_because: str


def validate_variants(
    existing: Sequence[str],
    candidates: Sequence[str],
    *,
    limit: int = DEFAULT_VARIANT_LIMIT,
) -> ExpansionValidation:
    """Validate, normalize, and de-duplicate proposed boolean queries."""

    if limit < 0:
        raise ValueError("limit must be non-negative")
    known: set[str] = set()
    for query in existing:
        known.add(to_display(parse_query(query)))

    accepted: list[str] = []
    rejected: dict[str, str] = {}
    for raw_candidate in candidates:
        candidate = raw_candidate.strip()
        if not candidate:
            rejected[raw_candidate] = "empty"
            continue
        if len(candidate) > MAX_QUERY_LENGTH:
            rejected[raw_candidate] = "too_long"
            continue
        try:
            normalized = to_display(parse_query(candidate))
        except QueryParseError:
            rejected[raw_candidate] = "invalid_syntax"
            continue
        if normalized in known:
            rejected[raw_candidate] = "duplicate"
            continue
        if len(accepted) >= limit:
            rejected[raw_candidate] = "limit_reached"
            continue
        known.add(normalized)
        accepted.append(normalized)
    return ExpansionValidation(accepted=accepted, rejected=rejected)


def propose_variants(
    protocol: ReviewProtocol,
    existing: Sequence[str],
    sample_titles: Sequence[str],
    provider: QueryVariantProvider,
    *,
    limit: int = DEFAULT_VARIANT_LIMIT,
) -> ExpansionValidation:
    """Call a caller-owned strategy and validate everything it returns."""

    context = ExpansionContext(
        protocol=protocol,
        existing_queries=tuple(existing),
        sample_titles=tuple(sample_titles[:15]),
    )
    return validate_variants(existing, provider.propose(context), limit=limit)


def assess_saturation(
    previously_seen_ids: set[str],
    round_ids: set[str],
    *,
    threshold: float = DEFAULT_NOVELTY_THRESHOLD,
) -> SaturationReport:
    """Decide whether a round saturated using new/total unique-work novelty."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between zero and one")
    new_ids = round_ids - previously_seen_ids
    total = len(previously_seen_ids | round_ids)
    novelty = len(new_ids) / total if total else 0.0
    saturated = novelty < threshold
    stopped_because = (
        f"novelty {novelty:.2%} below {threshold:.2%} threshold"
        if saturated
        else f"novelty {novelty:.2%} meets {threshold:.2%} threshold"
    )
    return SaturationReport(
        previously_seen=len(previously_seen_ids),
        newly_seen=len(new_ids),
        novelty=round(novelty, 6),
        threshold=threshold,
        saturated=saturated,
        stopped_because=stopped_because,
    )
