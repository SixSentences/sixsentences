"""Compile the boolean AST for OpenAlex's stemmed ``search`` parameter.

OpenAlex uses Elasticsearch query_string syntax: uppercase AND/OR/NOT, quoted
phrases, parentheses. OpenAlex also offers different capabilities through
``search.exact`` and OQL, including wildcard and field-aware forms. This small
compiler intentionally targets only stemmed ``search``. Its limitations are:

- trailing-wildcard truncation is dropped (stemming may still recover variants,
  but recall differs from the corpus target); and
- field scope is dropped because this target searches across its indexed text.

Both degradations are surfaced to the caller via `CompileNotes`.
"""

import re
from dataclasses import dataclass, field

from sixsentences.querylang.ast import And, Field_, Node, Not, Or, Term

_SAFE_BARE_TERM = re.compile(r"^[^\W_]\w*$", re.UNICODE)
_UNSAFE_LITERAL = re.compile(r'["\\\x00-\x1f\x7f]')


class OpenAlexCompileError(ValueError):
    """Raised when a literal cannot be represented safely in stemmed search."""


@dataclass
class CompileNotes:
    """Losses introduced by the stemmed-search target."""

    dropped_wildcards: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)


def compile_openalex(node: Node) -> tuple[str, CompileNotes]:
    """Compile an AST and report every target-specific degradation."""

    notes = CompileNotes()
    return _compile(node, notes), notes


def _compile(node: Node, notes: CompileNotes) -> str:
    match node:
        case Term(text=text, field=f, phrase=phrase, truncated=truncated):
            if not text or text != text.strip():
                raise OpenAlexCompileError("query terms must be non-empty and trimmed")
            if _UNSAFE_LITERAL.search(text):
                raise OpenAlexCompileError(
                    "quotes, backslashes, and control characters are unsupported"
                )
            if truncated:
                notes.dropped_wildcards.append(text)
            if f is not Field_.ANY:
                notes.dropped_fields.append(f"{f.value}:{text}")
            return f'"{text}"' if phrase or _SAFE_BARE_TERM.fullmatch(text) is None else text
        case And(children=children):
            return "(" + " AND ".join(_compile(c, notes) for c in children) + ")"
        case Or(children=children):
            return "(" + " OR ".join(_compile(c, notes) for c in children) + ")"
        case Not(child=child):
            return f"NOT {_compile(child, notes)}"
    raise TypeError(f"unknown node: {node!r}")
