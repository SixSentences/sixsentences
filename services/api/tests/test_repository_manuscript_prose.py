"""Grounding and provider-boundary tests for repository manuscript prose."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from sixsentences_server.repositories.prose import generate_repository_manuscript_prose
from sixsentences_server.repositories.schemas import (
    DiagramEdge,
    DiagramNode,
    DiagramSpec,
    EvidenceRecord,
    RepositoryCoverage,
)


class SelectionPool:
    """Capture the exact egress DTO and return a fixed ID selection."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        _task: object,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
    ) -> object:
        self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
        return SimpleNamespace(text=json.dumps(self.payload))


def _grounding(
    *,
    node_count: int = 3,
    edge_count: int = 2,
    long_ids: bool = False,
) -> tuple[DiagramSpec, list[EvidenceRecord], RepositoryCoverage]:
    node_ids = [
        (f"n{index:02d}" + "x" * 61 if long_ids else f"node_{index:02d}")
        for index in range(node_count)
    ]
    edge_ids = [
        (f"e{index:02d}" + "y" * 61 if long_ids else f"edge_{index:02d}")
        for index in range(edge_count)
    ]
    evidence: list[EvidenceRecord] = []
    nodes: list[DiagramNode] = []
    for index, node_id in enumerate(node_ids):
        label = f"Node{index:02d}"
        evidence_id = f"ev_{index:016x}"
        evidence.append(
            EvidenceRecord(
                id=evidence_id,
                path=f"private/CANARY_PATH_{index}.py",
                start_line=1,
                end_line=2,
                file_sha256=f"{index + 1:064x}",
                excerpt_sha256=f"{index + 101:064x}",
                kind="declaration",
                parser_id="tree-sitter-python",
                confidence=0.8,
                summary=(f"CANARY_SUMMARY_{index}; canonical display label {label};"),
                source_node_id=node_id,
                source_node_kind="module",
                source_node_group="Application",
            )
        )
        nodes.append(
            DiagramNode(
                id=node_id,
                label=label,
                kind="module",
                group="Application",
                evidence_ids=[evidence_id],
                confidence=0.8,
            )
        )
    edges: list[DiagramEdge] = []
    for index, edge_id in enumerate(edge_ids):
        source = node_ids[index % node_count]
        target = node_ids[(index + 1) % node_count]
        evidence_id = f"ev_{node_count + index:016x}"
        evidence.append(
            EvidenceRecord(
                id=evidence_id,
                path=f"private/CANARY_EDGE_PATH_{index}.py",
                start_line=3,
                end_line=4,
                file_sha256=f"{node_count + index + 1:064x}",
                excerpt_sha256=f"{node_count + index + 101:064x}",
                kind="dependency",
                parser_id="tree-sitter-python",
                confidence=0.7,
                summary=f"CANARY_EDGE_SUMMARY_{index}",
                source_node_id=source,
                source_node_kind="module",
                source_node_group="Application",
                target_node_id=target,
                target_node_kind="module",
                target_node_group="Application",
                relationship="uses",
            )
        )
        edges.append(
            DiagramEdge(
                id=edge_id,
                source=source,
                target=target,
                label="uses",
                evidence_ids=[evidence_id],
                confidence=0.7,
            )
        )
    spec = DiagramSpec(
        title="Canonical architecture",
        kind="architecture",
        language="en",
        analysis_mode="model_assisted",
        nodes=nodes,
        edges=edges,
        scope_note="CANARY_SCOPE_NOTE must never cross provider egress",
    )
    coverage = RepositoryCoverage(
        archive_entries=node_count + edge_count,
        files_in_scope=node_count + edge_count,
        eligible_files=node_count + edge_count,
        analyzed_files=node_count + edge_count,
        excluded_files=0,
        eligible_bytes=1_000,
        analyzed_bytes=1_000,
        excluded_by_reason={},
        complete=True,
    )
    return spec, evidence, coverage


