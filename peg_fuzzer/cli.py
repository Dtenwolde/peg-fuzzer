"""CLI entry point."""

from __future__ import annotations

import argparse
import os

DEFAULT_GRAMMAR_DIR = os.path.join(
    os.path.dirname(__file__), "..", "duckdb", "extension", "autocomplete", "grammar"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="PEG grammar fuzzer for DuckDB SQL")
    parser.add_argument(
        "--grammar-dir",
        default=DEFAULT_GRAMMAR_DIR,
        help="Path to the grammar directory (default: duckdb submodule grammar)",
    )
    parser.add_argument(
        "--start-rule",
        default="Statement",
        help="Root grammar rule to generate from (default: Statement)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of SQL statements to generate and test (default: 100)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducibility (default: random)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print OK and ERROR results too",
    )

    args = parser.parse_args()

    from peg_fuzzer.fuzzer import run_fuzzer

    run_fuzzer(
        grammar_dir=args.grammar_dir,
        start_rule=args.start_rule,
        count=args.count,
        seed=args.seed,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
