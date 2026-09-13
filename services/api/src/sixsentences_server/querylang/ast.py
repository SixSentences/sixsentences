"""Boolean query AST.

One canonical representation; per-source compilers translate it. This is the
seam that later grows vocabulary mapping (MeSH/Emtree) and per-database syntax
targets (PubMed, Scopus, ...) without touching callers.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Field_(StrEnum):
    ANY = "any"
    TITLE = "title"
    ABSTRACT = "abstract"


@dataclass(frozen=True)
class Term:
    text: str
    field: Field_ = Field_.ANY
    phrase: bool = False  # exact phrase vs single token
    # trailing-wildcard truncation ("transform*"); only suffix wildcards supported
    truncated: bool = False


@dataclass(frozen=True)
class And:
    children: tuple["Node", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Or:
    children: tuple["Node", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Not:
    child: "Node" = None  # type: ignore[assignment]


Node = Term | And | Or | Not


def to_display(node: Node) -> str:
    """Render the AST back to a normalized, human-readable boolean string."""
    match node:
        case Term(text=text, field=f, phrase=phrase, truncated=truncated):
            body = f'"{text}"' if phrase else text
            if truncated:
                body += "*"
            return f"{f.value}:{body}" if f is not Field_.ANY else body
        case And(children=children):
            return "(" + " AND ".join(to_display(c) for c in children) + ")"
        case Or(children=children):
            return "(" + " OR ".join(to_display(c) for c in children) + ")"
        case Not(child=child):
            return f"NOT {to_display(child)}"
    raise TypeError(f"unknown node: {node!r}")
