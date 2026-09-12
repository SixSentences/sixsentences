"""Versioned local corpus: immutable Parquet snapshots queried through DuckDB.

Layout under <data_dir>/corpus/:
    current.json                       atomic active-snapshot pointer
    snapshots/<generation>/works.parquet
    snapshots/<generation>/meta.json

The original root-level ``works.parquet`` and ``meta.json`` remain readable
for local fixtures and installations that have not run a new sync yet.

Boolean search runs entirely offline against the parquet file — no network,
no rate limits, arbitrarily long queries. This is the architectural proof for
the full corpus platform.
"""

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.corpus.manifest import CORPUS_MANIFEST_SCHEMA, CorpusManifest
from sixsentences_server.corpus.store import CorpusVersion
from sixsentences_server.querylang.ast import And, Field_, Node, Not, Or, Term
from sixsentences_server.querylang.compile_duckdb import compile_duckdb

META_FILE = "meta.json"
WORKS_FILE = "works.parquet"
CURRENT_FILE = "current.json"
SNAPSHOTS_DIR = "snapshots"
LOCK_FILE = ".corpus.lock"
RELEASE_FILE = "release.json"

_RELAXED_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _relaxed_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 3 and token not in _RELAXED_STOPWORDS
    ]


def _relaxed_query_condition(node: Node) -> tuple[str, list[str]]:
    """Compile a recall-oriented companion to the strict Boolean query.

    Logical concept groups stay intact, while quoted phrases may match any
    significant constituent token. This recovers records whose title uses a
    close formulation (for example ``disability grant`` for ``social grant``)
    without turning an AND-structured review query into an unrestricted bag
    of words.
    """

    match node:
        case Term(text=text, field=field, phrase=phrase):
            if field is Field_.TITLE:
                haystack = "lower(coalesce(title, ''))"
            elif field is Field_.ABSTRACT:
                haystack = "lower(coalesce(abstract, ''))"
            else:
                haystack = "lower(coalesce(title, '') || ' ' || coalesce(abstract, ''))"
            normalized = " ".join(text.casefold().split())
            needles = _relaxed_tokens(normalized) if phrase else [normalized]
            needles = [needle for needle in needles if needle]
            if not needles:
                return "FALSE", []
            return (
                "(" + " OR ".join(f"contains({haystack}, ?)" for _ in needles) + ")",
                needles,
            )
        case And(children=children):
            compiled = [_relaxed_query_condition(child) for child in children]
            return (
                "(" + " AND ".join(condition for condition, _ in compiled) + ")",
                [param for _, params in compiled for param in params],
            )
        case Or(children=children):
            compiled = [_relaxed_query_condition(child) for child in children]
            return (
                "(" + " OR ".join(condition for condition, _ in compiled) + ")",
                [param for _, params in compiled for param in params],
            )
        case Not(child=child):
            condition, params = _relaxed_query_condition(child)
            return f"(NOT {condition})", params
    raise TypeError(f"unknown query node: {node!r}")


def _positive_query_terms(node: Node, *, negated: bool = False) -> list[Term]:
    match node:
        case Term() as term:
            return [] if negated else [term]
        case And(children=children) | Or(children=children):
            return [
                term for child in children for term in _positive_query_terms(child, negated=negated)
            ]
        case Not(child=child):
            return _positive_query_terms(child, negated=not negated)
    return []


class CorpusNotSyncedError(RuntimeError):
    pass


class CorpusManifestError(RuntimeError):
    pass


