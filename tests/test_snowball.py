"""Citation snowballing widens identification without auto-including works."""

from collections.abc import Iterator

from sixsentences.core.models import WorkRecord
from sixsentences.pipeline.snowball import collect_snowball_candidates


class SyntheticCorpus:
    def by_ids(self, ids: list[str]) -> list[WorkRecord]:
        if "W2" not in ids:
            return []
        return [WorkRecord(id="W2", title="Backward corpus candidate", year=2024)]


class SyntheticCitationClient:
    def iter_works(self, oa_filter: str, *, limit: int) -> Iterator[WorkRecord]:
        del limit
        if oa_filter.startswith("openalex_id:"):
            yield WorkRecord(id="W3", title="Out of date range", year=2010)
        elif oa_filter.startswith("cites:"):
            yield WorkRecord(id="W4", title="Forward citation candidate", year=2025)
            yield WorkRecord(id="KNOWN", title="Already identified", year=2025)


class _IdentifierPolicyCorpus:
    def by_ids(self, ids: list[str]) -> list[WorkRecord]:
        records = {
            "W2": WorkRecord(id="W2", title="Shared title with a +5 effect"),
            "W3": WorkRecord(id="W3", title="Shared title with a +5 effect"),
            "W4": WorkRecord(id="W4", title="First DOI copy", doi="10.1234/shared"),
            "W5": WorkRecord(
                id="W5",
                title="Second DOI copy",
                doi="https://doi.org/10.1234/SHARED",
            ),
        }
        return [records[work_id] for work_id in ids if work_id in records]


def test_snowball_collects_candidates_but_keeps_filters_and_known_ids() -> None:
    seed = WorkRecord(
        id="W1",
        title="Seed study",
        year=2024,
        referenced_works=["W2", "W3"],
    )
    result = collect_snowball_candidates(
        [seed],
        known_ids={"W1", "KNOWN"},
        known_dois=set(),
        corpus=SyntheticCorpus(),
        client=SyntheticCitationClient(),
        year_from=2020,
    )

    assert [record.id for record in result.records] == ["W2", "W4"]
    assert result.backward_refs == 2
    assert result.backward_resolved == 2
    assert result.skipped_filters == 1
    assert result.skipped_known == 1


def test_snowball_never_suppresses_by_title_but_deduplicates_valid_dois() -> None:
    seed = WorkRecord(
        id="W1",
        title="Seed study",
        referenced_works=["W2", "W3", "W4", "W5"],
    )

    result = collect_snowball_candidates(
        [seed],
        known_ids={"W1"},
        known_dois=set(),
        corpus=_IdentifierPolicyCorpus(),
    )

    assert [record.id for record in result.records] == ["W2", "W3", "W4"]
    assert result.skipped_known == 1
