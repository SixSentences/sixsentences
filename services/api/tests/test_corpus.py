"""MicroCorpus store: offline boolean search against parquet."""

import pytest

from sixsentences_server.corpus.duckdb_store import CorpusNotSyncedError, DuckDBCorpus
from sixsentences_server.querylang.parser import parse_query


def test_search_boolean_and(corpus: DuckDBCorpus) -> None:
    hits = corpus.search(parse_query("transformer AND attention"))
    assert [w.id for w in hits] == ["W1"]


def test_search_or_and_ordering_by_citations(corpus: DuckDBCorpus) -> None:
    hits = corpus.search(parse_query("transformer OR screening"))
    # W2's abstract mentions screening; ordering is by citation count desc
    assert [w.id for w in hits] == ["W1", "W2", "W3"]


def test_search_phrase(corpus: DuckDBCorpus) -> None:
    hits = corpus.search(parse_query('"active learning"'))
    assert [w.id for w in hits] == ["W2"]


def test_relaxed_search_keeps_concepts_but_tolerates_phrase_variants(
    corpus: DuckDBCorpus,
) -> None:
    query = parse_query(
        '("active learner" OR "participant screening") AND (review OR classification)'
    )

    assert corpus.search(query) == []
    assert [work.id for work in corpus.search_relaxed(query)] == ["W2"]


def test_relaxed_search_respects_year_window_and_negation(corpus: DuckDBCorpus) -> None:
    query = parse_query('("learning method") AND NOT survey')

    assert {work.id for work in corpus.search_relaxed(query)} == {"W3"}
    assert corpus.search_relaxed(query, year_to=2020) == []


def test_search_not(corpus: DuckDBCorpus) -> None:
    hits = corpus.search(parse_query("learning AND NOT survey"))
    assert {w.id for w in hits} == {"W3"}


def test_search_wildcard(corpus: DuckDBCorpus) -> None:
    hits = corpus.search(parse_query("screen*"))
    assert {w.id for w in hits} == {"W2", "W3"}


def test_search_year_window(corpus: DuckDBCorpus) -> None:
    query = parse_query("learning")  # W2 (2020) + W3 (2021)
    assert {w.id for w in corpus.search(query)} == {"W2", "W3"}
    assert {w.id for w in corpus.search(query, year_from=2021)} == {"W3"}
    assert {w.id for w in corpus.search(query, year_to=2020)} == {"W2"}
    assert {w.id for w in corpus.search(query, year_from=2020, year_to=2020)} == {"W2"}


def test_retracted_flag_round_trips(corpus: DuckDBCorpus) -> None:
    hits = corpus.search(parse_query("retracted"))
    assert hits and hits[0].is_retracted is True


def test_unsynced_corpus_raises(settings) -> None:  # type: ignore[no-untyped-def]
    empty = DuckDBCorpus(settings.data_dir / "nowhere")
    with pytest.raises(CorpusNotSyncedError):
        empty.search(parse_query("x"))


def test_version_metadata(corpus: DuckDBCorpus) -> None:
    version = corpus.version()
    assert version.version == "micro-test-3"
    assert version.works == 3
    assert corpus.count() == 3
