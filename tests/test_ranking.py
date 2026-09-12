"""Decomposed multi-signal ranking: relevance, impact, recency, integrity."""

import pytest

from sixsentences.core.models import ReviewProtocol, WorkRecord
from sixsentences.ranking.scorer import DEFAULT_WEIGHTS, RETRACTION_PENALTY, rank_works

PROTOCOL = ReviewProtocol(
    question="How do transformer attention mechanisms improve translation?",
    inclusion_criteria=["neural machine translation"],
    query_string="transformer AND attention",
)

NOW = 2026


def _work(wid: str, title: str, *, year: int, cites: int, retracted: bool = False) -> WorkRecord:
    return WorkRecord(id=wid, title=title, year=year, cited_by_count=cites, is_retracted=retracted)


def test_relevance_rewards_protocol_vocabulary_in_title() -> None:
    on_topic = _work("W1", "Transformer attention for translation", year=2020, cites=10)
    off_topic = _work("W2", "A study of soil microbiology", year=2020, cites=10)
    ranked = {r.work.id: r for r in rank_works([on_topic, off_topic], PROTOCOL, now_year=NOW)}
    assert ranked["W1"].signals.relevance > ranked["W2"].signals.relevance
    assert ranked["W1"].score > ranked["W2"].score


def test_impact_is_age_normalized_and_scaled_to_unit() -> None:
    # same citations, but the newer work earned them faster -> higher impact
    old = _work("W1", "transformer attention", year=2010, cites=160)
    new = _work("W2", "transformer attention", year=2024, cites=160)
    ranked = {r.work.id: r for r in rank_works([old, new], PROTOCOL, now_year=NOW)}
    assert ranked["W2"].signals.impact > ranked["W1"].signals.impact
    # normalization: the top impact in the set is 1.0
    assert max(r.signals.impact for r in ranked.values()) == 1.0


def test_retracted_work_is_penalized_but_kept_and_flagged() -> None:
    clean = _work("W1", "transformer attention translation", year=2021, cites=50)
    dirty = _work("W2", "transformer attention translation", year=2021, cites=50, retracted=True)
    ranked = {r.work.id: r for r in rank_works([clean, dirty], PROTOCOL, now_year=NOW)}
    assert ranked["W2"].retracted is True
    # identical signals, so the penalty is exactly the multiplicative factor
    assert ranked["W2"].score == round(ranked["W1"].score * RETRACTION_PENALTY, 6)
    # still present in the output, just ranked below the clean one
    assert ranked["W1"].score > ranked["W2"].score


def test_ranking_is_deterministic_and_ordered() -> None:
    works = [
        _work("W1", "unrelated topic", year=2019, cites=1),
        _work("W2", "transformer attention translation", year=2023, cites=500),
        _work("W3", "attention mechanisms", year=2018, cites=50),
    ]
    first = [r.work.id for r in rank_works(works, PROTOCOL, now_year=NOW)]
    second = [r.work.id for r in rank_works(list(reversed(works)), PROTOCOL, now_year=NOW)]
    assert first == second  # order independent of input order
    scores = [r.score for r in rank_works(works, PROTOCOL, now_year=NOW)]
    assert scores == sorted(scores, reverse=True)


def test_explanation_exposes_the_decomposition() -> None:
    ranked = rank_works(
        [_work("W1", "transformer attention", year=2022, cites=20)], PROTOCOL, now_year=NOW
    )
    expl = ranked[0].explanation
    for signal in ("relevance", "impact", "recency"):
        assert signal in expl
    assert str(DEFAULT_WEIGHTS["relevance"]) in expl  # weights are visible, not hidden


def test_duplicate_work_ids_are_rejected_before_signal_calculation() -> None:
    works = [
        _work("W1", "transformer attention", year=2024, cites=1),
        _work("W1", "unrelated duplicate id", year=2020, cites=999),
    ]
    with pytest.raises(ValueError, match="unique"):
        rank_works(works, PROTOCOL, now_year=NOW)


def test_negated_query_terms_are_not_rewarded_as_relevance() -> None:
    protocol = ReviewProtocol(
        question="cancer intervention",
        query_string="cancer AND NOT mice",
    )
    mice = _work("W1", "Cancer intervention in mice", year=2024, cites=0)
    adults = _work("W2", "Cancer intervention in adults", year=2024, cites=0)

    ranked = {item.work.id: item for item in rank_works([mice, adults], protocol, now_year=NOW)}

    assert ranked["W1"].signals.relevance == ranked["W2"].signals.relevance


def test_double_negation_restores_positive_query_vocabulary() -> None:
    protocol = ReviewProtocol(question="cancer", query_string="cancer AND NOT NOT adults")
    with_adults = _work("W1", "Cancer intervention in adults", year=2024, cites=0)
    without_adults = _work("W2", "Cancer intervention", year=2024, cites=0)

    ranked = {
        item.work.id: item
        for item in rank_works([with_adults, without_adults], protocol, now_year=NOW)
    }

    assert ranked["W1"].signals.relevance > ranked["W2"].signals.relevance
