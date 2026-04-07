"""Tests for the DuckDB runner."""

import pytest

from peg_fuzzer.runner.result import Outcome, Parser
from peg_fuzzer.runner.runner import run_both, run_one


@pytest.mark.parametrize("parser", [Parser.PEG, Parser.POSTGRES])
def test_valid_query_ok(parser):
    result = run_one("SELECT 1", parser)
    assert result.outcome == Outcome.OK
    assert result.duration_ms >= 0
    assert result.parser == parser


@pytest.mark.parametrize("parser", [Parser.PEG, Parser.POSTGRES])
def test_invalid_query_error(parser):
    result = run_one("SELECT FROM", parser)
    assert result.outcome == Outcome.ERROR
    assert result.error_msg


@pytest.mark.parametrize("parser", [Parser.PEG, Parser.POSTGRES])
def test_syntax_error_is_error(parser):
    result = run_one("THIS IS NOT SQL", parser)
    assert result.outcome == Outcome.ERROR


@pytest.mark.parametrize("parser", [Parser.PEG, Parser.POSTGRES])
def test_sql_is_preserved(parser):
    sql = "SELECT 42"
    result = run_one(sql, parser)
    assert result.sql == sql


@pytest.mark.parametrize("parser", [Parser.PEG, Parser.POSTGRES])
def test_valid_create_table(parser):
    result = run_one("CREATE TABLE t (id INTEGER, name VARCHAR)", parser)
    assert result.outcome == Outcome.OK


def test_run_both_returns_compare_result():
    cmp = run_both("SELECT 1")
    assert cmp.peg.outcome == Outcome.OK
    assert cmp.postgres.outcome == Outcome.OK
    assert not cmp.diverged
    assert not cmp.any_crash


def test_run_both_agrees_on_error():
    cmp = run_both("SELECT FROM")
    assert cmp.peg.outcome == Outcome.ERROR
    assert cmp.postgres.outcome == Outcome.ERROR
    assert not cmp.diverged


def test_run_both_sql_preserved():
    sql = "SELECT 123"
    cmp = run_both(sql)
    assert cmp.sql == sql
    assert cmp.peg.sql == sql
    assert cmp.postgres.sql == sql
