"""Adapters that turn public benchmark releases into immutable golden suites.

Adapters never download implicitly.  Operators first pin a source release on
disk, verify its license, and then build a suite whose SHA-256 digest records
the exact bytes used.  This keeps CI deterministic and prevents upstream data
changes from rewriting historical release evidence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sixsentences_server.evals.quality import (
    GoldClaim,
    GoldenCase,
    GoldenSuite,
    GoldVerdict,
    GoldWork,
    SuiteSource,
    sha256_json,
)


class SynergyCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    question: str = Field(min_length=1)
    query: str = Field(min_length=1)


class ReviewCaseSpec(BaseModel):
    """Operator-authored review question paired with published gold labels."""

    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    question: str = Field(min_length=1)
    query: str = Field(min_length=1)


class SciClaimCaseSpec(BaseModel):
    """One explicitly selected SciClaimEval paper and normalized domain."""

    model_config = ConfigDict(extra="forbid")

    paper_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)


class AdjudicatedRecord(BaseModel):
    """One locally adjudicated case with explicit stage-level evidence."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    question: str = Field(min_length=1)
    query: str = Field(min_length=1)
    works: list[GoldWork] = Field(min_length=1)
    final_relevant_ids: list[str] = Field(default_factory=list)
    claims: list[GoldClaim] = Field(default_factory=list)


class WebisRecord(BaseModel):
    """Normalized subset exported from a pinned Webis-SR4ALL release.

    Reference lists are retrieval relevance judgements, not claims that every
    cited work was included after screening.  Consequently this adapter leaves
    screening and full-text labels unset.
    """

    model_config = ConfigDict(extra="ignore")

    review_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    question: str = Field(min_length=1)
    query: str = Field(min_length=1)
    reference_ids: list[str] = Field(min_length=1)


def _normalise_openalex_id(value: str) -> str:
    return value.removeprefix("https://openalex.org/").strip()


