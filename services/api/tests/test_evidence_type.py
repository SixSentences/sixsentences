"""Evidence-type constraints for novice research requests."""

from sixsentences_server.core.evidence_type import (
    filter_primary_research,
    foundational_method_requested,
    primary_research_requested,
    secondary_source_like,
)
from sixsentences_server.core.models import WorkRecord


def _work(work_id: str, title: str, *, work_type: str = "article") -> WorkRecord:
    return WorkRecord(id=work_id, title=title, work_type=work_type)


def test_german_original_papers_exclude_clear_reviews_and_surveys() -> None:
    primary = _work("W1", "A Controlled Evaluation of Retrieval Augmented Generation")
    survey = _work("W2", "A Survey on Retrieval Augmented Generation")
    review = _work("W3", "Recent systems", work_type="review")

    kept, omitted = filter_primary_research(
        "Mach mir eine Tabelle mit fünf wirklich passenden Originalarbeiten.",
        [survey, primary, review],
    )

    assert [work.id for work in kept] == ["W1"]
    assert [work.id for work in omitted] == ["W2", "W3"]


def test_explicit_request_for_original_papers_and_reviews_keeps_both() -> None:
    works = [
        _work("W1", "An Empirical RAG Evaluation"),
        _work("W2", "A Systematic Review of RAG"),
    ]

    kept, omitted = filter_primary_research(
        "Compare original papers and systematic reviews.", works
    )

    assert kept == works
    assert omitted == []


def test_all_secondary_candidates_remain_visible_for_an_honest_gap() -> None:
    works = [
        _work("W1", "A Survey of RAG"),
        _work("W2", "A Systematic Literature Review of RAG"),
    ]

    kept, omitted = filter_primary_research("Show three primary research papers", works)

    assert kept == works
    assert omitted == []


def test_systematic_evaluation_is_not_misclassified_as_a_review() -> None:
    work = _work("W1", "A Systematic Evaluation of Retrieval Pipelines")

    assert primary_research_requested("show empirical studies")
    assert not secondary_source_like(work)


def test_negative_survey_constraint_means_primary_research() -> None:
    assert primary_research_requested(
        "kleine korrektur: nur die ursprünglichen Methodenarbeiten, keine Surveys"
    )
    assert primary_research_requested("Use original method papers, without reviews")


def test_foundational_method_request_excludes_later_benchmarks_and_extensions() -> None:
    works = [
        _work("W-RAG", "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"),
        _work("W-FUSION", "RAG-Fusion: A New Take on Retrieval Augmented Generation"),
        _work("W-BENCH", "Benchmarking Large Language Models in Retrieval-Augmented Generation"),
        _work("W-SURVEY", "A Survey of Retrieval-Augmented Generation"),
    ]

    kept, omitted = filter_primary_research(
        "wirklich nur die ursprünglichen Methodenarbeiten, keine Surveys",
        works,
    )

    assert foundational_method_requested(
        "wirklich nur die ursprünglichen Methodenarbeiten, keine Surveys"
    )
    assert [work.id for work in kept] == ["W-RAG"]
    assert [work.id for work in omitted] == ["W-FUSION", "W-BENCH", "W-SURVEY"]
