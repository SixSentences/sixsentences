"""Immutable, operator-only evaluation harness for TypeSafe Jev.

The harness deliberately lives outside the review pipeline.  It accepts one
license-attested public-bibliographic fixture, calls the Jev shadow adapter, and
writes raw-text-free, content-minimized evaluation evidence. It never opens a database session
or creates an authoritative screening decision.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sixsentences_server.core.models import ReviewProtocol, WorkRecord
from sixsentences_server.evals.quality import QualityThresholds
from sixsentences_server.screening.jev import (
    JEV_MODEL,
    JevAdvisorySignal,
    JevShadowResult,
)

JEV_SHADOW_DATASET_SCHEMA: Final = 1
JEV_SHADOW_REPORT_SCHEMA: Final = 1
JEV_SHADOW_RUNNER: Final = "sixsentences_server.jev-shadow-eval"
JEV_MAX_INPUT_TOKENS_PER_CALL: Final = 64_000
JEV_INPUT_USD_PER_MILLION_TOKENS: Final = Decimal("0.042")
_MAX_RECORDS: Final = 100_000


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


class JevShadowGoldVerdict(StrEnum):
    """Independent, human-adjudicated title/abstract label."""

    INCLUDE = "include"
    EXCLUDE = "exclude"


class JevShadowDatasetMetadata(BaseModel):
    """Pinned provenance and reuse permission for one evaluation dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dataset_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=1, max_length=2_000)
    license: str = Field(min_length=1, max_length=500)
    license_verified: bool


class JevShadowProtocol(BaseModel):
    """Deeply immutable JSON representation of ``ReviewProtocol``.

    The conversion happens only in memory.  The report retains a digest, not
    the research question, query, or eligibility text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    question: str = Field(min_length=1, max_length=20_000)
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    year_from: int | None = Field(default=None, ge=1000, le=3000)
    year_to: int | None = Field(default=None, ge=1000, le=3000)
    languages: tuple[str, ...] = ("en",)
    peer_reviewed_only: bool = False
    query_string: str = Field(min_length=1, max_length=100_000)
    comparison_required: bool = False
    comparison_note: str = Field(default="", max_length=20_000)
    eligibility_dimensions: tuple[str, ...] = ()
    synthesized_by: str = Field(default="heuristic", min_length=1, max_length=500)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_protocol(self) -> JevShadowProtocol:
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from must not exceed year_to")
        criteria = self.inclusion_criteria + self.exclusion_criteria
        if not criteria:
            raise ValueError("at least one inclusion or exclusion criterion is required")
        if any(not item.strip() for item in criteria):
            raise ValueError("screening criteria must not be blank")
        if len(criteria) > 100:
            raise ValueError("at most 100 screening criteria are permitted")
        if any(len(item) > 8_000 or len(item.encode("utf-8")) > 8_000 for item in criteria):
            raise ValueError("a screening criterion exceeds the permitted size")
        if not self.languages or any(not language.strip() for language in self.languages):
            raise ValueError("at least one non-blank language is required")
        return self

    def to_review_protocol(self) -> ReviewProtocol:
        """Return the shared screening contract used by the Jev adapter."""

        return ReviewProtocol(
            question=self.question,
            inclusion_criteria=list(self.inclusion_criteria),
            exclusion_criteria=list(self.exclusion_criteria),
            year_from=self.year_from,
            year_to=self.year_to,
            languages=list(self.languages),
            peer_reviewed_only=self.peer_reviewed_only,
            query_string=self.query_string,
            comparison_required=self.comparison_required,
            comparison_note=self.comparison_note,
            eligibility_dimensions=list(self.eligibility_dimensions),
            synthesized_by=self.synthesized_by,
            version=self.version,
        )


class JevShadowDatasetRecord(BaseModel):
    """One public title/abstract and optional independent gold label."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=8_000)
    abstract: str = Field(max_length=30_000)
    domain: str | None = Field(default=None, min_length=1, max_length=500)
    gold: JevShadowGoldVerdict | None = None

    @model_validator(mode="after")
    def enforce_adapter_byte_limits(self) -> JevShadowDatasetRecord:
        if len(self.title.encode("utf-8")) > 8_000:
            raise ValueError("title exceeds the Jev adapter byte limit")
        if len(self.abstract.encode("utf-8")) > 30_000:
            raise ValueError("abstract exceeds the Jev adapter byte limit")
        return self


