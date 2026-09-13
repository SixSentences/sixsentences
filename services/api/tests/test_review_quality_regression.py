"""Deterministic quality gates for evidence-state and document-identity rules."""

import json
from pathlib import Path
from types import SimpleNamespace

from sixsentences_server.acquisition.identity import verify_document_identity
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.screening.evidence import evidence_state

FIXTURE = Path(__file__).parent / "fixtures" / "review_quality_cases.json"


def _cases() -> dict:  # type: ignore[type-arg]
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_document_identity_quality_cases() -> None:
    for case in _cases()["identity_cases"]:
        work = WorkRecord.model_validate(case["work"])
        document = (case["document"] + "\n") * int(case["repeat"])
        result = verify_document_identity(work, document)
        assert result.status.value == case["expected"], case["id"]


def test_evidence_state_quality_cases() -> None:
    for case in _cases()["evidence_state_cases"]:
        decision = SimpleNamespace(
            reviewer=case["reviewer"],
            verdict=case["verdict"],
        )
        assert evidence_state(decision).value == case["expected"]
