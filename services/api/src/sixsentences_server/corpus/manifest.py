"""Versioned corpus build plans, manifests, and release quality gates.

The corpus is an operational dependency, not an opaque parquet file.  These
models make every generation reproducible and allow the release pipeline to
reject a smaller, biased, corrupt, or otherwise incomplete build before the
active pointer is changed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field, model_validator

CORPUS_MANIFEST_SCHEMA: Final = 3
DEFAULT_SEED = 42


class CorpusSlice(BaseModel):
    """One mutually exclusive OpenAlex slice in a stratified corpus build."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    label: str = Field(min_length=2, max_length=100)
    oa_filter: str = Field(min_length=3)
    target_works: int = Field(gt=0)
    from_year: int | None = Field(default=None, ge=1900, le=2100)
    to_year: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def validate_years(self) -> CorpusSlice:
        if (
            self.from_year is not None
            and self.to_year is not None
            and self.from_year > self.to_year
        ):
            raise ValueError("corpus slice from_year must not exceed to_year")
        normalized_filter = self.oa_filter.lower()
        if (
            "from_publication_date:" in normalized_filter
            or "to_publication_date:" in normalized_filter
        ):
            raise ValueError("publication dates belong in corpus slice year fields")
        return self


class CorpusQualityGates(BaseModel):
    """Fail-closed thresholds evaluated before a generation is activated."""

    minimum_target_ratio: float = Field(default=0.95, ge=0.0, le=1.0)
    minimum_unique_ratio: float = Field(default=0.995, ge=0.0, le=1.0)
    minimum_abstract_ratio: float = Field(default=0.98, ge=0.0, le=1.0)
    minimum_title_ratio: float = Field(default=0.999, ge=0.0, le=1.0)
    minimum_slice_ratio: float = Field(default=0.90, ge=0.0, le=1.0)


