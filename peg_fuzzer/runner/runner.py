"""Execute SQL against DuckDB under both PEG and Postgres parsers."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

from .result import CompareResult, Outcome, Parser, RunResult

# SQL to switch each parser on before running the test statement.
_PARSER_SETUP = {
    Parser.PEG: "CALL enable_peg_parser()",
    Parser.POSTGRES: "CALL disable_peg_parser()",
}


@contextmanager
def _work_dir(directory: Path | None):
    """Temporarily change cwd so DuckDB writes relative paths inside directory."""
    if directory is None:
        yield
        return
    old = os.getcwd()
    directory.mkdir(parents=True, exist_ok=True)
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(old)


def run_one(sql: str, parser: Parser, work_dir: Path | None = None) -> RunResult:
    """Run a single SQL statement with the given parser in a fresh in-memory connection."""
    conn = duckdb.connect()
    start = 0.0
    try:
        conn.execute(_PARSER_SETUP[parser])
        start = time.perf_counter()
        with _work_dir(work_dir):
            conn.execute(sql)
        duration_ms = (time.perf_counter() - start) * 1000
        return RunResult(sql=sql, parser=parser, outcome=Outcome.OK, duration_ms=duration_ms)
    except duckdb.Error as e:
        duration_ms = (time.perf_counter() - start) * 1000 if start else 0.0
        return RunResult(sql=sql, parser=parser, outcome=Outcome.ERROR, error_msg=str(e), duration_ms=duration_ms)
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000 if start else 0.0
        return RunResult(sql=sql, parser=parser, outcome=Outcome.CRASH, error_msg=str(e), duration_ms=duration_ms)
    finally:
        conn.close()


def run_both(sql: str, work_dir: Path | None = None) -> CompareResult:
    """Run a SQL statement under both parsers and return a comparison."""
    peg = run_one(sql, Parser.PEG, work_dir=work_dir)
    postgres = run_one(sql, Parser.POSTGRES, work_dir=work_dir)
    return CompareResult(sql=sql, peg=peg, postgres=postgres)