class JevShadowDataset(BaseModel):
    """Versioned input artifact; unknown or identifying fields fail closed."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = JEV_SHADOW_DATASET_SCHEMA
    metadata: JevShadowDatasetMetadata
    protocol: JevShadowProtocol
    records: tuple[JevShadowDatasetRecord, ...] = Field(min_length=1, max_length=_MAX_RECORDS)

    @model_validator(mode="after")
    def unique_record_ids(self) -> JevShadowDataset:
        record_ids = [record.record_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("records contain duplicate record_id values")
        return self

    @property
    def digest(self) -> str:
        """Stable digest over the exact validated public evaluation input."""

        return _sha256(self.model_dump(mode="json"))

    @property
    def protocol_digest(self) -> str:
        """Stable protocol identity without copying protocol text to output."""

        return _sha256(self.protocol.model_dump(mode="json"))


class JevShadowScreeningMetrics(BaseModel):
    """Recall-first title/abstract metrics matching the production semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gold: int = Field(ge=0)
    gold_includes: int = Field(ge=0)
    gold_excludes: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    sensitivity: float = Field(ge=0.0, le=1.0)
    specificity: float = Field(ge=0.0, le=1.0)
    false_exclusion_rate: float = Field(ge=0.0, le=1.0)
    unresolved_rate: float = Field(ge=0.0, le=1.0)


class JevShadowDomainMetrics(BaseModel):
    """Metrics retained for one named domain without any source text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    domain: str
    metrics: JevShadowScreeningMetrics


class JevShadowGate(BaseModel):
    """One reproducible release-quality comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    value: float
    threshold: float
    operator: Literal[">=", "<="]
    passed: bool


class JevShadowRecordResult(BaseModel):
    """One raw-text-free shadow prediction and its provider provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_id: str
    domain: str | None = None
    gold: JevShadowGoldVerdict | None = None
    shadow: JevShadowResult


class JevShadowReportMetadata(BaseModel):
    """Safe subset of input provenance retained in the output artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dataset_id: str
    version: str
    source_uri: str
    license: str
    license_verified: bool


class JevShadowUsageSummary(BaseModel):
    """Provider-reported usage and a transparent price calculation, not an invoice."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    requests: int = Field(ge=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    input_price_usd_per_million_tokens: float = Field(ge=0.0)
    estimated_input_cost_usd: float = Field(ge=0.0)
    calculation_basis: Literal[
        "provider_reported_input_tokens_x_published_input_price"
    ] = "provider_reported_input_tokens_x_published_input_price"
    provider_invoice: Literal[False] = False


class JevShadowEvaluationReport(BaseModel):
    """Immutable output evidence that cannot masquerade as a product decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = JEV_SHADOW_REPORT_SCHEMA
    generated_at: datetime
    generated_by: Literal["sixsentences_server.jev-shadow-eval"] = JEV_SHADOW_RUNNER
    mode: Literal["shadow"] = "shadow"
    advisory_only: Literal[True] = True
    authoritative: Literal[False] = False
    metadata: JevShadowReportMetadata
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    seed: Literal[42] = 42
    model: Literal["jev-1.13.0"] = JEV_MODEL
    minimum_confidence: float = Field(ge=0.0, le=1.0)
    budget_usd: float = Field(ge=0.0)
    conservative_reserved_usd: float = Field(ge=0.0)
    evaluated_records: int = Field(ge=1)
    total_dataset_records: int = Field(ge=1)
    records: tuple[JevShadowRecordResult, ...] = Field(min_length=1)
    usage: JevShadowUsageSummary
    metrics: JevShadowScreeningMetrics | None = None
    by_domain: tuple[JevShadowDomainMetrics, ...] = ()
    gates: tuple[JevShadowGate, ...] = ()
    gate_evaluation_complete: bool
    gate_blockers: tuple[str, ...]
    passed: bool

    @model_validator(mode="after")
    def enforce_advisory_gate(self) -> JevShadowEvaluationReport:
        if self.passed and (not self.gate_evaluation_complete or self.gate_blockers):
            raise ValueError("an incomplete Jev shadow gate cannot pass")
        if self.passed and any(not gate.passed for gate in self.gates):
            raise ValueError("a Jev shadow report cannot pass a failed quality gate")
        return self

    @property
    def digest(self) -> str:
        """Stable report identity independent of the execution timestamp."""

        return _sha256(self.model_dump(mode="json", exclude={"generated_at"}))