class CorpusBuildPlan(BaseModel):
    """Serializable input contract for one deterministic corpus build."""

    schema_version: Literal[2] = 2
    profile: str = Field(min_length=2, max_length=80)
    from_year: int = Field(ge=1900, le=2100)
    to_year: int = Field(default=2100, ge=1900, le=2100)
    seed: int = DEFAULT_SEED
    sort: str = Field(default="cited_by_count:desc", min_length=3, max_length=80)
    slices: tuple[CorpusSlice, ...]
    quality_gates: CorpusQualityGates = Field(default_factory=CorpusQualityGates)

    @model_validator(mode="after")
    def validate_slices(self) -> CorpusBuildPlan:
        if not self.slices:
            raise ValueError("a corpus build plan needs at least one slice")
        ids = [item.id for item in self.slices]
        if len(ids) != len(set(ids)):
            raise ValueError("corpus slice ids must be unique")
        if self.from_year > self.to_year:
            raise ValueError("corpus plan from_year must not exceed to_year")
        coordinates: set[tuple[str, int, int]] = set()
        intervals: dict[str, list[tuple[int, int, str]]] = {}
        for item in self.slices:
            start = item.from_year if item.from_year is not None else self.from_year
            end = item.to_year if item.to_year is not None else self.to_year
            if start < self.from_year or end > self.to_year:
                raise ValueError(f"corpus slice {item.id!r} lies outside the plan years")
            coordinate = (item.oa_filter, start, end)
            if coordinate in coordinates:
                raise ValueError("corpus slices must not repeat the same filter and years")
            coordinates.add(coordinate)
            intervals.setdefault(item.oa_filter, []).append((start, end, item.id))
        for values in intervals.values():
            ordered = sorted(values)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if current[0] <= previous[1]:
                    raise ValueError(
                        "corpus slices using the same filter have overlapping year bands: "
                        f"{previous[2]!r} and {current[2]!r}"
                    )
        return self

    @property
    def target_works(self) -> int:
        return sum(item.target_works for item in self.slices)

    @property
    def digest(self) -> str:
        payload = self.model_dump_json(exclude_none=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def production_corpus_plan(
    *, target_works: int = 1_000_000, from_year: int = 2015, to_year: int = 2026
) -> CorpusBuildPlan:
    """Return the launch profile balanced across domain and publication era.

    OpenAlex assigns exactly one primary topic to a work, so filtering through
    ``primary_topic.domain.id`` gives mutually exclusive slices and avoids the
    cross-domain duplication produced by the broader ``topics`` relationship.
    Three non-overlapping time bands prevent a citation-descending API ingest
    from becoming almost entirely older, highly cited literature.
    """

    if from_year > to_year:
        raise ValueError("from_year must not exceed to_year")
    domains = (
        ("life-sciences", "Life Sciences", 1),
        ("social-sciences", "Social Sciences", 2),
        ("physical-sciences", "Physical Sciences", 3),
        ("health-sciences", "Health Sciences", 4),
    )
    years = list(range(from_year, to_year + 1))
    band_count = min(3, len(years))
    base_years, extra_years = divmod(len(years), band_count)
    bands: list[tuple[str, int, int]] = []
    offset = 0
    for index in range(band_count):
        width = base_years + (1 if index < extra_years else 0)
        band_years = years[offset : offset + width]
        bands.append((f"era-{index + 1}", band_years[0], band_years[-1]))
        offset += width
    slice_count = len(domains) * len(bands)
    if target_works < slice_count:
        raise ValueError(
            f"the multidomain time-stratified profile needs at least {slice_count} works"
        )
    base, remainder = divmod(target_works, slice_count)
    slices: list[CorpusSlice] = []
    for domain_slug, domain_label, domain_id in domains:
        for band_slug, band_from, band_to in bands:
            index = len(slices)
            slices.append(
                CorpusSlice(
                    id=f"{domain_slug}-{band_slug}",
                    label=f"{domain_label}, {band_from} to {band_to}",
                    oa_filter=f"primary_topic.domain.id:{domain_id},has_abstract:true",
                    target_works=base + (1 if index < remainder else 0),
                    from_year=band_from,
                    to_year=band_to,
                )
            )
    return CorpusBuildPlan(
        profile="production-multidomain-v2",
        from_year=from_year,
        to_year=to_year,
        slices=tuple(slices),
    )


class CorpusCoverage(BaseModel):
    fetched_rows: int
    unique_works: int
    duplicate_rows: int
    title_works: int
    abstract_works: int
    doi_works: int
    open_access_works: int
    retracted_works: int
    out_of_range_works: int = 0
    year_min: int | None
    year_max: int | None
    by_slice: dict[str, int]
    by_work_type: dict[str, int]

    @property
    def unique_ratio(self) -> float:
        return self.unique_works / self.fetched_rows if self.fetched_rows else 0.0

    @property
    def title_ratio(self) -> float:
        return self.title_works / self.unique_works if self.unique_works else 0.0

    @property
    def abstract_ratio(self) -> float:
        return self.abstract_works / self.unique_works if self.unique_works else 0.0


class CorpusGateResult(BaseModel):
    passed: bool
    checks: dict[str, bool]
    failures: tuple[str, ...] = ()


class CorpusManifest(BaseModel):
    """Schema-v3 active pointer and immutable generation description."""

    manifest_schema: int = CORPUS_MANIFEST_SCHEMA
    version: str
    works: int = Field(ge=0)
    sources: dict[str, str]
    created_at: datetime
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    snapshot: str
    works_file: str
    meta_file: str
    build: CorpusBuildPlan
    build_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: CorpusCoverage
    gates: CorpusGateResult

    @model_validator(mode="after")
    def validate_manifest(self) -> CorpusManifest:
        if self.build_digest != self.build.digest:
            raise ValueError("corpus build digest does not match the embedded plan")
        if self.works != self.coverage.unique_works:
            raise ValueError("manifest works count does not match coverage")
        if not self.gates.passed:
            raise ValueError("a failed corpus generation cannot be activated")
        if self.manifest_schema >= 3:
            expected_gates = evaluate_corpus_gates(self.build, self.coverage)
            if self.gates != expected_gates:
                raise ValueError("corpus gates do not match the embedded coverage")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("corpus manifest created_at must include a timezone")
        return self

    def stable_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True)


def evaluate_corpus_gates(plan: CorpusBuildPlan, coverage: CorpusCoverage) -> CorpusGateResult:
    gates = plan.quality_gates
    checks = {
        "target_works": coverage.unique_works >= plan.target_works * gates.minimum_target_ratio,
        "unique_ids": coverage.unique_ratio >= gates.minimum_unique_ratio,
        "titles": coverage.title_ratio >= gates.minimum_title_ratio,
        "abstracts": coverage.abstract_ratio >= gates.minimum_abstract_ratio,
        "publication_years": coverage.out_of_range_works == 0,
    }
    for item in plan.slices:
        checks[f"slice:{item.id}"] = (
            coverage.by_slice.get(item.id, 0) >= item.target_works * gates.minimum_slice_ratio
        )
    failures = tuple(name for name, passed in checks.items() if not passed)
    return CorpusGateResult(passed=not failures, checks=checks, failures=failures)
