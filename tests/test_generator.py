"""Tests for the SQL generator."""

import random

import pytest

from peg_fuzzer.grammar.parser import load_grammar_dir
from peg_fuzzer.generator.generator import Generator

GRAMMAR_DIR = "duckdb/extension/autocomplete/grammar"


@pytest.fixture(scope="module")
def grammar():
    return load_grammar_dir(GRAMMAR_DIR)


@pytest.fixture
def gen(grammar):
    return Generator(grammar, random.Random(42))


def test_generate_returns_string(gen):
    sql = gen.generate("SelectStatement")
    assert isinstance(sql, str)
    assert len(sql) > 0


def test_generate_select_nonempty(gen):
    # SelectStatement can expand to VALUES / TABLE / DESCRIBE / etc. -- just check non-empty
    for _ in range(20):
        sql = gen.generate("SelectStatement")
        assert len(sql) > 0


def test_generate_statement_runs(gen):
    # Should generate without exception for many iterations.
    for _ in range(50):
        gen.generate("Statement")


def test_generate_reproducible():
    g = load_grammar_dir(GRAMMAR_DIR)
    sql1 = Generator(g, random.Random(0)).generate("SelectStatement")
    sql2 = Generator(g, random.Random(0)).generate("SelectStatement")
    assert sql1 == sql2


def test_generate_insert(gen):
    sql = gen.generate("InsertStatement")
    assert "INSERT" in sql.upper()


def test_generate_create_table(gen):
    sql = gen.generate("CreateStatement")
    assert "CREATE" in sql.upper()


def test_generate_different_seeds_differ():
    g = load_grammar_dir(GRAMMAR_DIR)
    results = {Generator(g, random.Random(i)).generate("Statement") for i in range(10)}
    assert len(results) > 1
