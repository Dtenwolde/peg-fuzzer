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
_NUMERIC_EDGES = ["0", "-1", "1", "2147483647", "-2147483648", "0.0", "-0.5"]


def generate_terminal(kind: OverrideKind, rng: random.Random) -> str:
    if kind == OverrideKind.NUMBER_LITERAL:
        return _number(rng)
    if kind == OverrideKind.STRING_LITERAL:
        return _string_literal(rng)
    if kind == OverrideKind.OPERATOR_LITERAL:
        return rng.choice(_OPERATORS_POOL)
    if kind == OverrideKind.TYPE_NAME:
        return rng.choice(_TYPE_NAMES)
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
    if roll < 0.05:
        return "''"  # empty string
    if roll < 0.10:
        return "' '"  # single space
    length = rng.randint(1, 12)
    chars = string.ascii_letters + string.digits + "_ -."
    body = "".join(rng.choice(chars) for _ in range(length))
    body = body.replace("'", "''")
    return f"'{body}'"
