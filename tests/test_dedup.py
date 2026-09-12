"""Destructive deduplication requires a valid canonical DOI."""

import pytest

from sixsentences.core.models import WorkRecord
from sixsentences.core.text import structural_tokens
from sixsentences.pipeline.dedup import dedup_by_doi


def _w(wid: str, title: str, **kw: object) -> WorkRecord:
    return WorkRecord(id=wid, title=title, **kw)  # type: ignore[arg-type]


def test_collapses_records_with_the_same_valid_canonical_doi() -> None:
    works = [
        _w(
            "W1",
            "IaC Generation with LLMs: An Error Taxonomy",
            doi="https://doi.org/10.1234/Study-2024-A",
            venue="ACM Transactions on Software Engineering",
            cited_by_count=5,
            authors=["Ada Lovelace"],
            year=2024,
        ),
        _w(
            "W2",
            "IaC Generation with LLMs — preprint metadata",
            doi="doi:10.1234/study-2024-a",
            venue="arXiv",
            cited_by_count=2,
            authors=["Ada Lovelace"],
            year=2023,
        ),
        _w(
            "W3",
            "iac generation with llms: an error taxonomy.",
            doi="10.1234/STUDY-2024-A",
            venue="arXiv (Cornell University)",
            authors=["Lovelace, Ada"],
            year=2023,
        ),
    ]
    kept, removed = dedup_by_doi(works)
    assert removed == 2 and len(kept) == 1
    assert kept[0].id == "W1"  # canonical: richer metadata and a published venue


def test_distinct_titles_are_not_merged() -> None:
    works = [
        _w("W1", "Terraform configuration generation with language models"),
        _w("W2", "Ansible playbook synthesis with language models"),
    ]
    kept, removed = dedup_by_doi(works)
    assert removed == 0 and len(kept) == 2


def test_identifierless_records_are_never_merged_even_with_identical_short_titles() -> None:
    works = [_w("W1", "Editorial"), _w("W2", "Editorial"), _w("W3", "Introduction")]
    kept, removed = dedup_by_doi(works)
    assert removed == 0 and len(kept) == 3


def test_canonical_prefers_more_citations_within_a_doi_group() -> None:
    works = [
        _w(
            "W1",
            "A comparative study of LLM infrastructure code generation",
            doi="10.4321/shared",
            cited_by_count=1,
            authors=["Grace Hopper"],
            year=2025,
        ),
        _w(
            "W2",
            "A comparative study of LLM infrastructure code generation",
            doi="10.4321/shared",
            cited_by_count=40,
            authors=["Grace Hopper"],
            year=2025,
        ),
    ]
    kept, removed = dedup_by_doi(works)
    assert removed == 1 and kept[0].id == "W2"


def test_short_titles_do_not_move_ahead_of_earlier_long_title_groups() -> None:
    first = _w(
        "W1",
        "A sufficiently long first title for stable ordering",
        doi="10.5678/canonical",
        authors=["Ada Lovelace"],
        year=2024,
    )
    short = _w("W2", "Editorial")
    duplicate = _w(
        "W3",
        "A sufficiently long first title for stable ordering",
        doi="10.5678/canonical",
        authors=["Ada Lovelace"],
        year=2024,
    )
    last = _w("W4", "A distinct sufficiently long title at the end")

    kept, removed = dedup_by_doi([first, short, duplicate, last])

    assert removed == 1
    assert [work.id for work in kept] == ["W1", "W2", "W4"]


def test_same_title_without_identity_evidence_is_not_merged() -> None:
    title = "A sufficiently long but metadata poor study title"
    kept, removed = dedup_by_doi([_w("W1", title), _w("W2", title)])

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]


def test_same_title_with_conflicting_identity_evidence_is_not_merged() -> None:
    title = "A sufficiently long shared title that can name distinct studies"
    works = [
        _w("W1", title, doi="10.1234/a", authors=["Ada Lovelace"], year=2022),
        _w("W2", title, doi="10.1234/b", authors=["Grace Hopper"], year=2025),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]


