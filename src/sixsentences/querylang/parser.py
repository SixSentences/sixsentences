"""Parser for boolean search strings -> AST.

Grammar (recursive descent):
    or_expr  := and_expr (OR and_expr)*
    and_expr := unary (AND? unary)*        # adjacency = implicit AND
    unary    := NOT unary | atom
    atom     := '(' or_expr ')' | term
    term     := [field ':'] (WORD['*'] | '"' phrase '"')

Operators are case-insensitive keywords (AND/OR/NOT). Fields: title, abstract.
"""

import re
from dataclasses import dataclass

from sixsentences.querylang.ast import And, Field_, Node, Not, Or, Term


class QueryParseError(ValueError):
    """Raised when a boolean query is malformed."""


_TOKEN_RE = re.compile(
    r"""
    \s*(
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<phrase>"[^"]*")
      | (?P<word>[^\s()"]+)
    )
    """,
    re.VERBOSE,
)


@dataclass
class _Tok:
    kind: str
    value: str


def _tokenize(text: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            if text[pos:].strip() == "":
                break
            raise QueryParseError(f"cannot tokenize at position {pos}: {text[pos : pos + 20]!r}")
        pos = m.end()
        if m.group("lparen"):
            tokens.append(_Tok("lparen", "("))
        elif m.group("rparen"):
            tokens.append(_Tok("rparen", ")"))
        elif m.group("phrase"):
            tokens.append(_Tok("phrase", m.group("phrase")[1:-1]))
        else:
            word = m.group("word")
            upper = word.upper()
            if upper in ("AND", "OR", "NOT"):
                tokens.append(_Tok(upper, word))
            else:
                tokens.append(_Tok("word", word))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Tok]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> _Tok | None:
        """Return the next token without consuming it."""

        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> _Tok:
        """Consume and return the next token."""

        tok = self.peek()
        if tok is None:
            raise QueryParseError("unexpected end of query")
        self.pos += 1
        return tok

    def parse(self) -> Node:
        """Parse all tokens and reject trailing input."""

        node = self.or_expr()
        if self.peek() is not None:
            raise QueryParseError(f"unexpected token: {self.peek()!r}")
        return node

    def or_expr(self) -> Node:
        """Parse an OR expression."""

        children = [self.and_expr()]
        while (tok := self.peek()) is not None and tok.kind == "OR":
            self.next()
            children.append(self.and_expr())
        return children[0] if len(children) == 1 else Or(tuple(children))

    def and_expr(self) -> Node:
        """Parse explicit or implicit conjunctions."""

        children = [self.unary()]
        while (tok := self.peek()) is not None:
            if tok.kind == "AND":
                self.next()
                children.append(self.unary())
            elif tok.kind in ("word", "phrase", "lparen", "NOT"):
                children.append(self.unary())  # implicit AND
            else:
                break
        return children[0] if len(children) == 1 else And(tuple(children))

    def unary(self) -> Node:
        """Parse unary negation or an atom."""

        tok = self.peek()
        if tok is not None and tok.kind == "NOT":
            self.next()
            return Not(self.unary())
        return self.atom()

    def atom(self) -> Node:
        """Parse a parenthesized expression, phrase, or term."""

        tok = self.next()
        if tok.kind == "lparen":
            node = self.or_expr()
            closing = self.next()
            if closing.kind != "rparen":
                raise QueryParseError("expected ')'")
            return node
        if tok.kind == "phrase":
            phrase = tok.value.strip()
            if not phrase:
                raise QueryParseError("empty phrase")
            return Term(phrase, phrase=True)
        if tok.kind == "word":
            prefix = tok.value.lower()
            if prefix in ("title:", "abstract:"):
                return self._field_value(Field_(prefix[:-1]))
            return self._term_from_word(tok.value)
        raise QueryParseError(f"unexpected token: {tok!r}")

    def _field_value(self, field: Field_) -> Term:
        """Consume a value separated from a recognized ``field:`` token."""

        value = self.peek()
        if value is None:
            raise QueryParseError(f"missing value after {field.value}:")
        if value.kind == "phrase":
            self.next()
            phrase = value.value.strip()
            if not phrase:
                raise QueryParseError(f"empty phrase after {field.value}:")
            return Term(phrase, field=field, phrase=True)
        if value.kind == "word" and ":" not in value.value:
            self.next()
            return self._bare_term(value.value, field=field)
        raise QueryParseError(f"expected a term or quoted phrase after {field.value}:")

    def _term_from_word(self, word: str) -> Term:
        field = Field_.ANY
        if ":" in word:
            prefix, _, rest = word.partition(":")
            if prefix.lower() not in ("title", "abstract"):
                raise QueryParseError(f"unsupported field prefix: {prefix!r}")
            if not rest or ":" in rest:
                raise QueryParseError(f"invalid value after {prefix.lower()}:")
            field = Field_(prefix.lower())
            word = rest
        return self._bare_term(word, field=field)

    @staticmethod
    def _bare_term(word: str, *, field: Field_) -> Term:
        if "*" in word and (word.count("*") != 1 or not word.endswith("*")):
            raise QueryParseError("wildcard must be a single trailing '*'")
        truncated = word.endswith("*")
        if truncated:
            word = word[:-1]
        if not word:
            raise QueryParseError("empty term")
        return Term(word, field=field, truncated=truncated)


def parse_query(text: str) -> Node:
    """Parse a boolean query string into the canonical AST."""

    tokens = _tokenize(text)
    if not tokens:
        raise QueryParseError("empty query")
    try:
        return _Parser(tokens).parse()
    except RecursionError:  # pathologically nested parens -> a clean 400, not a 500
        raise QueryParseError("query nesting too deep") from None
