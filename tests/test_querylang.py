"""Golden cases for the query language: parse -> both compile targets."""

import re

import pytest

from sixsentences.querylang.ast import And, Field_, Not, Or, Term, to_display
from sixsentences.querylang.compile_duckdb import compile_duckdb
from sixsentences.querylang.compile_openalex import compile_openalex
from sixsentences.querylang.parser import QueryParseError, parse_query


def test_parse_simple_and() -> None:
    ast = parse_query("transformer AND attention")
    assert ast == And((Term("transformer"), Term("attention")))


def test_deeply_nested_parens_raise_parse_error_not_recursionerror() -> None:
    # a pathologically nested query is a clean 400 (QueryParseError), never a 500
    with pytest.raises(QueryParseError):
        parse_query("(" * 5000 + "x" + ")" * 5000)


def test_parse_precedence_or_binds_looser_than_and() -> None:
    ast = parse_query("a AND b OR c")
    assert isinstance(ast, Or)
    assert isinstance(ast.children[0], And)


def test_parse_phrase_field_wildcard_not() -> None:
    ast = parse_query('title:"deep learning" AND NOT survey*')
    assert isinstance(ast, And)
    first = ast.children[0]
    assert first == Term("deep learning", field=Field_.TITLE, phrase=True)
    negation = ast.children[-1]
    assert isinstance(negation, Not)
    assert isinstance(negation.child, Term)
    assert negation.child.truncated is True


def test_parse_field_term() -> None:
    ast = parse_query("title:transformer")
    assert ast == Term("transformer", field=Field_.TITLE)


def test_parse_abstract_scoped_phrase() -> None:
    ast = parse_query('abstract:"deep learning"')
    assert ast == Term("deep learning", field=Field_.ABSTRACT, phrase=True)


def test_field_phrase_display_and_duckdb_roundtrip() -> None:
    ast = parse_query('title:"deep learning"')
    assert to_display(ast) == 'title:"deep learning"'
    assert parse_query(to_display(ast)) == ast
    sql, parameters = compile_duckdb(ast)
    assert sql == "regexp_matches(coalesce(title,''), ?, 'i')"
    assert "deep" in parameters[0] and "learning" in parameters[0]


@pytest.mark.parametrize(
    "query",
    [
        "title:",
        "abstract:",
        'title:""',
        'abstract:"   "',
        "title:()",
        "title: AND x",
        "title::value",
        "author:smith",
    ],
)
def test_malformed_field_prefix_raises(query: str) -> None:
    with pytest.raises(QueryParseError):
        parse_query(query)


@pytest.mark.parametrize("query", ["foo**", "fo*o", "*foo", "title:foo**"])
def test_malformed_wildcards_fail_closed(query: str) -> None:
    with pytest.raises(QueryParseError, match="wildcard"):
        parse_query(query)


def test_implicit_and_adjacency() -> None:
    ast = parse_query("systematic review automation")
    assert isinstance(ast, And)
    assert len(ast.children) == 3


def test_parse_empty_raises() -> None:
    with pytest.raises(QueryParseError):
        parse_query("   ")


def test_tokenizer_handles_large_whitespace_runs_in_linear_pass() -> None:
    assert parse_query("\n " * 100_000 + "evidence") == Term("evidence")


def test_unterminated_phrase_fails_at_its_start() -> None:
    with pytest.raises(QueryParseError, match="unterminated phrase at position 6"):
        parse_query('title:"unfinished')


def test_parse_unbalanced_paren_raises() -> None:
    with pytest.raises(QueryParseError):
        parse_query("(a OR b")


def test_display_roundtrip_stability() -> None:
    ast = parse_query('("large language model" OR llm) AND evaluation')
    rendered = to_display(ast)
    assert to_display(parse_query(rendered)) == rendered


def test_compile_duckdb_golden_structure() -> None:
    sql, params = compile_duckdb(parse_query('"active learning" OR screen*'))
    assert sql == (
        "(regexp_matches((coalesce(title,'') || ' ' || coalesce(abstract,'')), ?, 'i')"
        " OR regexp_matches((coalesce(title,'') || ' ' || coalesce(abstract,'')), ?, 'i'))"
    )
    assert len(params) == 2


def test_phrase_matching_preserves_words_and_recovers_hyphens() -> None:
    # the recall-critical behavior: real abstracts are plural/hyphenated
    (phrase,) = compile_duckdb(parse_query('"active learning"'))[1]
    rx = re.compile(phrase, re.IGNORECASE)
    assert rx.search("we use active learning")
    assert rx.search("an active-learning strategy")  # hyphen == space
    assert not rx.search("several active learnings")  # quoted phrase remains exact
    assert not rx.search("passive learning")


def test_matching_is_bounded_not_substring() -> None:
    (pattern,) = compile_duckdb(parse_query("agent"))[1]
    rx = re.compile(pattern, re.IGNORECASE)
    assert rx.search("the agent") and rx.search("many agents")
    assert not rx.search("reagent")  # bounded: no leading-boundary match


def test_truncation_is_open_prefix() -> None:
    (pattern,) = compile_duckdb(parse_query("screen*"))[1]
    rx = re.compile(pattern, re.IGNORECASE)
    assert all(rx.search(w) for w in ("screen", "screening", "screened", "screenshot"))


@pytest.mark.parametrize(
    ("query", "source"),
    [
        ("C++", "We study C++ systems"),
        ("#MeToo", "Evidence about #MeToo"),
        (".NET", ".NET tooling"),
    ],
)
def test_punctuated_terms_have_character_appropriate_boundaries(query: str, source: str) -> None:
    (pattern,) = compile_duckdb(parse_query(query))[1]

    assert re.search(pattern, source, re.IGNORECASE)


def test_compile_duckdb_field_scoping() -> None:
    sql, params = compile_duckdb(parse_query("title:bert"))
    assert "coalesce(title,'')" in sql
    rx = re.compile(params[0], re.IGNORECASE)
    assert rx.search("BERT embeddings") and rx.search("berts")


def test_compile_openalex_golden() -> None:
    query, notes = compile_openalex(parse_query('("deep learning" OR cnn) AND NOT survey'))
    assert query == '(("deep learning" OR cnn) AND NOT survey)'
    assert notes.dropped_wildcards == []


def test_compile_openalex_degradations_surfaced() -> None:
    query, notes = compile_openalex(parse_query("title:bert AND transform*"))
    assert notes.dropped_fields == ["title:bert"]
    assert notes.dropped_wildcards == ["transform"]
    assert query == "(bert AND transform)"
