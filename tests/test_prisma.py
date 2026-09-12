"""PRISMA renderers format only explicit caller-provided counts."""

from datetime import UTC, datetime

from sixsentences.core.models import PrismaCounts, SearchExecution
from sixsentences.reporting.prisma import (
    render_flow_svg,
    render_flow_text,
    render_search_appendix,
)


def test_text_and_svg_render_the_same_terminal_count() -> None:
    counts = PrismaCounts(
        records_identified=12,
        other_identified=1,
        citation_identified=2,
        companion_reports_linked=1,
        duplicates_removed=2,
        records_screened=10,
        records_excluded=7,
        records_unsure=1,
        included=2,
    )
    text = render_flow_text(counts)
    assert "records included:          2" in text
    assert "via other methods:         1" in text
    svg = render_flow_svg(counts)
    assert svg.startswith("<svg")
    assert "1 via other methods" in svg
    assert "2 via citation search" in svg
    assert "1 companion report links" in svg
    assert "studies included" not in svg


def test_search_appendix_preserves_the_verbatim_query() -> None:
    query = '"infrastructure as code" AND security'
    appendix = render_search_appendix(
        [
            SearchExecution(
                source="openalex",
                platform="OpenAlex API",
                query_verbatim=query,
                date_run=datetime(2026, 1, 2, tzinfo=UTC),
                records_returned=4,
            )
        ]
    )
    assert query in appendix
    assert "2026-01-02" in appendix
    assert "deduplication:     not reported" in appendix


def test_svg_does_not_invent_a_retrieval_method_or_failure_reason() -> None:
    counts = PrismaCounts(
        records_identified=2,
        records_screened=2,
        included=2,
        reports_sought_for_retrieval=2,
        reports_not_retrieved=1,
        reports_assessed_for_eligibility=1,
        reports_included=1,
        studies_included=1,
    )
    svg = render_flow_svg(counts)
    assert "reports sought" in svg
    assert "reports not retrieved" in svg
    assert "open access" not in svg
    assert "no OA copy" not in svg
    assert "CALLER-SUPPLIED" in svg


def test_eligibility_flow_distinguishes_reports_and_studies() -> None:
    counts = PrismaCounts(
        records_identified=3,
        records_screened=3,
        included=3,
        reports_sought_for_retrieval=3,
        reports_assessed_for_eligibility=3,
        reports_excluded_fulltext=1,
        reports_included=2,
        studies_included=1,
    )

    text = render_flow_text(counts)
    svg = render_flow_svg(counts)

    assert "reports included:          2" in text
    assert "Studies included:            1" in text
    assert "reports of included studies" in svg
    assert "studies included" in svg
