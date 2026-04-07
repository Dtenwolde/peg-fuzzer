"""Tests for grammar/parser.py."""

import pytest

from peg_fuzzer.grammar.model import PEGTokenType
from peg_fuzzer.grammar.parser import load_grammar_dir, parse_grammar

GRAMMAR_DIR = "duckdb/extension/autocomplete/grammar"

SIMPLE_GRAMMAR = """
# a comment
Foo <- 'SELECT' Bar?
Bar <- 'FROM' Identifier
"""

PARAM_GRAMMAR = """
List(D) <- D (',' D)*
Parens(D) <- '(' D ')'
"""


def test_parse_simple():
    g = parse_grammar(SIMPLE_GRAMMAR)
    assert "Foo" in g.rules
    assert "Bar" in g.rules
    foo = g.rules["Foo"]
    assert foo.tokens[0].type == PEGTokenType.LITERAL
    assert foo.tokens[0].text == "SELECT"
    assert foo.tokens[1].type == PEGTokenType.REFERENCE
    assert foo.tokens[2].type == PEGTokenType.OPERATOR
    assert foo.tokens[2].text == "?"


def test_parse_parameterized():
    g = parse_grammar(PARAM_GRAMMAR)
    assert "List" in g.rules
    assert "Parens" in g.rules
    lst = g.rules["List"]
    assert "D" in lst.parameters
    parens = g.rules["Parens"]
    assert "D" in parens.parameters


def test_parse_comment_ignored():
    g = parse_grammar("# just a comment\nFoo <- 'X'\n")
    assert "Foo" in g.rules


def test_parse_or_multiline():
    src = "Foo <-\n  'A' /\n  'B'\n"
    g = parse_grammar(src)
    assert "Foo" in g.rules
    tokens = g.rules["Foo"].tokens
    types = [t.type for t in tokens]
    assert PEGTokenType.OPERATOR in types  # should have '/'


def test_duplicate_rule_raises():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_grammar("Foo <- 'A'\nFoo <- 'B'\n")


def test_load_grammar_dir_has_statement():
    g = load_grammar_dir(GRAMMAR_DIR)
    assert "Statement" in g.rules


def test_load_grammar_dir_has_select():
    g = load_grammar_dir(GRAMMAR_DIR)
    assert "SelectStatement" in g.rules


def test_load_grammar_dir_rule_count():
    g = load_grammar_dir(GRAMMAR_DIR)
    # sanity check -- the grammar has hundreds of rules
    assert len(g.rules) > 100


def test_load_grammar_dir_builtin_list():
    g = load_grammar_dir(GRAMMAR_DIR)
    assert "List" in g.rules
    assert "D" in g.rules["List"].parameters


def test_load_grammar_dir_builtin_parens():
    g = load_grammar_dir(GRAMMAR_DIR)
    assert "Parens" in g.rules
    assert "D" in g.rules["Parens"].parameters