def test_provider_only_selects_ids_and_never_receives_source_locators() -> None:
    spec, evidence, coverage = _grounding()
    pool = SelectionPool(
        {
            "selected_node_ids": [node.id for node in spec.nodes],
            "selected_edge_ids": [spec.edges[0].id],
        }
    )

    result = generate_repository_manuscript_prose(
        pool,
        spec_value=spec,
        evidence_value=evidence,
        coverage_value=coverage,
        prose_kind="description",
        language="en",
    )

    assert len(pool.calls) == 1
    egress = pool.calls[0]["system"] + pool.calls[0]["prompt"]
    assert "CANARY_PATH" not in egress
    assert "CANARY_SUMMARY" not in egress
    assert "CANARY_SCOPE_NOTE" not in egress
    assert "ev_" not in egress
    assert pool.calls[0]["max_tokens"] == 2_000
    assert result.text == " ".join(claim.text for claim in result.claims)
    assert result.evidence_ids
    assert all(claim.text not in pool.calls[0]["prompt"] for claim in result.claims)


def test_provider_omits_path_shaped_canonical_display_labels() -> None:
    spec, evidence, coverage = _grounding()
    source_path = "src/main.py"
    evidence[0] = evidence[0].model_copy(
        update={
            "path": source_path,
            "summary": "canonical display label src/main.py;",
        }
    )
    spec.nodes[0] = spec.nodes[0].model_copy(update={"label": source_path})
    pool = SelectionPool({"selected_node_ids": [spec.nodes[0].id], "selected_edge_ids": []})

    result = generate_repository_manuscript_prose(
        pool,
        spec_value=spec,
        evidence_value=evidence,
        coverage_value=coverage,
        prose_kind="description",
        language="en",
    )

    assert source_path not in pool.calls[0]["prompt"]
    assert "display_label" not in pool.calls[0]["prompt"]
    assert source_path in result.text


@pytest.mark.parametrize(
    "selection",
    [
        {
            "selected_node_ids": ["node_00", "invented_node"],
            "selected_edge_ids": [],
        },
        {
            "selected_node_ids": ["node_00", "node_00"],
            "selected_edge_ids": [],
        },
        {
            "selected_node_ids": ["node_00", "node_01"],
            "selected_edge_ids": [],
            "authored_claim": "Trust me instead of the evidence.",
        },
        {
            "selected_node_ids": ["node_00"],
            "selected_edge_ids": ["edge_00"],
        },
    ],
)
def test_selector_rejects_unknown_duplicate_extra_or_unbound_ids(
    selection: dict[str, object],
) -> None:
    spec, evidence, coverage = _grounding()
    with pytest.raises(ValueError):
        generate_repository_manuscript_prose(
            SelectionPool(selection),
            spec_value=spec,
            evidence_value=evidence,
            coverage_value=coverage,
            prose_kind="description",
            language="en",
        )


def test_maximum_section_preserves_all_long_ids_with_bounded_output() -> None:
    spec, evidence, coverage = _grounding(
        node_count=20,
        edge_count=32,
        long_ids=True,
    )
    pool = SelectionPool(
        {
            "selected_node_ids": [node.id for node in spec.nodes],
            "selected_edge_ids": [edge.id for edge in spec.edges],
        }
    )

    result = generate_repository_manuscript_prose(
        pool,
        spec_value=spec,
        evidence_value=evidence,
        coverage_value=coverage,
        prose_kind="section",
        language="en",
        analysis_metadata={
            "candidate_nodes_omitted": 4,
            "candidate_edges_omitted": 7,
        },
    )

    assert result.selected_node_ids == [node.id for node in spec.nodes]
    assert result.selected_edge_ids == [edge.id for edge in spec.edges]
    assert result.described_node_count == result.spec_node_count == 20
    assert result.described_edge_count == result.spec_edge_count == 32
    assert len(result.text) <= 16_000
    assert "4 additional component candidates" in result.scope_note
    assert "7 additional relationship candidates" in result.scope_note
    assert len(pool.calls[0]["prompt"]) <= 16_000
    assert pool.calls[0]["max_tokens"] == 2_000