class JevShadowEvaluator(Protocol):
    """Narrow protocol implemented by the network adapter and test doubles."""

    def evaluate(
        self,
        work: WorkRecord,
        protocol: ReviewProtocol,
        *,
        public_bibliographic_data_confirmed: bool,
        minimum_confidence: float,
    ) -> JevShadowResult: ...


def load_jev_shadow_dataset(path: Path) -> JevShadowDataset:
    """Load one strict schema-v1 dataset from disk."""

    return JevShadowDataset.model_validate_json(path.read_text(encoding="utf-8"), strict=True)


def write_jev_shadow_report(path: Path, report: JevShadowEvaluationReport) -> None:
    """Create, but never overwrite, one completed shadow evidence artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(report.model_dump_json(indent=2))


def conservative_jev_reservation_usd(pending_calls: int) -> Decimal:
    """Reserve Jev's full 64k context price for every non-retried call."""

    if isinstance(pending_calls, bool) or pending_calls < 0:
        raise ValueError("pending_calls must be a non-negative integer")
    return (
        Decimal(pending_calls)
        * Decimal(JEV_MAX_INPUT_TOKENS_PER_CALL)
        * JEV_INPUT_USD_PER_MILLION_TOKENS
        / Decimal(1_000_000)
    )


def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
    return numerator / denominator if denominator else empty


def _screening_metrics(
    rows: list[tuple[JevShadowGoldVerdict, JevAdvisorySignal]],
) -> JevShadowScreeningMetrics:
    true_positive = true_negative = false_positive = false_negative = unresolved = 0
    for gold, predicted in rows:
        if predicted is JevAdvisorySignal.UNSURE:
            unresolved += 1
        elif gold is JevShadowGoldVerdict.INCLUDE:
            if predicted is JevAdvisorySignal.INCLUDE:
                true_positive += 1
            else:
                false_negative += 1
        elif predicted is JevAdvisorySignal.EXCLUDE:
            true_negative += 1
        else:
            false_positive += 1
    includes = sum(gold is JevShadowGoldVerdict.INCLUDE for gold, _ in rows)
    excludes = len(rows) - includes
    return JevShadowScreeningMetrics(
        gold=len(rows),
        gold_includes=includes,
        gold_excludes=excludes,
        evaluated=len(rows) - unresolved,
        true_positive=true_positive,
        true_negative=true_negative,
        false_positive=false_positive,
        false_negative=false_negative,
        unresolved=unresolved,
        sensitivity=_ratio(true_positive, includes, empty=1.0),
        specificity=_ratio(true_negative, excludes, empty=1.0),
        false_exclusion_rate=_ratio(false_negative, includes, empty=0.0),
        unresolved_rate=_ratio(unresolved, len(rows), empty=0.0),
    )


def _gate(
    name: str,
    value: float,
    threshold: float,
    operator: Literal[">=", "<="],
) -> JevShadowGate:
    passed = value >= threshold if operator == ">=" else value <= threshold
    return JevShadowGate(
        name=name,
        value=round(value, 6),
        threshold=threshold,
        operator=operator,
        passed=passed,
    )

