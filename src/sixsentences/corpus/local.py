"""Small, immutable DuckDB/Parquet corpus for local and embedded use."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from sixsentences.core.models import WorkRecord, utcnow
from sixsentences.corpus.store import CorpusVersion
from sixsentences.querylang.ast import Node
from sixsentences.querylang.compile_duckdb import compile_duckdb
from sixsentences.querylang.parser import parse_query

WORKS_FILE: Final = "works.parquet"
MANIFEST_FILE: Final = "manifest.json"
SCHEMA_VERSION: Final = 1

_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "doi",
    "title",
    "abstract",
    "year",
    "venue",
    "authors",
    "cited_by_count",
    "is_retracted",
    "referenced_works",
    "work_type",
    "source",
    "oa_status",
    "oa_url",
    "pdf_url",
    "oa_landing_url",
    "oa_license",
    "oa_version",
    "arxiv_id",
    "pmid",
    "pmcid",
    "publication_date",
    "volume",
    "issue",
    "pages",
    "publisher",
    "language",
    "issn",
    "oa_locations",
)


class CorpusError(RuntimeError):
    """Raised when a local corpus is missing, corrupt, or inconsistent."""


class CorpusManifest(BaseModel):
    """Checksummed metadata for one local snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=utcnow)
    source: str
    works: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def identifier(self) -> str:
        """Return a compact content-derived snapshot identifier."""

        return f"local-{self.sha256[:16]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_list(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _record_row(record: WorkRecord) -> tuple[object, ...]:
    values = record.model_dump(mode="json")
    values["authors"] = _encode_list(values["authors"])
    values["referenced_works"] = _encode_list(values["referenced_works"])
    values["oa_locations"] = _encode_list(values["oa_locations"])
    return tuple(values[column] for column in _COLUMNS)


