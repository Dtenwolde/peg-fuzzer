"""Parse .gram files into a Grammar IR.

Mirrors the logic in duckdb/extension/autocomplete/parser/peg_parser.cpp.
"""

from __future__ import annotations

import os
from enum import Enum, auto
from pathlib import Path

from .model import Grammar, PEGToken, PEGTokenType, Rule


class _ParseState(Enum):
    RULE_NAME = auto()
    RULE_SEPARATOR = auto()
    RULE_DEFINITION = auto()


_OPERATORS = frozenset("/?(*)+!")


def _is_alphanumeric(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _is_space(ch: str) -> bool:
    return ch in " \t\r\n"


def _is_newline(ch: str) -> bool:
    return ch in "\r\n"


def _is_operator(ch: str) -> bool:
    return ch in _OPERATORS


def parse_grammar(text: str) -> Grammar:
    """Parse a grammar string and return a Grammar."""
    grammar = Grammar()
    _parse_rules(text, grammar)
    return grammar


def _parse_rules(text: str, grammar: Grammar) -> None:
    s = text
    n = len(s)
    c = 0

    rule_name: str = ""
    tokens: list[PEGToken] = []
    parameters: dict[str, int] = {}
    state = _ParseState.RULE_NAME
    bracket_count = 0
    in_or_clause = False

    def flush_rule() -> None:
        nonlocal rule_name, tokens, parameters
        if tokens:
            grammar.add_rule(Rule(name=rule_name, parameters=dict(parameters), tokens=list(tokens)))
        rule_name = ""
        tokens = []
        parameters = {}

    while c < n:
        ch = s[c]

        # comments
        if ch == "#":
            while c < n and not _is_newline(s[c]):
                c += 1
            continue

        # newline while in rule definition can end the rule
        if (
            state == _ParseState.RULE_DEFINITION
            and _is_newline(ch)
            and bracket_count == 0
            and not in_or_clause
            and tokens
        ):
            flush_rule()
            state = _ParseState.RULE_NAME
            c += 1
            continue

        # skip whitespace
        if _is_space(ch):
            c += 1
            continue

        if state == _ParseState.RULE_NAME:
            start = c
            if s[c] == "%":
                c += 1
            while c < n and _is_alphanumeric(s[c]):
                c += 1
            if c == start:
                raise ValueError(f"Expected alphanumeric rule name at pos {c}")
            rule_name = s[start:c]
            tokens = []
            parameters = {}
            state = _ParseState.RULE_SEPARATOR

        elif state == _ParseState.RULE_SEPARATOR:
            if s[c] == "(":
                if parameters:
                    raise ValueError(f"Multiple parameters at pos {c}")
                c += 1
                param_start = c
                while c < n and _is_alphanumeric(s[c]):
                    c += 1
                if param_start == c:
                    raise ValueError(f"Expected parameter name at pos {c}")
                param_name = s[param_start:c]
                parameters[param_name] = len(parameters)
                if c >= n or s[c] != ")":
                    raise ValueError(f"Expected closing ) at pos {c}")
                c += 1
            else:
                if c + 1 >= n or s[c] != "<" or s[c + 1] != "-":
                    raise ValueError(f"Expected <- at pos {c}")
                c += 2
                state = _ParseState.RULE_DEFINITION

        elif state == _ParseState.RULE_DEFINITION:
            in_or_clause = False

            if s[c] == "'":
                # literal
                c += 1
                start = c
                while c < n and s[c] != "'":
                    if s[c] == "\\":
                        c += 1
                    c += 1
                if c >= n:
                    raise ValueError(f"Unclosed literal at pos {c}")
                tokens.append(PEGToken(PEGTokenType.LITERAL, s[start:c]))
                c += 1
                # 'i' suffix (case-insensitive) is not used in this grammar variant
                if c < n and s[c] == "i":
                    raise ValueError(f"Unexpected 'i' suffix in rule {rule_name}")

            elif _is_alphanumeric(s[c]):
                # rule reference or function call
                start = c
                while c < n and _is_alphanumeric(s[c]):
                    c += 1
                ref = s[start:c]
                if c < n and s[c] == "(":
                    # function call -- opening paren is consumed as part of FUNCTION_CALL token
                    c += 1
                    bracket_count += 1
                    tokens.append(PEGToken(PEGTokenType.FUNCTION_CALL, ref))
                else:
                    tokens.append(PEGToken(PEGTokenType.REFERENCE, ref))

            elif s[c] in ("[", "<"):
                # regex token [chars] or <pattern>
                close = "]" if s[c] == "[" else ">"
                start = c
                c += 1
                while c < n and s[c] != close:
                    if s[c] == "\\":
                        c += 1
                    if c < n:
                        c += 1
                c += 1  # consume closing char
                tokens.append(PEGToken(PEGTokenType.REGEX, s[start:c]))

            elif _is_operator(s[c]):
                op = s[c]
                if op == "(":
                    bracket_count += 1
                elif op == ")":
                    if bracket_count == 0:
                        raise ValueError(f"Unclosed ) at pos {c} in rule {rule_name}")
                    bracket_count -= 1
                elif op == "/":
                    in_or_clause = True
                tokens.append(PEGToken(PEGTokenType.OPERATOR, op))
                c += 1
            else:
                raise ValueError(f"Unrecognized char {s[c]!r} in rule {rule_name} at pos {c}")

    # EOF -- flush any pending rule
    if state == _ParseState.RULE_SEPARATOR and rule_name:
        raise ValueError(f"Rule {rule_name} has no definition")
    if state == _ParseState.RULE_DEFINITION:
        flush_rule()


def _keyword_rule_name(filename: str) -> str:
    """Convert e.g. 'reserved_keyword.list' -> 'ReservedKeyword'."""
    stem = filename.replace(".list", "")
    return "".join(p.capitalize() for p in stem.split("_"))


def _build_keyword_rules(grammar_dir: Path) -> Grammar:
    """Generate keyword category rules from .list files.

    Mirrors the logic in inline_grammar.py:
      ReservedKeyword <- 'ALL' / 'ANALYSE' / ...
      UnreservedKeyword <- 'ABORT' / ...
      etc.
    """
    grammar = Grammar()
    kw_dir = grammar_dir / "keywords"
    for list_file in sorted(kw_dir.glob("*.list")):
        rule_name = _keyword_rule_name(list_file.name)
        keywords = [
            line.strip()
            for line in list_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if not keywords:
            continue
        tokens: list[PEGToken] = []
        for i, kw in enumerate(keywords):
            tokens.append(PEGToken(PEGTokenType.LITERAL, kw))
            if i < len(keywords) - 1:
                tokens.append(PEGToken(PEGTokenType.OPERATOR, "/"))
        grammar.add_rule(Rule(name=rule_name, parameters={}, tokens=tokens))
    return grammar


def load_grammar_dir(grammar_dir: str | Path) -> Grammar:
    """Load all .gram files from grammar_dir/statements/ into one Grammar.

    Also generates keyword category rules from grammar_dir/keywords/*.list,
    matching the output of inline_grammar.py.
    """
    grammar_dir = Path(grammar_dir)
    statements_dir = grammar_dir / "statements"
    grammar = Grammar()

    # Load keyword category rules first (ReservedKeyword, UnreservedKeyword, etc.)
    kw_grammar = _build_keyword_rules(grammar_dir)
    for rule in kw_grammar.rules.values():
        grammar.add_rule(rule)

    # common.gram first so built-ins (%whitespace, List, Parens) are defined early
    files = sorted(statements_dir.glob("*.gram"))
    common = statements_dir / "common.gram"
    if common in files:
        files.remove(common)
        files.insert(0, common)

    for path in files:
        text = path.read_text(encoding="utf-8")
        sub = Grammar()
        _parse_rules(text, sub)
        for rule in sub.rules.values():
            grammar.add_rule(rule)

    return grammar
