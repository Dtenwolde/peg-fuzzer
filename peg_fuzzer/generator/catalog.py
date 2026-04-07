"""Query a live DuckDB connection to build dynamic terminal value pools."""

from __future__ import annotations

import duckdb

from peg_fuzzer.grammar.overrides import OverrideKind


def load_catalog_pools() -> dict[OverrideKind, list[str]]:
    """Return pools of real DuckDB names for function/setting terminal kinds."""
    con = duckdb.connect()
    try:
        scalar = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT function_name FROM duckdb_functions()"
                " WHERE function_type = 'scalar' ORDER BY 1"
            ).fetchall()
        ]
        table = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT function_name FROM duckdb_functions()"
                " WHERE function_type IN ('table', 'table_macro') ORDER BY 1"
            ).fetchall()
        ]
        settings = [
            row[0]
            for row in con.execute("SELECT name FROM duckdb_settings() ORDER BY 1").fetchall()
        ]
    finally:
        con.close()

    return {
        OverrideKind.SCALAR_FUNCTION_NAME: scalar,
        OverrideKind.RESERVED_SCALAR_FUNCTION_NAME: scalar,
        OverrideKind.TABLE_FUNCTION_NAME: table,
        OverrideKind.SETTING_NAME: settings,
    }
