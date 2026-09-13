"""Parser for boolean search strings -> AST.

Grammar (recursive descent):
    or_expr  := and_expr (OR and_expr)*
    and_expr := unary (AND? unary)*        # adjacency = implicit AND
    unary    := NOT unary | atom
    atom     := '(' or_expr ')' | term
    term     := [field ':'] (WORD['*'] | '"' phrase '"')

Operators are case-insensitive keywords (AND/OR/NOT). Fields: title, abstract.
"""

from dataclasses import dataclass

from sixsentences_server.querylang.ast import And, Field_, Node, Not, Or, Term


class QueryParseError(ValueError):
    pass


@dataclass
class _Tok:
    kind: str
    value: str


def _tokenize(text: str) -> list[_Tok]:
    """Tokenize in one pass without input-dependent regular-expression backtracking."""

    tokens: list[_Tok] = []
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            break
        character = text[pos]
        if character == "(":
            tokens.append(_Tok("lparen", "("))
            pos += 1
            continue
        if character == ")":
            tokens.append(_Tok("rparen", ")"))
            pos += 1
            continue
        if character == '"':
            closing = text.find('"', pos + 1)
            if closing < 0:
                raise QueryParseError(f"cannot tokenize at position {pos}: unterminated phrase")
            tokens.append(_Tok("phrase", text[pos + 1 : closing]))
            pos = closing + 1
            continue
        start = pos
        while pos < len(text) and not text[pos].isspace() and text[pos] not in '()"':
            pos += 1
        if start == pos:
            raise QueryParseError(f"cannot tokenize at position {pos}: {text[pos : pos + 20]!r}")
        word = text[start:pos]
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
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> _Tok:
        tok = self.peek()
        if tok is None:
            raise QueryParseError("unexpected end of query")
        self.pos += 1
        return tok

    def parse(self) -> Node:
        node = self.or_expr()
        if self.peek() is not None:
            raise QueryParseError(f"unexpected token: {self.peek()!r}")
        return node

    def or_expr(self) -> Node:
        children = [self.and_expr()]
        while (tok := self.peek()) is not None and tok.kind == "OR":
            self.next()
            children.append(self.and_expr())
        return children[0] if len(children) == 1 else Or(tuple(children))

    def and_expr(self) -> Node:
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
        tok = self.peek()
        if tok is not None and tok.kind == "NOT":
            self.next()
            return Not(self.unary())
        return self.atom()

    def atom(self) -> Node:
        tok = self.next()
        if tok.kind == "lparen":
            node = self.or_expr()
            closing = self.next()
            if closing.kind != "rparen":
                raise QueryParseError("expected ')'")
            return node
        if tok.kind == "phrase":
            return Term(tok.value, phrase=True)
        if tok.kind == "word":
            return self._term_from_word(tok.value)
        raise QueryParseError(f"unexpected token: {tok!r}")

    def _term_from_word(self, word: str) -> Term:
        field = Field_.ANY
        if ":" in word:
            prefix, _, rest = word.partition(":")
            if prefix.lower() in ("title", "abstract") and rest:
                field = Field_(prefix.lower())
                word = rest
        # a quoted phrase after a field prefix (title:"deep learning") arrives
        # as two tokens; handled by treating the remainder as a plain token.
        truncated = word.endswith("*")
        if truncated:
            word = word.rstrip("*")
        if not word:
            raise QueryParseError("empty term")
        return Term(word, field=field, truncated=truncated)


def parse_query(text: str) -> Node:
    tokens = _tokenize(text)
    if not tokens:
        raise QueryParseError("empty query")
    try:
        return _Parser(tokens).parse()
    except RecursionError:  # pathologically nested parens -> a clean 400, not a 500
        raise QueryParseError("query nesting too deep") from None
