"""Tests for deduplication logic."""

import json
import tempfile
from pathlib import Path

from peg_fuzzer.dedup import KnownIssues, _normalize, _signature
from peg_fuzzer.runner.result import CompareResult, Outcome, Parser, RunResult


def _make_cmp(peg_outcome, pg_outcome, peg_err="", pg_err=""):
    sql = "SELECT 1"
    return CompareResult(
        sql=sql,
        peg=RunResult(sql=sql, parser=Parser.PEG, outcome=peg_outcome, error_msg=peg_err),
        postgres=RunResult(sql=sql, parser=Parser.POSTGRES, outcome=pg_outcome, error_msg=pg_err),
    )


def test_normalize_strips_double_quoted_names():
    assert _normalize('Catalog "foo" does not exist') == 'Catalog "<X>" does not exist'


def test_normalize_strips_single_quoted_names():
    assert _normalize("syntax error at or near 'EXISTS'") == "syntax error at or near '<X>'"


def test_normalize_strips_numbers():
    assert _normalize("error at position 42") == "error at position <N>"


def test_normalize_first_line_only():
    msg = "First line\nSecond line\nThird line"
    assert _normalize(msg) == "First line"


def test_same_error_same_signature():
    a = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Parser Error: syntax error at or near "EXISTS"')
    b = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Parser Error: syntax error at or near "EXISTS"')
    assert _signature(a) == _signature(b)


def test_different_identifier_same_signature():
    a = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Catalog "foo" does not exist')
    b = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Catalog "bar" does not exist')
    assert _signature(a) == _signature(b)


def test_different_error_different_signature():
    a = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Parser Error: syntax error")
    b = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Binder Error: unknown column")
    assert _signature(a) != _signature(b)


def test_different_outcome_different_signature():
    a = _make_cmp(Outcome.OK, Outcome.ERROR)
    b = _make_cmp(Outcome.ERROR, Outcome.OK)
    assert _signature(a) != _signature(b)


def test_known_issues_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)
        cmp = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Parser Error: something")

        assert not ki.is_known(cmp)
        ki.mark_seen(cmp)
        assert ki.is_known(cmp)

        # Reload from disk and verify
        ki2 = KnownIssues(path)
        assert ki2.is_known(cmp)


def test_known_issues_deduplicates_by_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)

        a = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Catalog "foo" does not exist')
        b = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Catalog "bar" does not exist')

        ki.mark_seen(a)
        assert ki.is_known(b)  # same pattern, different identifier


def test_known_issues_json_is_verbose():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)
        cmp = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Parser Error: syntax error at or near "EXISTS"')
        cmp.sql = "DETACH IF EXISTS foo"
        ki.mark_seen(cmp)

        data = json.loads(path.read_text())
        assert len(data) == 1
        entry = data[0]
        assert entry["peg_outcome"] == "OK"
        assert entry["postgres_outcome"] == "ERROR"
        assert entry["example_sql"] == "DETACH IF EXISTS foo"
        assert "<X>" in entry["postgres_error"]
        assert "hits" in entry


def test_known_issues_hit_count_increments():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)

        a = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Catalog "foo" does not exist')
        b = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err='Catalog "bar" does not exist')

        ki.mark_seen(a)
        ki.mark_seen(b)  # same signature

        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["hits"] == 2


def test_new_entry_has_resolved_false():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)
        cmp = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Parser Error: something")
        ki.mark_seen(cmp)

        data = json.loads(path.read_text())
        assert data[0]["resolved"] is False


def test_mark_resolved_sets_flag():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)
        cmp = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Parser Error: something")
        ki.mark_seen(cmp)
        ki.mark_resolved(cmp)

        data = json.loads(path.read_text())
        assert data[0]["resolved"] is True


def test_mark_resolved_persists_across_reload():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)
        cmp = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Parser Error: something")
        ki.mark_seen(cmp)
        ki.mark_resolved(cmp)

        ki2 = KnownIssues(path)
        data = json.loads(path.read_text())
        assert data[0]["resolved"] is True
        assert ki2.is_known(cmp)


def test_mark_resolved_unknown_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "known.json"
        ki = KnownIssues(path)
        cmp = _make_cmp(Outcome.OK, Outcome.ERROR, pg_err="Parser Error: something")
        # should not raise even if not seen
        ki.mark_resolved(cmp)
        assert not path.exists()