class DuckDBCorpus:
    def __init__(self, corpus_dir: Path, *, generation: str | None = None) -> None:
        self.corpus_dir = corpus_dir
        if generation is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", generation
        ):
            raise CorpusManifestError("unsafe corpus generation name")
        self.generation = generation

    @property
    def current_path(self) -> Path:
        return self.corpus_dir / CURRENT_FILE

    @property
    def snapshots_dir(self) -> Path:
        return self.corpus_dir / SNAPSHOTS_DIR

    def _manifest(self) -> dict[str, Any] | None:
        if self.generation is not None:
            path = self.snapshots_dir / self.generation / META_FILE
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CorpusManifestError("pinned corpus manifest is unreadable") from exc
            if not isinstance(manifest, dict):
                raise CorpusManifestError("pinned corpus manifest is not an object")
            return manifest
        if not self.current_path.is_file():
            return None
        try:
            manifest = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CorpusManifestError("active corpus manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise CorpusManifestError("active corpus manifest is not an object")
        return manifest

    def _manifest_path(self, key: str, legacy_name: str) -> Path:
        if self.generation is not None:
            return self.snapshots_dir / self.generation / legacy_name
        manifest = self._manifest()
        if manifest is None:
            return self.corpus_dir / legacy_name
        value = manifest.get(key)
        if not isinstance(value, str):
            raise CorpusManifestError(f"active corpus manifest has no {key}")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise CorpusManifestError(f"active corpus manifest has an unsafe {key}")
        return self.corpus_dir / relative

    @property
    def works_path(self) -> Path:
        return self._manifest_path("works_file", WORKS_FILE)

    @property
    def meta_path(self) -> Path:
        return self._manifest_path("meta_file", META_FILE)

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        with (self.corpus_dir / LOCK_FILE).open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def snapshot_path(self) -> Iterator[Path]:
        """Pin one immutable generation for the complete DuckDB query."""

        with self._lock(exclusive=False):
            self._require_unlocked()
            yield self.works_path

    # -- metadata ---------------------------------------------------------

    def exists(self) -> bool:
        with self._lock(exclusive=False):
            return self.works_path.is_file() and self.meta_path.is_file()

    def _require_unlocked(self) -> None:
        if not self.works_path.is_file() or not self.meta_path.is_file():
            raise CorpusNotSyncedError(
                f"no corpus at {self.corpus_dir}; run `six corpus sync` first"
            )

    def version(self) -> CorpusVersion:
        info = self.info()
        return CorpusVersion(
            version=info["version"],
            works=info["works"],
            sources=info["sources"],
        )

    def info(self) -> dict[str, Any]:
        """Describe the active generation for health and capacity planning."""

        with self._lock(exclusive=False):
            self._require_unlocked()
            meta = self._manifest()
            if meta is None:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            snapshots = (
                len(
                    [
                        path
                        for path in self.snapshots_dir.iterdir()
                        if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
                    ]
                )
                if self.snapshots_dir.is_dir()
                else 1
            )
            details = {
                "version": str(meta["version"]),
                "works": int(meta["works"]),
                "sources": dict(meta["sources"]),
                "bytes": self.works_path.stat().st_size,
                "sha256": meta.get("sha256"),
                "created_at": meta.get("created_at"),
                "snapshots": snapshots,
                "release_approved": (self.works_path.parent / RELEASE_FILE).is_file(),
            }
            for key in (
                "manifest_schema",
                "build",
                "build_digest",
                "coverage",
                "gates",
            ):
                if key in meta:
                    details[key] = meta[key]
            return details

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def verify(self) -> dict[str, Any]:
        """Verify the active pointer, immutable metadata, bytes, hash and rows."""

        with self._lock(exclusive=False):
            self._require_unlocked()
            active = self._manifest()
            metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise CorpusManifestError("active corpus metadata is not an object")
            expected = active or metadata
            if int(expected.get("manifest_schema") or 1) >= CORPUS_MANIFEST_SCHEMA:
                try:
                    CorpusManifest.model_validate(expected)
                    CorpusManifest.model_validate(metadata)
                except ValueError as exc:
                    raise CorpusManifestError(
                        "active corpus versioned manifest is invalid"
                    ) from exc
                if active != metadata:
                    raise CorpusManifestError("active pointer and immutable metadata differ")
            actual_bytes = self.works_path.stat().st_size
            expected_bytes = int(expected.get("bytes") or actual_bytes)
            if actual_bytes != expected_bytes:
                raise CorpusManifestError("active corpus byte size does not match its manifest")
            expected_hash = str(expected.get("sha256") or "")
            actual_hash = self._sha256(self.works_path)
            if expected_hash and actual_hash != expected_hash:
                raise CorpusManifestError("active corpus checksum does not match its manifest")
            with duckdb.connect() as connection:
                row = connection.execute(
                    "SELECT count(*), count(DISTINCT id) FROM read_parquet(?)",
                    [str(self.works_path)],
                ).fetchone()
            actual_rows = int(row[0]) if row else 0
            unique_rows = int(row[1]) if row else 0
            if actual_rows != int(expected["works"]):
                raise CorpusManifestError("active corpus row count does not match its manifest")
            if unique_rows != actual_rows:
                raise CorpusManifestError("active corpus contains duplicate work ids")
            if (self.works_path.parent / RELEASE_FILE).is_file():
                from sixsentences_server.evals.release import (
                    ReleaseGateError,
                    verify_release_evidence,
                )

                try:
                    verify_release_evidence(self, self.works_path.parent.name)
                except ReleaseGateError as exc:
                    raise CorpusManifestError(
                        f"active corpus release evidence failed verification: {exc}"
                    ) from exc
            return {
                "ok": True,
                "version": str(expected["version"]),
                "works": actual_rows,
                "bytes": actual_bytes,
                "sha256": actual_hash,
                "manifest_schema": int(expected.get("manifest_schema") or 1),
                "release_approved": (self.works_path.parent / RELEASE_FILE).is_file(),
            }

    def present_work_ids(self, work_ids: list[str]) -> list[str]:
        """Return the requested IDs that exist in this immutable corpus view.

        Release evaluation needs exact corpus coverage for each golden case.
        Querying only the requested IDs avoids loading a production corpus' full
        identifier column into application memory.
        """

        requested = list(dict.fromkeys(work_ids))
        if not requested:
            return []
        with self.snapshot_path() as path, duckdb.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM read_parquet(?) WHERE id IN (SELECT unnest(?))",
                [str(path), requested],
            ).fetchall()
        found = {str(row[0]) for row in rows}
        return [work_id for work_id in requested if work_id in found]

    def list_snapshots(self) -> list[dict[str, Any]]:
        """Return validated immutable snapshot descriptors, newest first."""

        if not self.snapshots_dir.is_dir():
            return []
        active = self._manifest()
        active_name = Path(str(active.get("snapshot") or "")).name if active else ""
        snapshots: list[dict[str, Any]] = []
        for path in sorted(
            (
                candidate
                for candidate in self.snapshots_dir.iterdir()
                if candidate.is_dir()
                and not candidate.is_symlink()
                and not candidate.name.startswith(".")
            ),
            key=lambda candidate: candidate.stat().st_mtime_ns,
            reverse=True,
        ):
            metadata_path = path / META_FILE
            if not metadata_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            snapshots.append(
                {
                    "generation": path.name,
                    "version": str(metadata.get("version") or "unknown"),
                    "works": int(metadata.get("works") or 0),
                    "created_at": metadata.get("created_at"),
                    "active": path.name == active_name,
                    "release_approved": (path / RELEASE_FILE).is_file(),
                }
            )
        return snapshots

    def generation_manifest(self, generation: str) -> CorpusManifest:
        """Load and structurally verify one immutable generation."""

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", generation):
            raise CorpusManifestError("unsafe corpus generation name")
        directory = self.snapshots_dir / generation
        metadata_path = directory / META_FILE
        works_path = directory / WORKS_FILE
        if not directory.is_dir() or not metadata_path.is_file() or not works_path.is_file():
            raise CorpusManifestError("corpus generation does not exist or is incomplete")
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            manifest = CorpusManifest.model_validate(raw)
        except (OSError, ValueError) as exc:
            raise CorpusManifestError("corpus generation manifest is invalid") from exc
        if manifest.sha256 != self._sha256(works_path):
            raise CorpusManifestError("corpus generation checksum does not match")
        if manifest.bytes != works_path.stat().st_size:
            raise CorpusManifestError("corpus generation byte size does not match")
        expected_snapshot = Path(SNAPSHOTS_DIR) / generation
        if (
            Path(manifest.snapshot) != expected_snapshot
            or Path(manifest.works_file) != expected_snapshot / WORKS_FILE
            or Path(manifest.meta_file) != expected_snapshot / META_FILE
        ):
            raise CorpusManifestError("corpus generation paths do not match its directory")
        with duckdb.connect() as connection:
            row = connection.execute(
                "SELECT count(*), count(DISTINCT id) FROM read_parquet(?)",
                [str(works_path)],
            ).fetchone()
        if row is None or int(row[0]) != manifest.works or row[0] != row[1]:
            raise CorpusManifestError("corpus generation rows do not match")
        return manifest

    def activate_generation(
        self,
        generation: str,
        *,
        snapshots_to_keep: int = 2,
        require_release_evidence: bool = False,
    ) -> None:
        """Verify and atomically reactivate one retained rollback generation."""

        manifest = self.generation_manifest(generation)
        if require_release_evidence:
            from sixsentences_server.evals.release import (
                ReleaseGateError,
                verify_release_evidence,
            )

            try:
                verify_release_evidence(self, generation)
            except ReleaseGateError as exc:
                raise CorpusManifestError(f"corpus release evidence is invalid: {exc}") from exc
        self.activate_snapshot(
            manifest.model_dump(mode="json"),
            snapshots_to_keep=snapshots_to_keep,
        )

    def write_meta(self, version: str, works: int, sources: dict[str, str]) -> None:
        """Publish metadata for the legacy root-level fixture layout."""

        with self._lock(exclusive=True):
            self.corpus_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.corpus_dir / f".{META_FILE}.partial"
            temporary.write_text(
                json.dumps(
                    {"version": version, "works": works, "sources": sources},
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.corpus_dir / META_FILE)

    def activate_snapshot(
        self,
        manifest: dict[str, Any],
        *,
        snapshots_to_keep: int,
    ) -> None:
        """Atomically switch readers, then retain a bounded rollback window."""

        required = {
            "snapshot",
            "works_file",
            "meta_file",
            "version",
            "works",
            "sources",
        }
        if not required.issubset(manifest):
            raise CorpusManifestError("new corpus manifest is incomplete")
        encoded = json.dumps(manifest, indent=2, sort_keys=True)
        with self._lock(exclusive=True):
            temporary = self.corpus_dir / f".{CURRENT_FILE}.partial"
            temporary.write_text(encoded, encoding="utf-8")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            temporary.replace(self.current_path)
            directory_fd = os.open(self.corpus_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._prune_snapshots_unlocked(
                active=str(manifest["snapshot"]),
                snapshots_to_keep=max(2, snapshots_to_keep),
            )

    def _prune_snapshots_unlocked(
        self,
        *,
        active: str,
        snapshots_to_keep: int,
    ) -> None:
        if not self.snapshots_dir.is_dir():
            return
        generations = sorted(
            (
                path
                for path in self.snapshots_dir.iterdir()
                if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        keep = {Path(active).name}
        keep.update(path.name for path in generations[:snapshots_to_keep])
        for generation in generations:
            if generation.name in keep:
                continue
            children = list(generation.iterdir())
            if any(not child.is_file() or child.is_symlink() for child in children):
                continue
            for child in children:
                child.unlink()
            generation.rmdir()

    # -- queries ----------------------------------------------------------

    def count(self) -> int:
        with self.snapshot_path() as works_path, duckdb.connect() as con:
            row = con.execute("SELECT count(*) FROM read_parquet(?)", [str(works_path)]).fetchone()
        return int(row[0]) if row else 0

    def search(
        self,
        query: Node,
        limit: int = 10_000,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[WorkRecord]:
        condition, params = compile_duckdb(query)
        # optional publication-year window (works with an unknown year fall out
        # of a bounded search — a period filter must not silently include them)
        year_params: list[int] = []
        if year_from is not None:
            condition = f"({condition}) AND year >= ?"
            year_params.append(year_from)
        if year_to is not None:
            condition = f"({condition}) AND year <= ?"
            year_params.append(year_to)
        with self.snapshot_path() as works_path, duckdb.connect() as con:
            # Schema-evolution tolerant: corpora written before referenced_works
            # existed are still readable (the column defaults to an empty list).
            columns = {
                row[0]
                for row in con.execute(
                    "SELECT * FROM read_parquet(?) LIMIT 0", [str(works_path)]
                ).description
            }
            refs = (
                "referenced_works" if "referenced_works" in columns else "'[]' AS referenced_works"
            )
            # OA metadata arrived with the acquisition layer (H1); corpora synced
            # before it are still readable (the column defaults to an empty object).
            oa = "open_access" if "open_access" in columns else "'{}' AS open_access"
            wt = "work_type" if "work_type" in columns else "NULL AS work_type"
            sql = (
                "SELECT id, doi, title, abstract, year, venue, authors, cited_by_count, "
                f"is_retracted, source, {refs}, {oa}, {wt} "
                f"FROM read_parquet(?) WHERE {condition} "
                "ORDER BY cited_by_count DESC LIMIT ?"
            )
            rows = con.execute(sql, [str(works_path), *params, *year_params, limit]).fetchall()
        return [_row_to_record(row) for row in rows]

    def search_relaxed(
        self,
        query: Node,
        limit: int = 1_000,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[WorkRecord]:
        """Run an audited recall companion for one Boolean query.

        Strict phrase matching is the reproducible primary search. Large
        corpora additionally benefit from a bounded lexical relaxation that
        retains AND/OR concept structure but tolerates nearby terminology.
        Exact phrase and title matches rank first, so the bounded result set
        remains useful rather than becoming a popularity-only sample.
        """

        condition, params = _relaxed_query_condition(query)
        year_params: list[int] = []
        if year_from is not None:
            condition = f"({condition}) AND year >= ?"
            year_params.append(year_from)
        if year_to is not None:
            condition = f"({condition}) AND year <= ?"
            year_params.append(year_to)

        score_parts: list[str] = []
        score_params: list[str] = []
        seen: set[tuple[str, bool]] = set()
        for term in _positive_query_terms(query):
            needle = " ".join(term.text.casefold().split())
            key = (needle, term.phrase)
            if not needle or key in seen:
                continue
            seen.add(key)
            title_weight = 50 if term.phrase else 10
            abstract_weight = 10 if term.phrase else 1
            score_parts.extend(
                [
                    f"{title_weight} * CAST(contains(lower(coalesce(title, '')), ?) AS INTEGER)",
                    f"{abstract_weight} * CAST(contains("
                    "lower(coalesce(abstract, '')), ?) AS INTEGER)",
                ]
            )
            score_params.extend([needle, needle])
        score = " + ".join(score_parts) or "0"

        with self.snapshot_path() as works_path, duckdb.connect() as con:
            columns = {
                row[0]
                for row in con.execute(
                    "SELECT * FROM read_parquet(?) LIMIT 0", [str(works_path)]
                ).description
            }
            refs = (
                "referenced_works" if "referenced_works" in columns else "'[]' AS referenced_works"
            )
            oa = "open_access" if "open_access" in columns else "'{}' AS open_access"
            wt = "work_type" if "work_type" in columns else "NULL AS work_type"
            sql = (
                "SELECT id, doi, title, abstract, year, venue, authors, cited_by_count, "
                f"is_retracted, source, {refs}, {oa}, {wt} "
                f"FROM read_parquet(?) WHERE {condition} "
                f"ORDER BY ({score}) DESC, cited_by_count DESC LIMIT ?"
            )
            rows = con.execute(
                sql,
                [str(works_path), *params, *year_params, *score_params, limit],
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def lookup(
        self,
        *,
        work_id: str | None = None,
        doi: str | None = None,
        title: str | None = None,
        limit: int = 10,
    ) -> list[WorkRecord]:
        """Direct record lookup by id / DOI / approximate title — the probe
        path ("is this specific paper in the index at all?"), deliberately
        separate from boolean search. id/DOI match exactly (OR); a title
        matches when every significant word appears (the caller ranks the
        candidates by similarity)."""
        conditions: list[str] = []
        params: list[str] = []
        if work_id:
            conditions.append("id = ?")
            params.append(work_id)
        if doi:
            conditions.append("lower(doi) LIKE ?")
            params.append(f"%{doi.lower()}%")
        joiner = " OR "
        if not conditions and title:
            words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2][:6]
            if not words:
                return []
            joiner = " AND "
            for word in words:
                conditions.append("lower(title) LIKE ?")
                params.append(f"%{word}%")
        if not conditions:
            return []
        with self.snapshot_path() as works_path, duckdb.connect() as con:
            columns = {
                row[0]
                for row in con.execute(
                    "SELECT * FROM read_parquet(?) LIMIT 0", [str(works_path)]
                ).description
            }
            refs = (
                "referenced_works" if "referenced_works" in columns else "'[]' AS referenced_works"
            )
            oa = "open_access" if "open_access" in columns else "'{}' AS open_access"
            wt = "work_type" if "work_type" in columns else "NULL AS work_type"
            sql = (
                "SELECT id, doi, title, abstract, year, venue, authors, cited_by_count, "
                f"is_retracted, source, {refs}, {oa}, {wt} "
                f"FROM read_parquet(?) WHERE {joiner.join(conditions)} "
                "ORDER BY cited_by_count DESC LIMIT ?"
            )
            rows = con.execute(sql, [str(works_path), *params, limit]).fetchall()
        return [_row_to_record(row) for row in rows]

    def by_ids(self, ids: list[str]) -> list[WorkRecord]:
        """Batch id lookup — snowballing resolves whole reference lists this
        way without touching the live API."""
        if not ids:
            return []
        records: list[WorkRecord] = []
        with self.snapshot_path() as works_path, duckdb.connect() as con:
            columns = {
                row[0]
                for row in con.execute(
                    "SELECT * FROM read_parquet(?) LIMIT 0", [str(works_path)]
                ).description
            }
            refs = (
                "referenced_works" if "referenced_works" in columns else "'[]' AS referenced_works"
            )
            oa = "open_access" if "open_access" in columns else "'{}' AS open_access"
            wt = "work_type" if "work_type" in columns else "NULL AS work_type"
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                sql = (
                    "SELECT id, doi, title, abstract, year, venue, authors, cited_by_count, "
                    f"is_retracted, source, {refs}, {oa}, {wt} "
                    f"FROM read_parquet(?) WHERE id IN ({placeholders})"
                )
                rows = con.execute(sql, [str(works_path), *chunk]).fetchall()
                records.extend(_row_to_record(row) for row in rows)
        return records


def _row_to_record(row: tuple[Any, ...]) -> WorkRecord:
    (
        work_id,
        doi,
        title,
        abstract,
        year,
        venue,
        authors_json,
        cited_by,
        retracted,
        source,
        refs_json,
        oa_json,
        work_type,
    ) = row
    oa = json.loads(oa_json) if oa_json else {}
    return WorkRecord(
        id=work_id,
        doi=doi,
        title=title or "",
        abstract=abstract,
        year=year,
        venue=venue,
        authors=json.loads(authors_json) if authors_json else [],
        cited_by_count=cited_by or 0,
        is_retracted=bool(retracted),
        referenced_works=json.loads(refs_json) if refs_json else [],
        work_type=work_type,
        source=source or "openalex",
        oa_status=oa.get("oa_status"),
        oa_url=oa.get("oa_url"),
        pdf_url=oa.get("pdf_url"),
        oa_landing_url=oa.get("oa_landing_url"),
        oa_license=oa.get("oa_license"),
        oa_version=oa.get("oa_version"),
        arxiv_id=oa.get("arxiv_id"),
        pmcid=oa.get("pmcid"),
    )
