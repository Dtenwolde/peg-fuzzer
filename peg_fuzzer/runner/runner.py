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


def run_one(sql: str, parser: Parser, work_dir: Path | None = None, setup_sql: str = "") -> RunResult:
    """Run a single SQL statement with the given parser in a fresh in-memory connection."""
    conn = duckdb.connect()
    start = 0.0
    try:
        if setup_sql:
            conn.execute(setup_sql)
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


def run_both(sql: str, work_dir: Path | None = None, setup_sql: str = "") -> CompareResult:
    """Run a SQL statement under both parsers and return a comparison."""
    peg = run_one(sql, Parser.PEG, work_dir=work_dir, setup_sql=setup_sql)
    postgres = run_one(sql, Parser.POSTGRES, work_dir=work_dir, setup_sql=setup_sql)
    return CompareResult(sql=sql, peg=peg, postgres=postgres)


class FuzzSession:
    """Persistent parser connections for high-throughput fuzzing.

    Creates one DuckDB connection per parser at startup, runs setup_sql once,
    then uses BEGIN/ROLLBACK around each fuzzing query to restore state.
    DuckDB's DDL is transactional so ROLLBACK undoes CREATE TABLE, INSERT, etc.
    from the fuzzing query while leaving the setup schema intact.

    If a connection enters a bad state (e.g. the query contained an explicit
    COMMIT/ROLLBACK), the connection is transparently recreated.
    """

    def __init__(self, setup_sql: str = "", work_dir: Path | None = None) -> None:
        self._setup_sql = setup_sql
        self._work_dir = work_dir
        self._conns: dict[Parser, duckdb.DuckDBPyConnection] = {
            parser: self._open(parser) for parser in (Parser.PEG, Parser.POSTGRES)
        }

    def _open(self, parser: Parser) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect()
        if self._setup_sql:
            conn.execute(self._setup_sql)
        conn.execute(_PARSER_SETUP[parser])
        return conn

    def run(self, sql: str) -> CompareResult:
        peg = self._run_one(sql, Parser.PEG)
        postgres = self._run_one(sql, Parser.POSTGRES)
        return CompareResult(sql=sql, peg=peg, postgres=postgres)

    def _run_one(self, sql: str, parser: Parser) -> RunResult:
        conn = self._conns[parser]
        start = 0.0
        try:
            conn.execute("BEGIN")
            start = time.perf_counter()
            with _work_dir(self._work_dir):
                conn.execute(sql)
            duration_ms = (time.perf_counter() - start) * 1000
            result = RunResult(sql=sql, parser=parser, outcome=Outcome.OK, duration_ms=duration_ms)
        except duckdb.Error as e:
            duration_ms = (time.perf_counter() - start) * 1000 if start else 0.0
            result = RunResult(sql=sql, parser=parser, outcome=Outcome.ERROR, error_msg=str(e), duration_ms=duration_ms)
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000 if start else 0.0
            result = RunResult(sql=sql, parser=parser, outcome=Outcome.CRASH, error_msg=str(e), duration_ms=duration_ms)

        try:
            conn.execute("ROLLBACK")
        except Exception:
            # Connection in bad state (e.g. query issued explicit COMMIT/ROLLBACK);
            # recreate it so the next query gets a clean connection.
            try:
                conn.close()
            except Exception:
                pass
            self._conns[parser] = self._open(parser)
            return result

        # ROLLBACK only undoes transactional state. Session-level commands like
        # USE and SET survive it. Reset catalog+schema explicitly so accumulated
        # session drift doesn't cause spurious divergences between parsers.
        try:
            conn.execute("USE memory.main")
        except Exception:
            self._conns[parser] = self._open(parser)

        return result

    def close(self) -> None:
        for conn in self._conns.values():
            try:
                conn.close()
            except Exception:
                pass
