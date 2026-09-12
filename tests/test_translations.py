"""Database translations quote literals and reject ambiguous unsafe inputs."""

import pytest

from sixsentences.querylang.ast import Field_, Term
from sixsentences.querylang.compile_openalex import OpenAlexCompileError, compile_openalex
from sixsentences.querylang.parser import parse_query
from sixsentences.querylang.translate import QueryTranslationError, translations


def test_field_scoped_phrase_translates_for_every_database() -> None:
    rendered = translations(parse_query('title:"deep learning"'))
    assert rendered == {
        "pubmed": '"deep learning"[ti]',
        "scopus": 'TITLE("deep learning")',
        "wos": 'TI=("deep learning")',
        "ieee": '("Document Title":"deep learning")',
    }


def test_special_character_term_is_quoted_as_one_literal() -> None:
    rendered = translations(parse_query("C++"))
    assert rendered["pubmed"] == '"C++"[tiab]'
    assert rendered["scopus"] == 'TITLE-ABS-KEY("C++")'
    assert compile_openalex(parse_query("C++"))[0] == '"C++"'


@pytest.mark.parametrize("text", ['x" OR y', "x\\y", "x\ny"])
def test_programmatic_unsafe_literals_fail_closed(text: str) -> None:
    with pytest.raises(QueryTranslationError):
        translations(Term(text, field=Field_.TITLE))
    with pytest.raises(OpenAlexCompileError):
        compile_openalex(Term(text))


def test_truncation_rejects_punctuated_or_phrase_terms() -> None:
    with pytest.raises(QueryTranslationError):
        translations(Term("C++", truncated=True))
    with pytest.raises(QueryTranslationError):
        translations(Term("deep learning", phrase=True, truncated=True))
