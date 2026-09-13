"""Compile the boolean AST to a DuckDB WHERE condition.

Matching semantics: case-insensitive regexp per term against title/abstract.

Recall matters more than precision at the retrieval stage (screening and
ranking filter afterwards), so a plain term carries **light stemming**: it
also matches common English inflections (plural, gerund, past tense, agent
noun). Measured on the CS MicroCorpus this roughly doubles hits for
plural-heavy vocabulary ("agent" 58->108, "large language model" 10->56) —
exact `\bword\b` matching silently drops most real documents because abstracts
are written in the plural. Words inside a phrase may be separated by spaces or
hyphens ("decision making" == "decision-making"). Truncated terms
("transform*") still match any word continuation. Returns (sql_condition,
params) with '?' placeholders in traversal order.
"""

import re

from sixsentences_server.querylang.ast import And, Field_, Node, Not, Or, Term

_COLS = {
    Field_.ANY: "(coalesce(title,'') || ' ' || coalesce(abstract,''))",
    Field_.TITLE: "coalesce(title,'')",
    Field_.ABSTRACT: "coalesce(abstract,'')",
}

# Common English inflectional endings, appended optionally to a term so a query
# for "model"/"agent" also matches "models"/"agents"/"modeling". Bounded (unlike
# a bare \w* wildcard) so short stems don't over-match ("art" !-> "artificial").
_STEM_SUFFIX = r"(?:e?s|ing|e?d|ings|ion|ions|ers?|ors?|ality|ally)?"


def _term_pattern(term: Term) -> str:
    words = [re.escape(word) for word in term.text.split()]
    core = r"[\s\-]+".join(words)  # phrase words split by spaces or hyphens
    if term.truncated:
        return rf"\b{core}\w*"  # explicit wildcard: prefix match, no closing \b
    return rf"\b{core}{_STEM_SUFFIX}\b"


def compile_duckdb(node: Node) -> tuple[str, list[str]]:
    match node:
        case Term():
            return f"regexp_matches({_COLS[node.field]}, ?, 'i')", [_term_pattern(node)]
        case And(children=children):
            parts, params = _compile_children(children)
            return "(" + " AND ".join(parts) + ")", params
        case Or(children=children):
            parts, params = _compile_children(children)
            return "(" + " OR ".join(parts) + ")", params
        case Not(child=child):
            sql, params = compile_duckdb(child)
            return f"(NOT {sql})", params
    raise TypeError(f"unknown node: {node!r}")


def _compile_children(children: tuple[Node, ...]) -> tuple[list[str], list[str]]:
    parts: list[str] = []
    params: list[str] = []
    for child in children:
        sql, child_params = compile_duckdb(child)
        parts.append(sql)
        params.extend(child_params)
    return parts, params
