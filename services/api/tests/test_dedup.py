"""Title-normalized deduplication of re-indexed records."""

from sixsentences_server.core.models import WorkRecord
from sixsentences_server.pipeline.dedup import dedup_by_title


def _w(wid: str, title: str, **kw: object) -> WorkRecord:
    return WorkRecord(id=wid, title=title, **kw)  # type: ignore[arg-type]


def test_collapses_the_same_paper_reindexed_under_different_ids() -> None:
    works = [
        _w(
            "W1",
            "IaC Generation with LLMs: An Error Taxonomy",
            doi="10.1/x",
            venue="ACM Transactions on Software Engineering",
            cited_by_count=5,
        ),
        _w("W2", "IaC Generation with LLMs: An Error Taxonomy", venue="arXiv", cited_by_count=2),
        _w(
            "W3",
            "iac generation with llms: an error taxonomy.",  # case + trailing dot
            venue="arXiv (Cornell University)",
        ),
    ]
    kept, removed = dedup_by_title(works)
    assert removed == 2 and len(kept) == 1
    assert kept[0].id == "W1"  # canonical: has DOI and a published venue


def test_distinct_titles_are_not_merged() -> None:
    works = [
        _w("W1", "Terraform configuration generation with language models"),
        _w("W2", "Ansible playbook synthesis with language models"),
    ]
    kept, removed = dedup_by_title(works)
    assert removed == 0 and len(kept) == 2


def test_short_generic_titles_are_never_merged() -> None:
    works = [_w("W1", "Editorial"), _w("W2", "Editorial"), _w("W3", "Introduction")]
    kept, removed = dedup_by_title(works)
    assert removed == 0 and len(kept) == 3  # too short to dedup safely


def test_canonical_prefers_more_citations_when_no_doi() -> None:
    works = [
        _w("W1", "A comparative study of LLM infrastructure code generation", cited_by_count=1),
        _w("W2", "A comparative study of LLM infrastructure code generation", cited_by_count=40),
    ]
    kept, removed = dedup_by_title(works)
    assert removed == 1 and kept[0].id == "W2"