def _quality_evidence(
    dataset: JevShadowDataset,
    records: tuple[JevShadowRecordResult, ...],
) -> tuple[
    JevShadowScreeningMetrics | None,
    tuple[JevShadowDomainMetrics, ...],
    tuple[JevShadowGate, ...],
    tuple[str, ...],
]:
    labeled = [
        (record.gold, record.shadow.signal)
        for record in records
        if record.gold is not None
    ]
    if not labeled:
        return None, (), (), ("no independent gold labels",)

    overall = _screening_metrics(labeled)
    grouped: defaultdict[str, list[tuple[JevShadowGoldVerdict, JevAdvisorySignal]]] = (
        defaultdict(list)
    )
    unlabeled_count = 0
    missing_domain_count = 0
    for record in records:
        if record.gold is None:
            unlabeled_count += 1
        elif record.domain is None:
            missing_domain_count += 1
        else:
            grouped[record.domain].append((record.gold, record.shadow.signal))
    by_domain = tuple(
        JevShadowDomainMetrics(domain=domain, metrics=_screening_metrics(items))
        for domain, items in sorted(grouped.items())
    )

    limits = QualityThresholds()
    gates = (
        _gate(
            "minimum screening labels",
            float(overall.gold),
            float(limits.min_screening_gold),
            ">=",
        ),
        _gate(
            "minimum screening includes",
            float(overall.gold_includes),
            float(limits.min_screening_includes),
            ">=",
        ),
        _gate(
            "minimum screening excludes",
            float(overall.gold_excludes),
            float(limits.min_screening_excludes),
            ">=",
        ),
        _gate(
            "minimum screening domains",
            float(len(by_domain)),
            float(limits.min_screening_domains),
            ">=",
        ),
        _gate(
            "screening sensitivity",
            overall.sensitivity,
            limits.min_screening_sensitivity,
            ">=",
        ),
        _gate(
            "screening false exclusion",
            overall.false_exclusion_rate,
            limits.max_screening_false_exclusion_rate,
            "<=",
        ),
        _gate(
            "screening unresolved",
            overall.unresolved_rate,
            limits.max_screening_unresolved_rate,
            "<=",
        ),
        _gate(
            "worst-domain screening sensitivity",
            min((domain.metrics.sensitivity for domain in by_domain), default=0.0),
            limits.min_domain_screening_sensitivity,
            ">=",
        ),
        _gate(
            "worst-domain screening specificity",
            min((domain.metrics.specificity for domain in by_domain), default=0.0),
            limits.min_domain_screening_specificity,
            ">=",
        ),
    )

    blockers: list[str] = []
    if not dataset.metadata.license_verified:
        blockers.append("dataset license has not been verified")
    if len(records) != len(dataset.records):
        blockers.append("only a bounded prefix was evaluated; the complete dataset is required")
    inventory_gates = gates[:4]
    if any(not gate.passed for gate in inventory_gates):
        blockers.append("production screening label and domain inventory is incomplete")
    if unlabeled_count:
        blockers.append(f"{unlabeled_count} evaluated records lack independent gold labels")
    if missing_domain_count:
        blockers.append(f"{missing_domain_count} labeled records lack a domain")
    domain_inventory = Counter(
        record.domain
        for record in records
        if record.gold is not None and record.domain is not None
    )
    if domain_inventory and min(domain_inventory.values()) < limits.min_cases_per_domain:
        blockers.append("at least one labeled domain has fewer than two records")
    for domain in by_domain:
        if not domain.metrics.gold_includes or not domain.metrics.gold_excludes:
            blockers.append(
                f"domain {domain.domain!r} lacks include or exclude labels "
                "for both worst-domain gates"
            )
    return overall, by_domain, gates, tuple(blockers)


