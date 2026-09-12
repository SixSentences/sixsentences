"""Stratified OpenAlex ingestion into immutable, release-gated snapshots."""

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from sixsentences_server.connectors.openalex import OpenAlexClient
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.duckdb_store import META_FILE, WORKS_FILE, DuckDBCorpus
from sixsentences_server.corpus.manifest import (
    CorpusBuildPlan,
    CorpusCoverage,
    CorpusManifest,
    CorpusQualityGates,
    CorpusSlice,
    evaluate_corpus_gates,
)
from sixsentences_server.ops.status import require_storage_capacity

WORKS_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("doi", pa.string()),
        ("title", pa.string()),
        ("abstract", pa.string()),
        ("year", pa.int32()),
        ("venue", pa.string()),
        ("authors", pa.string()),  # JSON-encoded list
        ("cited_by_count", pa.int64()),
        ("is_retracted", pa.bool_()),
        ("source", pa.string()),
        ("referenced_works", pa.string()),  # JSON-encoded list of OpenAlex ids
        ("open_access", pa.string()),  # JSON-encoded OA location (drives acquisition)
        ("work_type", pa.string()),  # OpenAlex type (article/review/preprint/...)
        ("corpus_slice", pa.string()),  # provenance for coverage and drift reports
    ]
)

# OA fields carried on WorkRecord and serialized into the open_access column.
_OA_FIELDS = (
    "oa_status",
    "oa_url",
    "pdf_url",
    "oa_landing_url",
    "oa_license",
    "oa_version",
    "arxiv_id",
    "pmcid",
)

# OpenAlex field id for Computer Science
CS_FIELD_FILTER = "primary_topic.field.id:fields/17,has_abstract:true"
WRITE_BATCH_SIZE = 5_000
MIN_ESTIMATED_BYTES_PER_WORK = 4_096
SNAPSHOT_BUILD_OVERHEAD = 1.15


class CorpusSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class CorpusSyncResult:
    """Descriptor for a fully written, structurally gated generation."""

    works: int
    generation: str
    version: str
    activated: bool


def estimate_snapshot_bytes(corpus: DuckDBCorpus, target_works: int) -> int:
    """Conservatively forecast space needed for one staged generation.

    A large ingest must fail before downloading hundreds of thousands of
    records, not after the filesystem is nearly full.  Existing snapshots are
    used only when their observed density is higher than the conservative
    floor, because small fixtures compress unlike real abstracts.
    """

    bytes_per_work = float(MIN_ESTIMATED_BYTES_PER_WORK)
    if corpus.exists():
        info = corpus.info()
        works = int(info.get("works") or 0)
        if works > 0:
            bytes_per_work = max(bytes_per_work, int(info["bytes"]) / works)
    return int(target_works * bytes_per_work * SNAPSHOT_BUILD_OVERHEAD)


