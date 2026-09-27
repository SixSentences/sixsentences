"""Per-database translations of the canonical boolean query AST.

The local index and OpenAlex execute the query directly. These additional
compilers emit the same boolean logic in PubMed, Scopus, Web of Science,
IEEE Xplore, and Cochrane CENTRAL syntax for caller-managed multi-database
workflows.
"""

import re
from collections.abc import Callable
from functools import partial

from sixsentences.querylang.ast import And, Field_, Node, Not, Or, Term

_SAFE_BARE_TERM = re.compile(r"^[^\W_]\w*$", re.UNICODE)
_UNSAFE_LITERAL = re.compile(r'["\\\x00-\x1f\x7f]')

# Cochrane CENTRAL, as searched in the Cochrane Library Search Manager. The rules
# below come from its help page (https://www.cochranelibrary.com/search-manager-help)
# and its guide "Understanding error messages".
#
# A term without a field label searches "All text", which is wider than any field
# the other targets use, so every term gets a label:
#   any      -> :ti,ab,kw  title, abstract and keywords, the combination the help
#                          page's own examples use. The keywords are MeSH headings,
#                          MeSH check words, Emtree keywords and CRG keywords, so
#                          this is the scope of Scopus's TITLE-ABS-KEY.
#   title    -> :ti
#   abstract -> :ab
_CENTRAL_LABELS = {Field_.ANY: ":ti,ab,kw", Field_.TITLE: ":ti", Field_.ABSTRACT: ":ab"}
# Characters the Search Manager reads as syntax even inside a quoted phrase, or does
# not support: the wildcards * and ?, the colon before a field label, # line
# references, {} ranges, [] ~ ! $, parentheses, and every quote except the straight
# double quote.
_CENTRAL_UNSAFE = re.compile(r"[*?:#{}\[\]~!$()'‘’“”]")
# Operator words, which the Search Manager reads as a term only when quoted.
_CENTRAL_OPERATORS = frozenset({"and", "or", "not", "near", "next"})
_CENTRAL_UNARY_NOT = (
    "Cochrane CENTRAL has no unary NOT; negate inside a conjunction, as in 'a NOT b'"
)


class QueryTranslationError(ValueError):
    """Raised when a target syntax cannot represent the query safely."""


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


def _central_term(term: Term) -> str:
    body = _body(term)
    if _CENTRAL_UNSAFE.search(term.text):
        raise QueryTranslationError(
            "Cochrane CENTRAL cannot search for * ? : # { } [ ] ~ ! $ ( ) or single "
            "and curly quotes literally"
        )
    if term.text.lower() in _CENTRAL_OPERATORS:
        if term.truncated:
            raise QueryTranslationError("Cochrane CENTRAL cannot truncate an operator word")
        body = f'"{term.text}"'
    elif term.truncated and len(term.text) < 3:
        raise QueryTranslationError(
            "Cochrane CENTRAL truncates only word roots of at least three characters"
        )
    return body + _CENTRAL_LABELS[term.field]


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


def _compile_central(node: Node) -> str:
    """Compile for Cochrane CENTRAL, where NOT is binary.

    The Search Manager refuses two operators in a row, so the ``a AND NOT b`` that
    `_compile` writes is an error there. The negated children of a conjunction are
    subtracted from the others instead, as ``(a AND b) NOT (c OR d)``. That is the
    same set, because the order of a conjunction does not matter.
    """
    match node:
        case Term():
            return _central_term(node)
        case And(children=children):
            kept = [_compile_central(c) for c in children if not isinstance(c, Not)]
            removed = [_compile_central(c.child) for c in children if isinstance(c, Not)]
            if not removed:
                return "(" + " AND ".join(kept) + ")"
            if not kept:
                raise QueryTranslationError(_CENTRAL_UNARY_NOT)
            left = kept[0] if len(kept) == 1 else "(" + " AND ".join(kept) + ")"
            right = removed[0] if len(removed) == 1 else "(" + " OR ".join(removed) + ")"
            return f"({left} NOT {right})"
        case Or(children=children):
            return "(" + " OR ".join(_compile_central(c) for c in children) + ")"
        case Not():
            raise QueryTranslationError(_CENTRAL_UNARY_NOT)
    raise TypeError(f"unknown node: {node!r}")


_TARGETS: dict[str, Callable[[Node], str]] = {
    "pubmed": partial(_compile, term_fn=_pubmed_term),
    "scopus": partial(_compile, term_fn=_scopus_term),
    "wos": partial(_compile, term_fn=_wos_term),
    "ieee": partial(_compile, term_fn=_ieee_term),
    "central": _compile_central,
}


def translate(node: Node, target: str) -> str:
    """The query in one target syntax, ready to paste.

    Raises `QueryTranslationError` when that target cannot express the query,
    whatever the other targets can do with it.
    """
    return _TARGETS[target](node)


def translations(node: Node) -> dict[str, str]:
    """The query in every supported target syntax, ready to paste."""
    return {target: translate(node, target) for target in _TARGETS}