def run_jev_shadow_evaluation(
    dataset: JevShadowDataset,
    *,
    client: JevShadowEvaluator,
    git_revision: str,
    budget_usd: Decimal,
    limit: int,
    minimum_confidence: float,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> JevShadowEvaluationReport:
    """Evaluate a bounded prefix without touching product state or storage."""

    if len(git_revision) != 40 or any(
        character not in "0123456789abcdef" for character in git_revision
    ):
        raise ValueError("git_revision must be a full lowercase commit SHA")
    if isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if isinstance(minimum_confidence, bool) or not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence must be between zero and one")
    if not budget_usd.is_finite() or budget_usd < 0:
        raise ValueError("budget_usd must be a finite non-negative decimal")
    if not dataset.metadata.license_verified:
        raise ValueError("dataset license must be independently verified before provider egress")
    selected = dataset.records[:limit]
    reservation = conservative_jev_reservation_usd(len(selected))
    if budget_usd < reservation:
        raise ValueError(
            "budget is below the conservative 64k-context reservation "
            f"(${reservation:.6f} required)"
        )

    protocol = dataset.protocol.to_review_protocol()
    evaluated: list[JevShadowRecordResult] = []
    for index, record in enumerate(selected, start=1):
        if on_progress is not None:
            on_progress(index, len(selected), record.record_id)
        work = WorkRecord(
            # A constant local sentinel prevents benchmark identifiers from
            # reaching even the adapter boundary. The adapter itself transmits
            # only title and abstract.
            id="jev-shadow-public-record",
            title=record.title,
            abstract=record.abstract,
        )
        shadow = client.evaluate(
            work,
            protocol,
            public_bibliographic_data_confirmed=True,
            minimum_confidence=minimum_confidence,
        )
        if shadow.model != JEV_MODEL:
            raise ValueError("Jev shadow result used an unexpected model")
        if shadow.minimum_confidence != minimum_confidence:
            raise ValueError("Jev shadow result used an unexpected confidence floor")
        if shadow.usage.input_tokens > JEV_MAX_INPUT_TOKENS_PER_CALL:
            raise ValueError("Jev reported usage above the preflight reservation")
        evaluated.append(
            JevShadowRecordResult(
                record_id=record.record_id,
                domain=record.domain,
                gold=record.gold,
                shadow=shadow,
            )
        )

    immutable_records = tuple(evaluated)
    metrics, by_domain, gates, blockers = _quality_evidence(dataset, immutable_records)
    gate_complete = not blockers and bool(gates)
    passed = gate_complete and all(gate.passed for gate in gates)
    metadata = dataset.metadata
    total_input_tokens = sum(item.shadow.usage.input_tokens for item in immutable_records)
    total_output_tokens = sum(item.shadow.usage.output_tokens for item in immutable_records)
    estimated_cost = (
        Decimal(total_input_tokens)
        * JEV_INPUT_USD_PER_MILLION_TOKENS
        / Decimal(1_000_000)
    )
    return JevShadowEvaluationReport(
        generated_at=datetime.now(UTC),
        metadata=JevShadowReportMetadata(
            dataset_id=metadata.dataset_id,
            version=metadata.version,
            source_uri=metadata.source_uri,
            license=metadata.license,
            license_verified=metadata.license_verified,
        ),
        dataset_digest=dataset.digest,
        protocol_digest=dataset.protocol_digest,
        git_revision=git_revision,
        minimum_confidence=minimum_confidence,
        budget_usd=float(budget_usd),
        conservative_reserved_usd=float(reservation),
        evaluated_records=len(immutable_records),
        total_dataset_records=len(dataset.records),
        records=immutable_records,
        usage=JevShadowUsageSummary(
            requests=len(immutable_records),
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            input_price_usd_per_million_tokens=float(JEV_INPUT_USD_PER_MILLION_TOKENS),
            estimated_input_cost_usd=float(estimated_cost),
        ),
        metrics=metrics,
        by_domain=by_domain,
        gates=gates,
        gate_evaluation_complete=gate_complete,
        gate_blockers=blockers,
        passed=passed,
    )