def _work_row(work: WorkRecord, *, corpus_slice: str) -> dict[str, object]:
    return {
        "id": work.id,
        "doi": work.doi,
        "title": work.title,
        "abstract": work.abstract,
        "year": work.year,
        "venue": work.venue,
        "authors": json.dumps(work.authors),
        "cited_by_count": work.cited_by_count,
        "is_retracted": work.is_retracted,
        "source": "openalex",
        "referenced_works": json.dumps(work.referenced_works),
        "open_access": json.dumps({field: getattr(work, field) for field in _OA_FIELDS}),
        "work_type": work.work_type,
        "corpus_slice": corpus_slice,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_snapshot(path: Path, expected_rows: int) -> None:
    schema = pq.read_schema(path)
    if not schema.equals(WORKS_SCHEMA, check_metadata=False):
        raise CorpusSyncError("new corpus has an unexpected Parquet schema")
    metadata = pq.ParquetFile(path).metadata
    if metadata is None or metadata.num_rows != expected_rows:
        raise CorpusSyncError("new corpus row count does not match the ingest ledger")
    with duckdb.connect() as connection:
        row = connection.execute(
            "SELECT count(*) FROM read_parquet(?)",
            [str(path)],
        ).fetchone()
    if row is None or int(row[0]) != expected_rows:
        raise CorpusSyncError("DuckDB could not read the complete new corpus")


def _snapshot_coverage(
    path: Path,
    *,
    plan: CorpusBuildPlan,
    fetched_rows: int,
    duplicate_rows: int,
) -> CorpusCoverage:
    """Compute release evidence from the written artifact, never RAM counters."""

    with duckdb.connect() as connection:
        row = connection.execute(
            """
            SELECT
                count(*) AS unique_works,
                count(*) FILTER (WHERE trim(coalesce(title, '')) <> '') AS title_works,
                count(*) FILTER (WHERE trim(coalesce(abstract, '')) <> '') AS abstract_works,
                count(*) FILTER (WHERE trim(coalesce(doi, '')) <> '') AS doi_works,
                count(*) FILTER (
                    WHERE coalesce(json_extract_string(open_access, '$.oa_url'), '') <> ''
                       OR coalesce(json_extract_string(open_access, '$.pdf_url'), '') <> ''
                ) AS open_access_works,
                count(*) FILTER (WHERE is_retracted) AS retracted_works,
                min(year) AS year_min,
                max(year) AS year_max
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
        if row is None:
            raise CorpusSyncError("could not calculate corpus coverage")
        slice_rows = connection.execute(
            "SELECT corpus_slice, count(*) FROM read_parquet(?) GROUP BY corpus_slice",
            [str(path)],
        ).fetchall()
        type_rows = connection.execute(
            """
            SELECT coalesce(nullif(work_type, ''), 'unknown'), count(*)
            FROM read_parquet(?) GROUP BY 1
            """,
            [str(path)],
        ).fetchall()
        out_of_range = 0
        for item in plan.slices:
            start = item.from_year if item.from_year is not None else plan.from_year
            end = item.to_year if item.to_year is not None else plan.to_year
            invalid_row = connection.execute(
                """
                SELECT count(*) FROM read_parquet(?)
                WHERE corpus_slice = ?
                  AND (year IS NULL OR year < ? OR year > ?)
                """,
                [str(path), item.id, start, end],
            ).fetchone()
            out_of_range += int(invalid_row[0]) if invalid_row else 0
    return CorpusCoverage(
        fetched_rows=fetched_rows,
        unique_works=int(row[0]),
        duplicate_rows=duplicate_rows,
        title_works=int(row[1]),
        abstract_works=int(row[2]),
        doi_works=int(row[3]),
        open_access_works=int(row[4]),
        retracted_works=int(row[5]),
        out_of_range_works=out_of_range,
        year_min=int(row[6]) if row[6] is not None else None,
        year_max=int(row[7]) if row[7] is not None else None,
        by_slice={str(label): int(count) for label, count in slice_rows},
        by_work_type={str(label): int(count) for label, count in type_rows},
    )


def _remove_partial_snapshot(path: Path) -> None:
    if not path.is_dir() or path.is_symlink() or not path.name.startswith("."):
        return
    for name in (WORKS_FILE, META_FILE):
        (path / name).unlink(missing_ok=True)
    path.rmdir()


def sync_corpus(
    corpus: DuckDBCorpus,
    client: OpenAlexClient,
    *,
    plan: CorpusBuildPlan,
    progress: Callable[[int], None] | None = None,
    reserve_bytes: int = 0,
    snapshots_to_keep: int = 2,
    activate: bool = True,
) -> CorpusSyncResult:
    """Build all plan slices and optionally atomically activate the result.

    Production releases should set ``activate=False`` and promote the staged
    generation only after the external quality and economics gates pass.
    """

    corpus.corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus.snapshots_dir.mkdir(parents=True, exist_ok=True)
    require_storage_capacity(
        corpus.corpus_dir,
        reserve_bytes,
        estimate_snapshot_bytes(corpus, plan.target_works),
    )
    created = datetime.now(UTC)
    generation = f"{created.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
    partial = corpus.snapshots_dir / f".{generation}.partial"
    final = corpus.snapshots_dir / generation
    partial.mkdir()
    works_path = partial / WORKS_FILE
    meta_path = partial / META_FILE
    writer: pq.ParquetWriter | None = None
    rows: list[dict[str, object]] = []
    count = 0
    fetched = 0
    duplicates = 0
    seen_ids: set[str] = set()
    try:
        source_descriptors: dict[str, str] = {}
        for corpus_slice in plan.slices:
            slice_from = corpus_slice.from_year or plan.from_year
            slice_to = corpus_slice.to_year or plan.to_year
            full_filter = (
                f"{corpus_slice.oa_filter},"
                f"from_publication_date:{slice_from}-01-01,"
                f"to_publication_date:{slice_to}-12-31"
            )
            source_descriptors[f"openalex:{corpus_slice.id}"] = (
                f"filter={full_filter}; sort={plan.sort}; target={corpus_slice.target_works}"
            )
            for work in client.iter_works(
                full_filter,
                limit=corpus_slice.target_works,
                sort=plan.sort,
            ):
                fetched += 1
                if work.id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(work.id)
                rows.append(_work_row(work, corpus_slice=corpus_slice.id))
                count += 1
                if progress and count % 1_000 == 0:
                    progress(count)
                if len(rows) < WRITE_BATCH_SIZE:
                    continue
                table = pa.Table.from_pylist(rows, schema=WORKS_SCHEMA)
                require_storage_capacity(corpus.corpus_dir, reserve_bytes, table.nbytes)
                if writer is None:
                    writer = pq.ParquetWriter(works_path, WORKS_SCHEMA, compression="zstd")
                writer.write_table(table)
                rows.clear()
        if rows or writer is None:
            table = pa.Table.from_pylist(rows, schema=WORKS_SCHEMA)
            require_storage_capacity(corpus.corpus_dir, reserve_bytes, table.nbytes)
            if writer is None:
                writer = pq.ParquetWriter(works_path, WORKS_SCHEMA, compression="zstd")
            writer.write_table(table)
        writer.close()
        writer = None
        _validate_snapshot(works_path, count)

        coverage = _snapshot_coverage(
            works_path,
            plan=plan,
            fetched_rows=fetched,
            duplicate_rows=duplicates,
        )
        gates = evaluate_corpus_gates(plan, coverage)
        if not gates.passed:
            raise CorpusSyncError("new corpus failed release gates: " + ", ".join(gates.failures))

        stamp = created.strftime("%Y-%m-%d")
        version = f"{plan.profile}-{stamp}-openalex-{count}"
        sources = {
            name: f"{descriptor}; fetched={stamp}"
            for name, descriptor in source_descriptors.items()
        }
        digest = _sha256(works_path)
        relative = final.relative_to(corpus.corpus_dir)
        manifest = CorpusManifest(
            version=version,
            works=count,
            sources=sources,
            created_at=created,
            sha256=digest,
            bytes=works_path.stat().st_size,
            snapshot=str(relative),
            works_file=str(relative / WORKS_FILE),
            meta_file=str(relative / META_FILE),
            build=plan,
            build_digest=plan.digest,
            coverage=coverage,
            gates=gates,
        )
        meta_path.write_text(manifest.stable_json(), encoding="utf-8")
        for path in (works_path, meta_path):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        directory_fd = os.open(partial, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        partial.replace(final)

        if activate:
            corpus.activate_snapshot(
                manifest.model_dump(mode="json"),
                snapshots_to_keep=snapshots_to_keep,
            )
        return CorpusSyncResult(
            works=count,
            generation=generation,
            version=version,
            activated=activate,
        )
    except Exception:
        if writer is not None:
            writer.close()
        _remove_partial_snapshot(partial)
        raise


def sync_micro_corpus(
    corpus: DuckDBCorpus,
    client: OpenAlexClient,
    *,
    limit: int = 20_000,
    oa_filter: str = CS_FIELD_FILTER,
    from_year: int = 2015,
    to_year: int = 2100,
    progress: Callable[[int], None] | None = None,
    reserve_bytes: int = 0,
    snapshots_to_keep: int = 2,
    activate: bool = True,
) -> CorpusSyncResult:
    """Backward-compatible single-slice development corpus build.

    Development clients and unit-test stubs may legitimately return fewer than
    the requested limit, so target and per-slice completeness are disabled for
    this profile. Integrity, uniqueness, title, and abstract gates remain on.
    """

    plan = CorpusBuildPlan(
        profile="micro-cs",
        from_year=from_year,
        to_year=to_year,
        slices=(
            CorpusSlice(
                id="computer-science",
                label="Computer Science",
                oa_filter=oa_filter,
                target_works=limit,
            ),
        ),
        quality_gates=CorpusQualityGates(
            minimum_target_ratio=0.0,
            minimum_slice_ratio=0.0,
            minimum_abstract_ratio=0.0,
        ),
    )
    return sync_corpus(
        corpus,
        client,
        plan=plan,
        progress=progress,
        reserve_bytes=reserve_bytes,
        snapshots_to_keep=snapshots_to_keep,
        activate=activate,
    )
