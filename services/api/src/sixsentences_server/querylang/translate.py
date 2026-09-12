"""Per-database translations of the canonical boolean query AST.

The pinned index and OpenAlex execute the query natively; other databases
(PubMed, Scopus, Web of Science, IEEE Xplore) cannot be searched from here,
but reviewers routinely require them. These compilers emit the same boolean
logic in each database's field syntax, so the researcher runs it there and
uploads the export back into the run (see /imports) — the PRISMA-compliant
multi-database workflow.
"""

from collections.abc import Callable

from sixsentences_server.querylang.ast import And, Field_, Node, Not, Or, Term


def _body(term: Term) -> str:
    text = f'"{term.text}"' if term.phrase else term.text
    if term.truncated:
        text += "*"
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
