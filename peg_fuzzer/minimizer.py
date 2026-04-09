"""Greedy token-level SQL minimizer (delta debugging).

Tries to remove one token at a time, keeping the smallest SQL that still
satisfies a caller-supplied predicate (e.g. still diverges, still crashes,
still produces an INTERNAL Error).  Repeats until no single-token removal
preserves the property or max_passes is reached.

Token splitting is whitespace-based for simplicity.  Quoted strings with
internal spaces may not minimize perfectly, but the check always re-runs the
candidate so the saved SQL is always valid.
"""

from __future__ import annotations

from typing import Callable

from peg_fuzzer.runner.result import CompareResult


def minimize(
    sql: str,
    session,
    check: Callable[[CompareResult], bool],
    max_passes: int = 5,
) -> str:
    """Return the shortest whitespace-token subset of sql that satisfies check().

    check(cmp) must return True for the original sql (the caller is responsible
    for verifying this).  session must expose .run(sql) -> CompareResult.
    """
    tokens = sql.split()
    if not tokens:
        return sql
    for _ in range(max_passes):
        improved = False
        i = 0
        while i < len(tokens):
            candidate_tokens = tokens[:i] + tokens[i + 1:]
            if not candidate_tokens:
                # Never reduce to empty -- skip removing the last token.
                i += 1
                continue
            candidate = " ".join(candidate_tokens)
            cmp = session.run(candidate)
            if check(cmp):
                tokens = candidate_tokens
                improved = True
                # Don't increment i -- the token at i+1 shifted into position i.
            else:
                i += 1
        if not improved:
            break
    return " ".join(tokens)
