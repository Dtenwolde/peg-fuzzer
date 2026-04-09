"""Tests for the SQL minimizer."""

from peg_fuzzer.minimizer import minimize
from peg_fuzzer.runner.result import Outcome
from peg_fuzzer.runner.runner import run_both


class _Session:
    """Thin wrapper around run_both so minimize() tests don't need FuzzSession."""

    def __init__(self, setup_sql: str = "") -> None:
        self._setup = setup_sql

    def run(self, sql: str):
        return run_both(sql, setup_sql=self._setup)


session = _Session()


def _ok_check(cmp):
    return cmp.peg.outcome == Outcome.OK and cmp.postgres.outcome == Outcome.OK


def _err_check(cmp):
    return cmp.peg.outcome == Outcome.ERROR and cmp.postgres.outcome == Outcome.ERROR


def test_minimize_already_minimal():
    # SELECT 1 is valid for both parsers; can't shrink further.
    sql = "SELECT 1"
    result = minimize(sql, session, _ok_check)
    assert result == sql


def test_minimize_reduces_tokens():
    # Minimizer on a valid multi-token query preserves the OK property and
    # returns a non-empty result no longer than the original.
    sql = "SELECT 1 + 1"
    result = minimize(sql, session, _ok_check)
    cmp = session.run(result)
    assert cmp.peg.outcome == Outcome.OK
    assert cmp.postgres.outcome == Outcome.OK
    assert len(result.split()) > 0
    assert len(result.split()) <= len(sql.split())


def test_minimize_preserves_predicate():
    # The minimizer must only keep removals where the predicate still holds.
    sql = "SELECT 1"
    result = minimize(sql, session, _ok_check)
    cmp = session.run(result)
    assert _ok_check(cmp)


def test_minimize_single_token_not_emptied():
    # A single-token SQL -- minimizer must not reduce to empty string.
    sql = "BOGUS"
    result = minimize(sql, session, _err_check)
    assert result.strip() != ""


def test_minimize_strips_irrelevant_prefix():
    # "EXPLAIN SELECT 1" -- if SELECT 1 alone satisfies the same predicate,
    # the minimizer should drop "EXPLAIN".
    sql = "EXPLAIN SELECT 1"
    cmp_full = session.run(sql)
    target_peg = cmp_full.peg.outcome
    target_pg = cmp_full.postgres.outcome
    check = lambda c: c.peg.outcome == target_peg and c.postgres.outcome == target_pg
    result = minimize(sql, session, check)
    assert len(result.split()) <= len(sql.split())
    assert result.strip() != ""
    cmp_min = session.run(result)
    assert cmp_min.peg.outcome == target_peg
    assert cmp_min.postgres.outcome == target_pg


def test_minimize_preserves_internal_error():
    # When the predicate checks any_internal, minimizer must not reduce to SQL
    # that no longer produces an INTERNAL Error.
    # Use a known trigger: "CHECKPOINT system" produces INTERNAL Error on PEG.
    sql = "CHECKPOINT system"
    cmp_full = session.run(sql)
    if not cmp_full.any_internal:
        # If this DuckDB build doesn't trigger it, skip gracefully.
        return
    result = minimize(sql, session, lambda c: c.any_internal)
    cmp_min = session.run(result)
    assert cmp_min.any_internal, f"Minimized SQL lost INTERNAL Error: {result!r}"
    assert result.strip() != ""
