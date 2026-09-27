"""Database translations quote literals and reject ambiguous unsafe inputs."""

import pytest

from sixsentences.querylang.ast import Field_, Term
from sixsentences.querylang.compile_openalex import OpenAlexCompileError, compile_openalex
from sixsentences.querylang.parser import parse_query
from sixsentences.querylang.translate import QueryTranslationError, translate, translations


def test_field_scoped_phrase_translates_for_every_database() -> None:
    rendered = translations(parse_query('title:"deep learning"'))
    assert rendered == {
        "pubmed": '"deep learning"[ti]',
        "scopus": 'TITLE("deep learning")',
        "wos": 'TI=("deep learning")',
        "ieee": '("Document Title":"deep learning")',
        "central": '"deep learning":ti',
    }


def test_special_character_term_is_quoted_as_one_literal() -> None:
    rendered = translations(parse_query("C++"))
    assert rendered["pubmed"] == '"C++"[tiab]'
    assert rendered["scopus"] == 'TITLE-ABS-KEY("C++")'
    assert rendered["central"] == '"C++":ti,ab,kw'
    assert compile_openalex(parse_query("C++"))[0] == '"C++"'


@pytest.mark.parametrize("text", ['x" OR y', "x\\y", "x\ny"])
def test_programmatic_unsafe_literals_fail_closed(text: str) -> None:
    with pytest.raises(QueryTranslationError):
        translations(Term(text, field=Field_.TITLE))
    with pytest.raises(QueryTranslationError):
        translate(Term(text, field=Field_.TITLE), "central")
    with pytest.raises(OpenAlexCompileError):
        compile_openalex(Term(text))


def test_truncation_rejects_punctuated_or_phrase_terms() -> None:
    with pytest.raises(QueryTranslationError):
        translations(Term("C++", truncated=True))
    with pytest.raises(QueryTranslationError):
        translations(Term("deep learning", phrase=True, truncated=True))


# Cochrane CENTRAL: the rules are those of the Cochrane Library Search Manager,
# cited in translate.py.


def test_central_labels_every_term_with_its_field() -> None:
    query = parse_query('title:"deep learning" OR abstract:screen* OR evidence')
    assert translate(query, "central") == '("deep learning":ti OR screen*:ab OR evidence:ti,ab,kw)'


def test_central_writes_not_between_its_operands() -> None:
    assert translate(parse_query("trial NOT animal*"), "central") == (
        "(trial:ti,ab,kw NOT animal*:ti,ab,kw)"
    )
    # Every negated child is subtracted from the conjunction of the others.
    query = parse_query("(adult OR elderly) AND NOT child trial NOT title:rat")
    assert translate(query, "central") == (
        "(((adult:ti,ab,kw OR elderly:ti,ab,kw) AND trial:ti,ab,kw) NOT (child:ti,ab,kw OR rat:ti))"
    )


@pytest.mark.parametrize("query", ["NOT animal", "trial OR NOT animal", "NOT animal NOT rat"])
def test_central_refuses_a_not_with_nothing_to_subtract_from(query: str) -> None:
    node = parse_query(query)
    with pytest.raises(QueryTranslationError, match="no unary NOT"):
        translate(node, "central")
    assert translate(node, "pubmed")


@pytest.mark.parametrize("word", ["and", "OR", "Not", "near", "NEXT"])
def test_central_quotes_a_term_spelled_like_an_operator(word: str) -> None:
    assert translate(Term(word, field=Field_.TITLE), "central") == f'"{word}":ti'


def test_central_quotes_the_proximity_words_the_parser_reads_as_terms() -> None:
    assert translate(parse_query("hearing near aid"), "central") == (
        '(hearing:ti,ab,kw AND "near":ti,ab,kw AND aid:ti,ab,kw)'
    )
    with pytest.raises(QueryTranslationError, match="operator"):
        translate(parse_query("near*"), "central")


def test_central_truncates_only_roots_of_three_characters() -> None:
    assert translate(parse_query("abc*"), "central") == "abc*:ti,ab,kw"
    with pytest.raises(QueryTranslationError, match="three characters"):
        translate(parse_query("ab*"), "central")


@pytest.mark.parametrize(
    "query",
    [
        "wom?n",
        '"hearing aid*"',
        '"COVID-19: a review"',
        "#1",
        "neoplasm{majr}",
        "neoplasm[mesh]",
        "~cancer",
        "behavio!r",
        "cancer$",
        '"vitamin (D)"',
        "Crohn's",
        "Crohn\u2019s",
        "\u201clung cancer\u201d",
        "\u2018lung cancer\u2019",
    ],
)
def test_central_refuses_a_literal_it_would_read_as_syntax(query: str) -> None:
    node = parse_query(query)
    with pytest.raises(QueryTranslationError, match="literally"):
        translate(node, "central")
    # Only CENTRAL refuses; translations() still promises every target.
    assert translate(node, "pubmed")
    with pytest.raises(QueryTranslationError):
        translations(node)