def _normalise_doi(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        normalized = normalized.removeprefix(prefix)
    return normalized


def _source_digest(paths: list[Path]) -> str:
    evidence = {
        str(index): {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for index, path in enumerate(paths)
    }
    return sha256_json(evidence)


def _deterministic_subset(
    values: list[str],
    *,
    limit: int,
    namespace: str,
) -> list[str]:
    if len(values) <= limit:
        return sorted(values)
    return sorted(
        values,
        key=lambda value: hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest(),
    )[:limit]


def build_csmed_ft_suite(
    specs: list[ReviewCaseSpec],
    *,
    source_zip: Path,
    openalex_map: Path,
    suite_id: str,
    version: str,
    year_from: int = 2015,
    year_to: int = 2026,
) -> GoldenSuite:
    """Build exact title/abstract and full-text labels from CSMeD-FT.

    Every CSMeD-FT record already passed title/abstract screening. Multiple
    reports can resolve to one OpenAlex work; an included report therefore
    wins over an excluded companion report at work level.
    """

    if not specs:
        raise ValueError("at least one CSMeD-FT case is required")
    mapping_payload = json.loads(openalex_map.read_text(encoding="utf-8"))
    doi_map = mapping_payload.get("works")
    if not isinstance(doi_map, dict):
        raise ValueError("CSMeD-FT OpenAlex map must contain a works object")
    records: list[dict[str, str]] = []
    metadata: dict[str, Any] = {}
    previous_field_limit = csv.field_size_limit()
    csv.field_size_limit(10_000_000)
    with zipfile.ZipFile(source_zip) as archive:
        try:
            for split in ("train", "dev", "test", "sample"):
                csv_name = f"CSMeD-FT/CSMeD-FT-{split}.csv"
                with archive.open(csv_name) as raw:
                    records.extend(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))
                metadata_name = f"CSMeD-FT/CSMeD-FT-{split}_reviews_metadata.json"
                metadata.update(json.loads(archive.read(metadata_name)))
        finally:
            csv.field_size_limit(previous_field_limit)
    by_review: defaultdict[str, defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    selected = {spec.review_id for spec in specs}
    for record in records:
        review_id = str(record.get("review_id") or "").strip()
        if review_id not in selected:
            continue
        try:
            year = int(float(str(record.get("year") or "")))
        except ValueError:
            continue
        if not year_from <= year <= year_to:
            continue
        doi = _normalise_doi(str(record.get("doi") or ""))
        mapped = doi_map.get(doi)
        if not isinstance(mapped, dict) or not mapped.get("id"):
            raise ValueError(f"CSMeD-FT DOI is not mapped to OpenAlex: {doi!r}")
        work_id = _normalise_openalex_id(str(mapped["id"]))
        decision = str(record.get("decision") or "").strip().casefold()
        if decision not in {"included", "excluded"}:
            raise ValueError(f"unexpected CSMeD-FT decision: {decision!r}")
        by_review[review_id][work_id].append(decision)
    cases: list[GoldenCase] = []
    for spec in specs:
        if spec.review_id not in metadata:
            raise ValueError(f"unknown CSMeD-FT review: {spec.review_id}")
        labels = by_review.get(spec.review_id)
        if not labels:
            raise ValueError(f"CSMeD-FT review has no records in year window: {spec.review_id}")
        works: list[GoldWork] = []
        final_ids: list[str] = []
        for work_id, decisions in sorted(labels.items()):
            included = "included" in decisions
            verdict = GoldVerdict.INCLUDE if included else GoldVerdict.EXCLUDE
            works.append(
                GoldWork(
                    work_id=work_id,
                    retrieval_relevant=included,
                    screening=GoldVerdict.INCLUDE,
                    full_text=verdict,
                )
            )
            if included:
                final_ids.append(work_id)
        cases.append(
            GoldenCase(
                case_id=f"csmed-ft:{spec.review_id}",
                source_name="CSMeD-FT",
                domain=spec.domain,
                question=spec.question,
                query=spec.query,
                works=works,
                final_relevant_ids=final_ids,
            )
        )
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title="CSMeD-FT full-text study-selection benchmark",
        created_at=datetime.now(UTC),
        sources=[
            SuiteSource(
                kind="external",
                name="CSMeD-FT",
                version="1.0.0",
                uri="https://github.com/dli-rs/CSMeD",
                license="CC-BY-4.0",
                license_verified=True,
                sha256=_source_digest([source_zip, openalex_map]),
            )
        ],
        cases=cases,
    )


def build_clef_tar_suite(
    specs: list[ReviewCaseSpec],
    *,
    qrels: Path,
    topic_files: list[Path],
    openalex_map: Path,
    suite_id: str,
    version: str,
    max_works_per_case: int = 250,
    year_from: int = 2015,
    year_to: int = 2026,
) -> GoldenSuite:
    """Build deterministic screening labels from a pinned CLEF-TAR release."""

    if not specs:
        raise ValueError("at least one CLEF-TAR case is required")
    if max_works_per_case < 1:
        raise ValueError("max_works_per_case must be positive")
    mapping = json.loads(openalex_map.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("CLEF-TAR OpenAlex map must be an object keyed by PMID")
    labels_by_review: defaultdict[str, dict[str, int]] = defaultdict(dict)
    for line_number, line in enumerate(qrels.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 4:
            raise ValueError(f"invalid CLEF-TAR qrel on line {line_number}")
        review_id, _, pmid, raw_label = fields
        mapped = mapping.get(pmid)
        if not isinstance(mapped, dict) or not mapped.get("id"):
            continue
        try:
            publication_year = int(mapped.get("publication_year") or 0)
        except (TypeError, ValueError):
            continue
        if not year_from <= publication_year <= year_to:
            continue
        work_id = _normalise_openalex_id(str(mapped["id"]))
        label = 1 if int(raw_label) > 0 else 0
        labels_by_review[review_id][work_id] = max(
            label,
            labels_by_review[review_id].get(work_id, 0),
        )
    topic_ids = {path.stem for path in topic_files}
    cases: list[GoldenCase] = []
    for spec in specs:
        if spec.review_id not in topic_ids:
            raise ValueError(f"missing CLEF-TAR topic file for {spec.review_id}")
        labels = labels_by_review.get(spec.review_id)
        if not labels:
            raise ValueError(f"CLEF-TAR review has no mapped labels: {spec.review_id}")
        included = sorted(work_id for work_id, label in labels.items() if label == 1)
        if len(included) > max_works_per_case:
            raise ValueError("case limit would discard relevant CLEF-TAR labels")
        negatives = [work_id for work_id, label in labels.items() if label == 0]
        sampled_negatives = _deterministic_subset(
            negatives,
            limit=max_works_per_case - len(included),
            namespace=spec.review_id,
        )
        selected_labels = {
            **{item: 0 for item in sampled_negatives},
            **{item: 1 for item in included},
        }
        works = [
            GoldWork(
                work_id=work_id,
                retrieval_relevant=bool(label),
                screening=GoldVerdict.INCLUDE if label else GoldVerdict.EXCLUDE,
            )
            for work_id, label in sorted(selected_labels.items())
        ]
        cases.append(
            GoldenCase(
                case_id=f"clef-tar:{spec.review_id}",
                source_name="CLEF-TAR-2019",
                domain=spec.domain,
                question=spec.question,
                query=spec.query,
                works=works,
                final_relevant_ids=included,
            )
        )
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title="CLEF-TAR 2019 intervention screening benchmark",
        created_at=datetime.now(UTC),
        sources=[
            SuiteSource(
                kind="external",
                name="CLEF-TAR-2019",
                version="2019-test-intervention",
                uri="https://github.com/CLEF-TAR/tar/tree/master/2019-TAR",
                license="MIT",
                license_verified=True,
                sha256=_source_digest([qrels, openalex_map, *sorted(topic_files)]),
            )
        ],
        cases=cases,
    )


def build_sciclaimeval_suite(
    specs: list[SciClaimCaseSpec],
    *,
    source_json: Path,
    dataset_root: Path,
    openalex_map: Path,
    suite_id: str,
    version: str,
) -> GoldenSuite:
    """Build claim-support evidence from explicitly CC-BY-4.0 samples."""

    if not specs:
        raise ValueError("at least one SciClaimEval case is required")
    records = json.loads(source_json.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("SciClaimEval task export must be a list")
    mapping = json.loads(openalex_map.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("SciClaimEval OpenAlex map must be an object")
    selected = {spec.paper_id: spec for spec in specs}
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    paper_paths: dict[str, Path] = {}
    for raw in records:
        if not isinstance(raw, dict):
            continue
        paper_id = str(raw.get("paper_id") or "")
        if paper_id not in selected or raw.get("label") != "Supported":
            continue
        license_name = str(raw.get("license_name") or "").strip()
        license_url = str(raw.get("license_url") or "").strip().lower()
        if license_name != "CC BY 4.0" or "creativecommons.org/licenses/by/4.0" not in license_url:
            raise ValueError(f"SciClaimEval sample lacks explicit CC-BY-4.0 license: {paper_id}")
        claim = str(raw.get("claim") or "").strip()
        claim_id = str(raw.get("claim_id") or "").strip()
        if not claim or not claim_id:
            raise ValueError(f"SciClaimEval sample lacks claim text or id: {paper_id}")
        relative_paper_path = Path(str(raw.get("paper_path") or ""))
        paper_path = dataset_root / relative_paper_path
        if not paper_path.is_file():
            paper_path = source_json.parent / relative_paper_path
        if not paper_path.is_file():
            raise ValueError(f"SciClaimEval paper file is missing: {paper_path}")
        paper_paths[paper_id] = paper_path
        grouped[paper_id].append(raw)
    cases: list[GoldenCase] = []
    for spec in specs:
        paper_records = grouped.get(spec.paper_id)
        if not paper_records:
            raise ValueError(f"SciClaimEval paper has no supported claims: {spec.paper_id}")
        mapped = mapping.get(spec.paper_id)
        work = mapped.get("work") if isinstance(mapped, dict) else None
        if not isinstance(work, dict) or not work.get("id"):
            raise ValueError(f"SciClaimEval paper is not mapped to OpenAlex: {spec.paper_id}")
        work_id = _normalise_openalex_id(str(work["id"]))
        paper = json.loads(paper_paths[spec.paper_id].read_text(encoding="utf-8"))
        title = str(paper.get("title") or "").removesuffix(" [PeerJ]").strip()
        if not title:
            raise ValueError(f"SciClaimEval paper title is missing: {spec.paper_id}")
        claims = [
            GoldClaim(
                claim_id=f"sciclaimeval:{spec.paper_id}:{raw['claim_id']}",
                text=str(raw["claim"]).strip(),
                supported_by=[work_id],
            )
            for raw in sorted(paper_records, key=lambda item: str(item["claim_id"]))
        ]
        cases.append(
            GoldenCase(
                case_id=f"sciclaimeval:{spec.paper_id}",
                source_name="SciClaimEval",
                domain=spec.domain,
                question=f"Which empirical claims are supported by {title}?",
                query=title,
                works=[GoldWork(work_id=work_id, retrieval_relevant=True)],
                final_relevant_ids=[work_id],
                claims=claims,
            )
        )
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title="SciClaimEval citation-support benchmark",
        created_at=datetime.now(UTC),
        sources=[
            SuiteSource(
                kind="external",
                name="SciClaimEval",
                version="efb3807399acec43854fdf7741c1bcfe605a72b9",
                uri="https://huggingface.co/datasets/SciClaimEval/SciClaimEval",
                license="CC-BY-4.0 (explicitly verified samples only)",
                license_verified=True,
                sha256=_source_digest([source_json, openalex_map, *sorted(paper_paths.values())]),
            )
        ],
        cases=cases,
    )


def build_synergy_suite(
    specs: list[SynergyCaseSpec],
    *,
    source_dir: Path,
    suite_id: str,
    version: str,
) -> GoldenSuite:
    """Build a multi-review suite from a locally pinned SYNERGY release.

    SYNERGY's ``label_included`` is the published review's final inclusion
    decision.  It is exact full-text/final-ranking evidence, but not an exact
    title/abstract-stage label, so ``screening`` intentionally remains unset.
    """

    try:
        from synergy_dataset.base import Dataset
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("synergy-dataset not installed; run `uv sync --group eval`") from exc
    if not specs:
        raise ValueError("at least one SYNERGY case is required")
    cases: list[GoldenCase] = []
    source_evidence: dict[str, Any] = {}
    for spec in specs:
        records = Dataset(spec.dataset, path=source_dir).to_dict()
        if not records:
            raise ValueError(f"SYNERGY dataset {spec.dataset!r} is empty")
        works: list[GoldWork] = []
        final_ids: list[str] = []
        canonical_records: list[dict[str, Any]] = []
        for raw_id, record in sorted(records.items()):
            work_id = _normalise_openalex_id(str(raw_id))
            included = int(record.get("label_included") or 0) == 1
            works.append(
                GoldWork(
                    work_id=work_id,
                    retrieval_relevant=included,
                    screening=None,
                    # A negative final label does not reveal whether exclusion
                    # happened at title/abstract or full text. Only positives
                    # are safe full-text inclusion judgements.
                    full_text=GoldVerdict.INCLUDE if included else None,
                )
            )
            if included:
                final_ids.append(work_id)
            canonical_records.append({"work_id": work_id, "included": included})
        source_evidence[spec.dataset] = canonical_records
        cases.append(
            GoldenCase(
                case_id=f"synergy:{spec.dataset}",
                source_name="SYNERGY",
                domain=spec.domain,
                question=spec.question,
                query=spec.query,
                works=works,
                final_relevant_ids=final_ids,
            )
        )
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title="SYNERGY study-selection benchmark",
        created_at=datetime.now(UTC),
        sources=[
            SuiteSource(
                kind="external",
                name="SYNERGY",
                version="1",
                uri="https://doi.org/10.34894/HE6NAQ",
                license="CC0-1.0",
                license_verified=True,
                sha256=sha256_json(source_evidence),
            )
        ],
        cases=cases,
    )


def load_webis_jsonl(
    path: Path,
    *,
    suite_id: str,
    version: str,
    source_uri: str,
    license_name: str,
    license_verified: bool = False,
    limit: int | None = None,
) -> GoldenSuite:
    """Load a normalized, locally pinned Webis-SR4ALL JSONL export."""

    raw = path.read_bytes()
    cases: list[GoldenCase] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = WebisRecord.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"invalid Webis record on line {line_number}: {exc}") from exc
        ids = list(dict.fromkeys(_normalise_openalex_id(item) for item in record.reference_ids))
        cases.append(
            GoldenCase(
                case_id=f"webis:{record.review_id}",
                source_name="Webis-SR4ALL-26",
                domain=record.domain,
                question=record.question,
                query=record.query,
                works=[GoldWork(work_id=item, retrieval_relevant=True) for item in ids],
                final_relevant_ids=ids,
            )
        )
        if limit is not None and len(cases) >= limit:
            break
    if not cases:
        raise ValueError("Webis export contains no usable cases")
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title="Webis-SR4ALL-26 cross-disciplinary retrieval benchmark",
        created_at=datetime.now(UTC),
        sources=[
            SuiteSource(
                kind="external",
                name="Webis-SR4ALL-26",
                version=version,
                uri=source_uri,
                license=license_name,
                license_verified=license_verified,
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        ],
        cases=cases,
    )


def load_adjudicated_jsonl(
    path: Path,
    *,
    suite_id: str,
    version: str,
    source_name: str,
    source_uri: str,
) -> GoldenSuite:
    """Load human-adjudicated stage, citation, and ranking judgements.

    This adapter is intentionally strict and never upgrades model output into
    gold. The input URI should point to the review protocol or immutable
    adjudication record that explains who labelled the cases and how conflicts
    were resolved.
    """

    raw = path.read_bytes()
    cases: list[GoldenCase] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = AdjudicatedRecord.model_validate_json(line)
            cases.append(
                GoldenCase(
                    case_id=record.case_id,
                    source_name=source_name,
                    domain=record.domain,
                    question=record.question,
                    query=record.query,
                    works=record.works,
                    final_relevant_ids=record.final_relevant_ids,
                    claims=record.claims,
                )
            )
        except ValueError as exc:
            raise ValueError(f"invalid adjudicated record on line {line_number}: {exc}") from exc
    if not cases:
        raise ValueError("adjudicated export contains no usable cases")
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title="SixSentences human-adjudicated review benchmark",
        created_at=datetime.now(UTC),
        sources=[
            SuiteSource(
                kind="internal_adjudicated",
                name=source_name,
                version=version,
                uri=source_uri,
                license="Owned internal evaluation evidence",
                license_verified=True,
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        ],
        cases=cases,
    )


def merge_suites(
    suites: list[GoldenSuite],
    *,
    suite_id: str,
    version: str,
    title: str,
) -> GoldenSuite:
    """Merge independently pinned sources without losing their provenance."""

    if not suites:
        raise ValueError("at least one suite is required")
    sources = [source for suite in suites for source in suite.sources]
    cases = [case for suite in suites for case in suite.cases]
    return GoldenSuite(
        suite_id=suite_id,
        version=version,
        title=title,
        created_at=datetime.now(UTC),
        sources=sources,
        cases=cases,
    )


def write_suite(path: Path, suite: GoldenSuite) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
