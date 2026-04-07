"""Query a live DuckDB connection to build dynamic terminal value pools."""

from __future__ import annotations

import duckdb

from peg_fuzzer.grammar.overrides import OverrideKind


# Fixed schema used for fuzzing. Tables and columns are created in every fresh
# connection before the fuzzing query runs, so generated SQL can actually hit data.
FUZZ_SCHEMA: dict[str, list[tuple[str, str]]] = {
    "t":  [("id", "INTEGER"), ("col_a", "INTEGER"), ("col_b", "VARCHAR"), ("col_c", "DOUBLE")],
    "t1": [("id", "INTEGER"), ("x", "INTEGER"),     ("y", "INTEGER"),     ("data", "VARCHAR")],
    "t2": [("id", "INTEGER"), ("key", "VARCHAR"),   ("val", "INTEGER"),   ("src", "VARCHAR"), ("dst", "VARCHAR")],
}

# Two rows per table; literal values chosen to match column types above.
_FUZZ_ROWS: dict[str, list[tuple]] = {
    "t":  [(1, 10, "hello", 1.5), (2, 20, "world", 2.5)],
    "t1": [(1, 5, 10, "foo"),     (2, 7, 14, "bar")],
    "t2": [(1, "k1", 42, "a", "b"), (2, "k2", 99, "c", "d")],
}


def build_schema_setup() -> tuple[dict[OverrideKind, list[str]], str]:
    """Return (pools_extension, setup_sql) for the fixed fuzz schema.

    pools_extension maps TABLE_NAME / COLUMN_NAME kinds to lists derived from
    FUZZ_SCHEMA.  setup_sql is a semicolon-separated string of CREATE TABLE +
    INSERT statements to execute in each fresh runner connection.
    """
    stmts: list[str] = []
    for table, cols in FUZZ_SCHEMA.items():
        col_defs = ", ".join(f"{c} {t}" for c, t in cols)
        stmts.append(f"CREATE TABLE {table} ({col_defs})")
        col_names = ", ".join(c for c, _ in cols)
        for row in _FUZZ_ROWS[table]:
            vals = ", ".join(
                f"'{v}'" if isinstance(v, str) else str(v) for v in row
            )
            stmts.append(f"INSERT INTO {table} ({col_names}) VALUES ({vals})")

    setup_sql = "; ".join(stmts)

    table_names = list(FUZZ_SCHEMA)
    col_names = list(dict.fromkeys(c for cols in FUZZ_SCHEMA.values() for c, _ in cols))

    pools_ext: dict[OverrideKind, list[str]] = {
        OverrideKind.TABLE_NAME: table_names,
        OverrideKind.RESERVED_TABLE_NAME: table_names,
        OverrideKind.COLUMN_NAME: col_names,
        OverrideKind.RESERVED_COLUMN_NAME: col_names,
    }
    return pools_ext, setup_sql


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