def test_same_surname_and_year_with_different_given_names_is_not_merged() -> None:
    title = "A sufficiently long shared title for separate author studies"
    works = [
        _w("W1", title, authors=["Alice Smith"], year=2024),
        _w("W2", title, authors=["Bob Smith"], year=2024),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]


def test_initial_cannot_bridge_different_given_names_into_one_group() -> None:
    title = "A sufficiently long shared title for ambiguous author identities"
    works = [
        _w("W1", title, authors=["Ada Smith"], year=2024),
        _w("W2", title, authors=["A. Smith"], year=2024),
        _w("W3", title, authors=["Alice Smith"], year=2024),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2", "W3"]


def test_same_surname_and_year_with_different_initials_is_not_merged() -> None:
    title = "A sufficiently long shared title for distinct author initials"
    works = [
        _w("W1", title, authors=["A. Smith"], year=2024),
        _w("W2", title, authors=["B. Smith"], year=2024),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]


def test_initial_and_full_given_name_are_not_destructive_identity_evidence() -> None:
    title = "A sufficiently long shared title for incomplete author metadata"
    works = [
        _w("W1", title, authors=["A. Smith"], year=2024),
        _w("W2", title, authors=["Alice Smith"], year=2024),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]


def test_matching_initial_only_authors_are_not_destructive_identity_evidence() -> None:
    title = "A sufficiently long shared title with initial-only author metadata"
    works = [
        _w("W1", title, authors=["A. Smith"], year=2024),
        _w("W2", title, authors=["A. Smith"], year=2024),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]


def test_semantic_title_symbols_are_not_folded_for_destructive_dedup() -> None:
    author_and_year = {"authors": ["Alice Smith"], "year": 2024}
    title_pairs = [
        ("Treatment response reached 50% in adults", "Treatment response reached 50 in adults"),
        ("Treatment costs of $500 in adults", "Treatment costs of 500 in adults"),
        (
            "Event ratio 5/10 after treatment in adults",
            "Event ratio 5*10 after treatment in adults",
        ),
        ("Treatment effect was +5 units in adults", "Treatment effect was -5 units in adults"),
    ]

    for index, (left, right) in enumerate(title_pairs):
        assert structural_tokens(left) != structural_tokens(right)
        kept, removed = dedup_by_doi(
            [
                _w(f"W{index}a", left, **author_and_year),
                _w(f"W{index}b", right, **author_and_year),
            ]
        )

        assert removed == 0
        assert len(kept) == 2


@pytest.mark.parametrize(
    "invalid_doi",
    [
        "",
        "   ",
        "N/A",
        "unknown",
        "unavailable",
        "10.1/x",
        "10.1234",
        "10.1234/contains whitespace",
        "https://example.org/10.1234/wrong-host",
        "10.1234/-",
        "10.1234/.",
        "10.1234/()",
        "10.1234/unknown",
        "10.1234/null",
        "10.1234/tbd",
        "doi:10.1234/N/A",
        "doi:\n10.1234/control",
        "doi:\v10.1234/control",
        "https://doi.org/\n10.1234/control",
        "https://doi.org/ 10.1234/space-after-url-prefix",
        "10.0000/0000",
        "10.1234/000000",
        "10.1234/t.b.d",
        "10.1234/null-null",
        "10.1234/test",
        "10.1234/example",
        "10.1234/placeholder",
        "10.1234/pending",
        "10.1234/tba",
        "10.1234/notassigned",
        "10.1234/not-assigned",
        "10.1234/no-doi",
        "10.1234/nil",
        "10.1234/undefined",
        "10.1234/dummy",
        "10.1234/fake",
    ],
)
def test_invalid_or_placeholder_doi_never_authorizes_deletion(invalid_doi: str) -> None:
    title = "A sufficiently long identical title with matching author metadata"
    works = [
        _w("W1", title, doi=invalid_doi, authors=["Alice Smith"], year=2024),
        _w("W2", title, doi=invalid_doi, authors=["Alice Smith"], year=2024),
    ]

    kept, removed = dedup_by_doi(works)

    assert removed == 0
    assert [work.id for work in kept] == ["W1", "W2"]
