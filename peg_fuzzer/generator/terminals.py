"""Concrete value generators for terminal/override rule kinds."""

from __future__ import annotations

import random
import string

from peg_fuzzer.grammar.overrides import OverrideKind

# Plain identifiers safe as object names in any context.
_PLAIN_IDENTS = [
    "t", "t1", "t2", "t3", "col_a", "col_b", "col_c",
    "my_table", "s", "x", "y", "v", "a", "b", "c",
]

# Safe unreserved-ish keywords usable as identifiers.
_SAFE_NAMES = [
    "name", "value", "data", "info", "result", "item", "record", "entry",
    "count", "total", "amount", "key", "val", "idx", "src", "dst",
]

_OPERATORS_POOL = ["+", "-", "*", "/", "=", "<", ">", "<=", ">=", "<>", "!=", "||", "%"]

# Real DuckDB type names -- used when TypeName is needed.
_TYPE_NAMES = [
    "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT",
    "FLOAT", "DOUBLE", "DECIMAL", "BOOLEAN",
    "VARCHAR", "TEXT", "BLOB",
    "DATE", "TIME", "TIMESTAMP", "INTERVAL",
]

# Edge-case numeric values worth hitting explicitly.
_NUMERIC_EDGES = [
    "0", "-1", "1", "2147483647", "-2147483648", "0.0", "-0.5",
    "0xFF", "0x00", "0xDEADBEEF",  # hex literals
]

# DuckDB's built-in catalog and schema names.
_CATALOG_NAMES = ["memory", "system", "temp"]
_SCHEMA_NAMES = ["main", "pg_catalog", "information_schema"]


def generate_terminal(
    kind: OverrideKind,
    rng: random.Random,
    pools: dict[OverrideKind, list[str]] | None = None,
) -> str:
    # Dynamic pools (from live DuckDB catalog) take precedence over hardcoded defaults.
    if pools and kind in pools and pools[kind]:
        return rng.choice(pools[kind])
    if kind == OverrideKind.NUMBER_LITERAL:
        return _number(rng)
    if kind == OverrideKind.STRING_LITERAL:
        return _string_literal(rng)
    if kind == OverrideKind.OPERATOR_LITERAL:
        return rng.choice(_OPERATORS_POOL)
    if kind == OverrideKind.TYPE_NAME:
        return rng.choice(_TYPE_NAMES)
    if kind == OverrideKind.PARAMETER:
        return f"${rng.randint(1, 9)}"
    if kind in (OverrideKind.CATALOG_NAME,):
        return rng.choice(_CATALOG_NAMES)
    if kind in (OverrideKind.SCHEMA_NAME, OverrideKind.RESERVED_SCHEMA_NAME):
        return rng.choice(_SCHEMA_NAMES)
    # All other identifier-like kinds use the same pool; the kind distinction
    # matters for autocomplete suggestions but not for structural generation.
    return _identifier(rng)


def _identifier(rng: random.Random) -> str:
    pool = _PLAIN_IDENTS + _SAFE_NAMES
    return rng.choice(pool)


def _number(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.10:
        return rng.choice(_NUMERIC_EDGES)
    if roll < 0.45:
        # integer (positive or negative)
        sign = "-" if rng.random() < 0.2 else ""
        return f"{sign}{rng.randint(0, 1_000_000)}"
    if roll < 0.75:
        # decimal
        sign = "-" if rng.random() < 0.2 else ""
        integer_part = rng.randint(0, 999)
        frac_part = rng.randint(0, 9999)
        return f"{sign}{integer_part}.{frac_part:04d}"
    # scientific notation
    sign = "-" if rng.random() < 0.2 else ""
    mantissa = rng.randint(1, 99)
    exp = rng.randint(-10, 10)
    return f"{sign}{mantissa}e{exp}"


def _string_literal(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.04:
        return "''"  # empty string
    if roll < 0.08:
        return "' '"  # single space
    if roll < 0.12:
        return "'''''"  # just escaped quotes: '''
    if roll < 0.16:
        # embedded newline / tab
        esc = rng.choice(["\\n", "\\t", "\\r"])
        return f"'{esc}'"
    if roll < 0.20:
        # long string (stress buffer handling)
        body = "x" * rng.randint(64, 256)
        return f"'{body}'"
    if roll < 0.24:
        # SQL-like content that might confuse naive parsers
        snippet = rng.choice(["'; DROP TABLE t; --", "1 OR 1=1", "NULL", "0x41"])
        return f"'{snippet}'"
    length = rng.randint(1, 12)
    chars = string.ascii_letters + string.digits + "_ -."
    body = "".join(rng.choice(chars) for _ in range(length))
    body = body.replace("'", "''")
    return f"'{body}'"
