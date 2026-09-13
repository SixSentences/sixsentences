"""CorpusStore interface: the corpus-first seam of the whole system.

Retrieval always goes through a CorpusStore. The skeleton ships a local
Parquet+DuckDB implementation (MicroCorpus); H1 swaps in the full-snapshot
cluster implementation (OpenSearch/Vespa) behind the same interface. Runs pin
the CorpusVersion they searched, which is what makes searches replayable.
"""

from dataclasses import dataclass
from typing import Protocol

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.querylang.ast import Node


@dataclass(frozen=True)
class CorpusVersion:
    version: str  # e.g. "micro-2026-07-14-openalex"
    works: int
    sources: dict[str, str]  # source -> ingest descriptor


class CorpusStore(Protocol):
    def version(self) -> CorpusVersion: ...

    def search(self, query: Node, limit: int = 10_000) -> list[WorkRecord]: ...

    def count(self) -> int: ...
