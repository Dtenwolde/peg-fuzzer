"""Tests for RuleCoverage -- error class log and outcome count storage."""

import tempfile
from collections import Counter
from pathlib import Path

import pytest

from peg_fuzzer.coverage import RuleCoverage


@pytest.fixture
def db(tmp_path):
    cov = RuleCoverage(tmp_path / "coverage.db")
    yield cov
    cov.close()


def _merge(db, **kwargs):
    defaults = dict(
        run_hits=Counter(),
        queries_run=10,
        seed=0,
        start_rule="Statement",
    )
    defaults.update(kwargs)
    db.merge(**defaults)


# ---------------------------------------------------------------------------
# error_class_report
# ---------------------------------------------------------------------------

def test_error_class_report_empty(db):
    # No data -> empty string (no section shown).
    assert db.error_class_report() == ""


def test_error_class_report_after_merge(db):
    peg_cls = Counter({"Parser Error": 5, "Binder Error": 2})
    pg_cls = Counter({"Parser Error": 8, "Catalog Error": 3})
    _merge(db, peg_error_classes=peg_cls, pg_error_classes=pg_cls)

    report = db.error_class_report()
    assert "Parser Error" in report
    assert "Binder Error" in report
    assert "Catalog Error" in report
    # PEG section comes before Postgres section
    assert report.index("PEG") < report.index("Postgres")


def test_error_class_report_counts_accumulate(db):
    _merge(db, peg_error_classes=Counter({"Parser Error": 3}))
    _merge(db, peg_error_classes=Counter({"Parser Error": 7}))

    report = db.error_class_report()
    # Total should be 10
    assert "10" in report


def test_error_class_report_top_n(db):
    many = Counter({f"ErrClass{i}": i + 1 for i in range(20)})
    _merge(db, pg_error_classes=many)
    report = db.error_class_report(top_n=5)
    # Count lines that contain "ErrClass" -- these are the data rows.
    entry_lines = [l for l in report.splitlines() if "ErrClass" in l]
    assert len(entry_lines) <= 5


# ---------------------------------------------------------------------------
# Outcome counts stored in run_log
# ---------------------------------------------------------------------------

def test_merge_stores_outcome_counts(db):
    _merge(db, ok_count=50, err_count=30, crash_count=2, diverge_count=3)
    row = db._con.execute(
        "SELECT ok_count, err_count, crash_count, diverge_count FROM run_log"
    ).fetchone()
    assert row == (50, 30, 2, 3)


def test_merge_outcome_counts_default_zero(db):
    _merge(db)
    row = db._con.execute(
        "SELECT ok_count, err_count, crash_count, diverge_count FROM run_log"
    ).fetchone()
    assert row == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# Migration: adding new columns to an existing DB
# ---------------------------------------------------------------------------

def test_migration_adds_columns(tmp_path):
    """A DB created before the new columns were added gets them via migration."""
    db_path = tmp_path / "old.db"
    import duckdb
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE run_log (
            id INTEGER PRIMARY KEY,
            seed BIGINT,
            queries INTEGER,
            new_issues INTEGER,
            start_rule VARCHAR,
            ts TIMESTAMP DEFAULT now()
        )
    """)
    con.execute("INSERT INTO run_log (id, seed, queries, new_issues, start_rule) VALUES (1, 0, 5, 0, 'Statement')")
    con.close()

    # Opening via RuleCoverage should migrate the table.
    cov = RuleCoverage(db_path)
    cols = {row[1] for row in cov._con.execute("PRAGMA table_info('run_log')").fetchall()}
    assert "ok_count" in cols
    assert "err_count" in cols
    assert "crash_count" in cols
    assert "diverge_count" in cols
    cov.close()
