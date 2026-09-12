"""Portable input models reject ambiguous empty identities and invalid flows."""

import pytest
from pydantic import ValidationError

from sixsentences.core.models import PrismaCounts, ReviewProtocol, WorkRecord


@pytest.mark.parametrize(
    ("work_id", "title"),
    [("", "Valid title"), ("   ", "Valid title"), ("W1", ""), ("W1", "\n\t")],
)
def test_work_identity_and_title_must_not_be_empty(work_id: str, title: str) -> None:
    with pytest.raises(ValidationError):
        WorkRecord(id=work_id, title=title)


def test_work_identity_and_title_are_trimmed() -> None:
    work = WorkRecord(id=" W1 ", title="  Valid title  ")
    assert work.id == "W1"
    assert work.title == "Valid title"


@pytest.mark.parametrize(
    "field",
    [
        {"cited_by_count": -1},
        {"cited_by_count": True},
        {"year": -1},
        {"year": 10_000},
    ],
)
def test_work_numeric_metadata_has_plausible_domains(field: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkRecord(id="W1", title="Valid title", **field)


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "", "query_string": "evidence"},
        {"question": "Evidence", "query_string": ""},
        {"question": "Evidence", "query_string": "(broken"},
        {"question": "Evidence", "query_string": "evidence", "year_from": 2025, "year_to": 2024},
        {"question": "Evidence", "query_string": "evidence", "version": 0},
    ],
)
def test_protocol_rejects_empty_or_inconsistent_inputs(payload: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        ReviewProtocol.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"records_identified": 2, "duplicates_removed": 3},
        {"records_identified": 2, "other_identified": 2, "citation_identified": 1},
        {"records_identified": 3, "duplicates_removed": 1, "records_screened": 3},
        {"records_identified": 100, "records_screened": 1},
        {"records_identified": 2, "records_screened": 2, "records_excluded": 3},
        {
            "records_identified": 2,
            "records_screened": 2,
            "included": 1,
            "reports_sought_for_retrieval": 2,
        },
        {
            "records_identified": 2,
            "records_screened": 2,
            "included": 2,
            "reports_sought_for_retrieval": 2,
            "reports_assessed_for_eligibility": 2,
            "reports_included": 1,
            "studies_included": 2,
        },
        {
            "records_identified": 1,
            "records_screened": 1,
            "included": 1,
            "reports_sought_for_retrieval": 1,
            "reports_assessed_for_eligibility": 1,
            "reports_included": 1,
            "studies_included": 0,
        },
    ],
)
def test_prisma_counts_reject_inconsistent_subtotals(payload: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        PrismaCounts.model_validate(payload)