def build_local_corpus(
    records: Iterable[WorkRecord],
    corpus_dir: Path,
    *,
    source: str = "local-jsonl",
) -> CorpusManifest:
    """Write records as a deterministic, checksummed Parquet snapshot.

    IDs must be unique. Files are replaced atomically inside ``corpus_dir``;
    interrupted builds cannot be mistaken for a verified snapshot.
    """

    materialized = sorted(records, key=lambda record: record.id)
    ids = [record.id for record in materialized]
    if any(not item for item in ids):
        raise ValueError("work ids must not be empty")
    if len(ids) != len(set(ids)):
        raise ValueError("work ids must be unique")

    corpus_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    temporary_works = corpus_dir / f".{WORKS_FILE}.{token}.tmp"
    temporary_manifest = corpus_dir / f".{MANIFEST_FILE}.{token}.tmp"
    works_path = corpus_dir / WORKS_FILE
    manifest_path = corpus_dir / MANIFEST_FILE

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE works (
                id VARCHAR NOT NULL,
                doi VARCHAR,
                title VARCHAR NOT NULL,
                abstract VARCHAR,
                year INTEGER,
                venue VARCHAR,
                authors VARCHAR NOT NULL,
                cited_by_count BIGINT NOT NULL,
                is_retracted BOOLEAN NOT NULL,
                referenced_works VARCHAR NOT NULL,
                work_type VARCHAR,
                source VARCHAR NOT NULL,
                oa_status VARCHAR,
                oa_url VARCHAR,
                pdf_url VARCHAR,
                oa_landing_url VARCHAR,
                oa_license VARCHAR,
                oa_version VARCHAR,
                arxiv_id VARCHAR,
                pmid VARCHAR,
                pmcid VARCHAR,
                publication_date VARCHAR,
                volume VARCHAR,
                issue VARCHAR,
                pages VARCHAR,
                publisher VARCHAR,
                language VARCHAR,
                issn VARCHAR,
                oa_locations VARCHAR NOT NULL
            )
            """
        )
        if materialized:
            placeholders = ", ".join("?" for _ in _COLUMNS)
            connection.executemany(
                f"INSERT INTO works VALUES ({placeholders})",  # noqa: S608 - fixed count only
                [_record_row(record) for record in materialized],
            )
        connection.table("works").order("id").write_parquet(
            str(temporary_works), compression="zstd"
        )
        digest = _sha256(temporary_works)
        manifest = CorpusManifest(source=source, works=len(materialized), sha256=digest)
        temporary_manifest.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_works.replace(works_path)
        temporary_manifest.replace(manifest_path)
        return manifest
    finally:
        connection.close()
        temporary_works.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)


def _decode_json_list(value: str, *, field: str) -> list[Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"invalid JSON in corpus field {field}") from exc
    if not isinstance(decoded, list):
        raise CorpusError(f"corpus field {field} must contain a JSON list")
    return decoded


def _row_to_record(row: Sequence[object]) -> WorkRecord:
    values = dict(zip(_COLUMNS, row, strict=True))
    values["authors"] = _decode_json_list(str(values["authors"]), field="authors")
    values["referenced_works"] = _decode_json_list(
        str(values["referenced_works"]), field="referenced_works"
    )
    values["oa_locations"] = _decode_json_list(str(values["oa_locations"]), field="oa_locations")
    return WorkRecord.model_validate(values)


class LocalCorpus:
    """Read and search a checksummed corpus snapshot without a service."""

    def __init__(self, corpus_dir: Path) -> None:
        self.corpus_dir = corpus_dir
        self.works_path = corpus_dir / WORKS_FILE
        self.manifest_path = corpus_dir / MANIFEST_FILE

    def _manifest(self) -> CorpusManifest:
        if not self.manifest_path.is_file() or not self.works_path.is_file():
            raise CorpusError(f"no local corpus found at {self.corpus_dir}")
        try:
            return CorpusManifest.model_validate_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise CorpusError("invalid corpus manifest") from exc

    def verify(self) -> CorpusManifest:
        """Validate the manifest, checksum, schema, and work count."""

        manifest = self._manifest()
        if manifest.schema_version != SCHEMA_VERSION:
            raise CorpusError(
                f"unsupported corpus schema {manifest.schema_version}; expected {SCHEMA_VERSION}"
            )
        if _sha256(self.works_path) != manifest.sha256:
            raise CorpusError("corpus checksum does not match its manifest")
        if self.count() != manifest.works:
            raise CorpusError("corpus work count does not match its manifest")
        return manifest

    def version(self) -> CorpusVersion:
        """Return the content identity after verification."""

        manifest = self.verify()
        return CorpusVersion(manifest.identifier, manifest.works, manifest.sha256)

    def count(self) -> int:
        """Return the number of works in the snapshot."""

        if not self.works_path.is_file():
            raise CorpusError(f"no local corpus found at {self.corpus_dir}")
        connection = duckdb.connect(":memory:")
        try:
            row = connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(self.works_path)]
            ).fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            connection.close()

    def search(self, query: Node | str, limit: int = 10_000) -> list[WorkRecord]:
        """Search title and abstract with a parameterized boolean query."""

        if limit < 1:
            return []
        node = parse_query(query) if isinstance(query, str) else query
        condition, parameters = compile_duckdb(node)
        columns = ", ".join(_COLUMNS)
        statement = (
            f"SELECT {columns} FROM read_parquet(?) WHERE {condition} "
            "ORDER BY cited_by_count DESC, id ASC LIMIT ?"
        )
        connection = duckdb.connect(":memory:")
        try:
            rows = connection.execute(
                statement,
                [str(self.works_path), *parameters, limit],
            ).fetchall()
        finally:
            connection.close()
        return [_row_to_record(row) for row in rows]

    def by_ids(self, ids: list[str]) -> list[WorkRecord]:
        """Resolve records by canonical id, preserving caller order."""

        unique_ids = list(dict.fromkeys(item for item in ids if item))
        if not unique_ids:
            return []
        placeholders = ", ".join("?" for _ in unique_ids)
        columns = ", ".join(_COLUMNS)
        statement = f"SELECT {columns} FROM read_parquet(?) WHERE id IN ({placeholders})"
        connection = duckdb.connect(":memory:")
        try:
            rows = connection.execute(statement, [str(self.works_path), *unique_ids]).fetchall()
        finally:
            connection.close()
        records = {_record.id: _record for _record in map(_row_to_record, rows)}
        return [records[item] for item in unique_ids if item in records]
