"""Per-database translations of the canonical boolean query AST.

The local index and OpenAlex execute the query directly. These additional
compilers emit the same boolean logic in PubMed, Scopus, Web of Science, and
IEEE Xplore syntax for caller-managed multi-database workflows.
"""

import re
from collections.abc import Callable

from sixsentences.querylang.ast import And, Field_, Node, Not, Or, Term

_SAFE_BARE_TERM = re.compile(r"^[^\W_]\w*$", re.UNICODE)
_UNSAFE_LITERAL = re.compile(r'["\\\x00-\x1f\x7f]')


class QueryTranslationError(ValueError):
    """Raised when one literal cannot be represented safely across targets."""


def _body(term: Term) -> str:
    text = term.text
    if not text or text != text.strip():
        raise QueryTranslationError("query terms must be non-empty and trimmed")
    if _UNSAFE_LITERAL.search(text):
        raise QueryTranslationError("quotes, backslashes, and control characters are unsupported")
    if term.truncated:
        if term.phrase or _SAFE_BARE_TERM.fullmatch(text) is None:
            raise QueryTranslationError(
                "truncation is supported only for a single alphanumeric term"
            )
        return text + "*"
    if term.phrase or _SAFE_BARE_TERM.fullmatch(text) is None:
        return f'"{text}"'
    return text


def _pubmed_term(term: Term) -> str:
    tag = {Field_.ANY: "[tiab]", Field_.TITLE: "[ti]", Field_.ABSTRACT: "[ab]"}[term.field]
    return f"{_body(term)}{tag}"


def _scopus_term(term: Term) -> str:
    func = {
        Field_.ANY: "TITLE-ABS-KEY",
        Field_.TITLE: "TITLE",
        Field_.ABSTRACT: "ABS",
    }[term.field]
    return f"{func}({_body(term)})"


def _wos_term(term: Term) -> str:
    tag = {Field_.ANY: "TS", Field_.TITLE: "TI", Field_.ABSTRACT: "AB"}[term.field]
    return f"{tag}=({_body(term)})"


def _ieee_term(term: Term) -> str:
    tag = {
        Field_.ANY: "All Metadata",
        Field_.TITLE: "Document Title",
        Field_.ABSTRACT: "Abstract",
    }[term.field]
    return f'("{tag}":{_body(term)})'


def _compile(node: Node, term_fn: Callable[[Term], str]) -> str:
    match node:
        case Term():
            return term_fn(node)
        case And(children=children):
            return "(" + " AND ".join(_compile(c, term_fn) for c in children) + ")"
        case Or(children=children):
            return "(" + " OR ".join(_compile(c, term_fn) for c in children) + ")"
        case Not(child=child):
            return f"NOT {_compile(child, term_fn)}"
    raise TypeError(f"unknown node: {node!r}")


def translations(node: Node) -> dict[str, str]:
    """The query in every supported target syntax, ready to paste."""
    return {
        "pubmed": _compile(node, _pubmed_term),
        "scopus": _compile(node, _scopus_term),
        "wos": _compile(node, _wos_term),
        "ieee": _compile(node, _ieee_term),
    }
