"""Compile the boolean AST to an OpenAlex `search` query string.

OpenAlex uses Elasticsearch query_string syntax: uppercase AND/OR/NOT, quoted
phrases, parentheses. Documented limitations of this target:
- no trailing-wildcard truncation -> the wildcard is dropped (stem still
  matches via ES analysis, but recall differs from the corpus target)
- field-scoped terms are not expressible inside `search` -> field is dropped
  (OpenAlex searches title+abstract+fulltext); use the corpus target for
  field-precise retrieval.
Both degradations are surfaced to the caller via `CompileNotes`.
"""

from dataclasses import dataclass, field

from sixsentences_server.querylang.ast import And, Field_, Node, Not, Or, Term


@dataclass
class CompileNotes:
    dropped_wildcards: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)


def compile_openalex(node: Node) -> tuple[str, CompileNotes]:
    notes = CompileNotes()
    return _compile(node, notes), notes


def _compile(node: Node, notes: CompileNotes) -> str:
    match node:
        case Term(text=text, field=f, phrase=phrase, truncated=truncated):
            if truncated:
                notes.dropped_wildcards.append(text)
            if f is not Field_.ANY:
                notes.dropped_fields.append(f"{f.value}:{text}")
            return f'"{text}"' if phrase or " " in text else text
        case And(children=children):
            return "(" + " AND ".join(_compile(c, notes) for c in children) + ")"
        case Or(children=children):
            return "(" + " OR ".join(_compile(c, notes) for c in children) + ")"
        case Not(child=child):
            return f"NOT {_compile(child, notes)}"
    raise TypeError(f"unknown node: {node!r}")
