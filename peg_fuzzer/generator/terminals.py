"""Concrete value generators for terminal/override rule kinds."""

from __future__ import annotations

import random
import string

from peg_fuzzer.grammar.overrides import OverrideKind

# Plain identifiers that are safe to use as names in any context.
_PLAIN_IDENTS = ["t", "t1", "t2", "col_a", "col_b", "my_table", "s", "x", "y", "v"]

# Safe unreserved-ish keywords usable as identifiers.
_SAFE_NAMES = ["name", "value", "data", "info", "result", "item", "record", "entry"]

_OPERATORS_POOL = ["+", "-", "*", "/", "=", "<", ">", "<=", ">=", "<>", "!=", "||", "%"]


def generate_terminal(kind: OverrideKind, rng: random.Random) -> str:
    if kind == OverrideKind.NUMBER_LITERAL:
        return _number(rng)
    if kind == OverrideKind.STRING_LITERAL:
        return _string_literal(rng)
    if kind == OverrideKind.OPERATOR_LITERAL:
        return rng.choice(_OPERATORS_POOL)
    # All identifier-like kinds use the same pool; the kind distinction matters for
    # autocomplete suggestions but not for generating structurally valid SQL.
    return _identifier(rng)


def _identifier(rng: random.Random) -> str:
    pool = _PLAIN_IDENTS + _SAFE_NAMES
    return rng.choice(pool)


def _number(rng: random.Random) -> str:
    choice = rng.random()
    if choice < 0.5:
        return str(rng.randint(0, 1000))
    # decimal
    integer_part = rng.randint(0, 999)
    frac_part = rng.randint(0, 99)
    return f"{integer_part}.{frac_part:02d}"


def _string_literal(rng: random.Random) -> str:
    length = rng.randint(0, 8)
    chars = string.ascii_lowercase + string.digits + "_ "
    body = "".join(rng.choice(chars) for _ in range(length))
    # escape single quotes
    body = body.replace("'", "''")
    return f"'{body}'"
