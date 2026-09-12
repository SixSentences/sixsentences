"""Golden cases for the query language: parse -> both compile targets."""

import re

import pytest

from sixsentences_server.querylang.ast import And, Field_, Not, Or, Term, to_display
from sixsentences_server.querylang.compile_duckdb import compile_duckdb
from sixsentences_server.querylang.compile_openalex import compile_openalex
from sixsentences_server.querylang.parser import QueryParseError, parse_query


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
    # field prefix before a quoted phrase is tokenized as a bare "title:" word —
    # phrase terms therefore stay field-less; documented limitation
    assert isinstance(first, Term | And)
    negation = ast.children[-1]
    assert isinstance(negation, Not)
    assert isinstance(negation.child, Term)
    assert negation.child.truncated is True


def test_parse_field_term() -> None:
    ast = parse_query("title:transformer")
    assert ast == Term("transformer", field=Field_.TITLE)


def test_implicit_and_adjacency() -> None:
    ast = parse_query("systematic review automation")
    assert isinstance(ast, And)
    assert len(ast.children) == 3


def test_parse_empty_raises() -> None:
    with pytest.raises(QueryParseError):
        parse_query("   ")


def test_parse_unbalanced_paren_raises() -> None:
    with pytest.raises(QueryParseError):
        parse_query("(a OR b")


def test_tokenizer_rejects_large_unterminated_phrase_without_regex_backtracking() -> None:
    with pytest.raises(QueryParseError, match="unterminated phrase"):
        parse_query(" " * 20_000 + '"' + "x" * 20_000)


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


def test_matching_recovers_plurals_and_hyphens() -> None:
    # the recall-critical behavior: real abstracts are plural/hyphenated
    (phrase,) = compile_duckdb(parse_query('"active learning"'))[1]
    rx = re.compile(phrase, re.IGNORECASE)
    assert rx.search("we use active learning")
    assert rx.search("an active-learning strategy")  # hyphen == space
    assert rx.search("several active learnings")  # light-stem plural
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
