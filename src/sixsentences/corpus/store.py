"""Interfaces shared by local corpus implementations."""

from dataclasses import dataclass
from typing import Protocol

from sixsentences.core.models import WorkRecord
from sixsentences.querylang.ast import Node


@dataclass(frozen=True)
class CorpusVersion:
    """Immutable identity of a local corpus snapshot."""

    identifier: str
    works: int
    sha256: str


class CorpusStore(Protocol):
    """Minimum interface needed by search and snowballing stages."""

    def version(self) -> CorpusVersion:
        """Return the immutable snapshot identity."""

        ...

    def search(self, query: Node, limit: int = 10_000) -> list[WorkRecord]:
        """Search the corpus using the canonical AST."""

        ...

    def by_ids(self, ids: list[str]) -> list[WorkRecord]:
        """Resolve canonical ids in caller order."""

        ...

    def count(self) -> int:
        """Return the number of works in the snapshot."""

        ...
