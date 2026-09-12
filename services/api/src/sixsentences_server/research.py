"""Auditable public-metadata research pipeline used by durable jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sixsentences.connectors.openalex import OpenAlexClient
from sixsentences.core.models import ReviewProtocol
from sixsentences.querylang.ast import to_display
from sixsentences.querylang.compile_openalex import compile_openalex
from sixsentences.querylang.parser import parse_query

from sixsentences_server.config import Settings


class ResearchExecutor(Protocol):
    """Injectable execution boundary for network-free worker tests."""

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class OpenAlexResearchExecutor:
    """Parse, compile, execute and record one bounded public metadata search."""

    settings: Settings

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload["query"])
        question = str(payload["question"])
        limit = min(int(payload.get("limit", self.settings.research_result_limit)), 1000)
        year_from = payload.get("year_from")
        year_to = payload.get("year_to")
        node = parse_query(query)
        compiled, notes = compile_openalex(node)
        protocol = ReviewProtocol(
            question=question,
            query_string=query,
            year_from=year_from,
            year_to=year_to,
            synthesized_by="human",
        )
        with OpenAlexClient(
            mailto=self.settings.openalex_mailto,
            api_key=self.settings.openalex_api_key.get_secret_value(),
        ) as client:
            works = client.search(
                compiled,
                limit=limit,
                year_from=year_from,
                year_to=year_to,
            )
        return {
            "protocol": protocol.model_dump(mode="json"),
            "normalized_query": to_display(node),
            "provider_query": compiled,
            "translation_notes": {
                "dropped_wildcards": notes.dropped_wildcards,
                "dropped_fields": notes.dropped_fields,
            },
            "records": [work.model_dump(mode="json") for work in works],
            "record_count": len(works),
            "truncated": len(works) >= limit,
            "method": "bounded cursor search over public scholarly metadata",
        }
