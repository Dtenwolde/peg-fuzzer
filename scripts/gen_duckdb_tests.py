#!/usr/bin/env python3
"""
Convert interesting/diverge_*.sql and interesting/internal_*.sql files into
DuckDB sqllogictest .test files.

Postgres is the reference: both parsers should produce the same outcome as
Postgres does.  If Postgres accepts the SQL, both statement blocks are
'statement ok'.  If Postgres rejects it, both are 'statement error' with the
Postgres error message.

diverge files: PEG and Postgres disagree on OK vs ERROR -- documents bugs to fix.
internal files: one or both parsers hit an INTERNAL Error -- documents crashes
                and assertion failures that should become clean error messages.

Usage:
    python scripts/gen_duckdb_tests.py
    python scripts/gen_duckdb_tests.py --output-dir path/to/out
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
INTERESTING_DIR = REPO_ROOT / "interesting"
DEFAULT_OUT_DIR = REPO_ROOT / "fuzzer_issues"


def parse_header(text: str) -> tuple[str, str, str, str, str]:
    """Parse the comment block at the top of an interesting/ SQL file.

    Returns (kind, peg_outcome, peg_error, pg_outcome, pg_error).
    kind is 'DIVERGE', 'INTERNAL', or 'CRASH'.
    outcome is 'OK' or 'ERR'; error is the raw error string (may be empty).
    """
    kind = peg_outcome = peg_error = pg_outcome = pg_error = ""
    for line in text.splitlines():
        if not line.startswith("--"):
            break
        m = re.match(r"--\s+(DIVERGE|INTERNAL|CRASH)\s*$", line)
        if m:
            kind = m.group(1)
            continue
        m = re.match(r"--\s+PEG:\s+(OK|ERR)\s*(.*)", line)
        if m:
            peg_outcome, peg_error = m.group(1), m.group(2).strip()
            continue
        m = re.match(r"--\s+Postgres:\s+(OK|ERR)\s*(.*)", line)
        if m:
            pg_outcome, pg_error = m.group(1), m.group(2).strip()
    return kind, peg_outcome, peg_error, pg_outcome, pg_error


def extract_sql(text: str) -> str:
    """Return SQL body by skipping the blank-line-terminated comment header."""
    _, _, sql = text.partition("\n\n")
    return sql.strip()


def _block(outcome: str, sql: str, error: str) -> list[str]:
    """Return lines for a single statement block."""
    if outcome == "OK":
        return ["statement ok", sql]
    lines = ["statement error", sql, "----"]
    if error:
        lines.append(error)
    return lines


def gen_test(
    stem: str,
    kind: str,
    peg_outcome: str,
    peg_error: str,
    pg_outcome: str,
    pg_error: str,
    sql: str,
    out_dir: Path,
) -> str:
    rel = f"{out_dir.relative_to(REPO_ROOT)}/{stem}.test"

    # Describe the current issue so it's clear what bug this test covers.
    if kind == "INTERNAL":
        internal_side = "PEG" if "INTERNAL" in peg_error else "Postgres"
        internal_msg = peg_error if "INTERNAL" in peg_error else pg_error
        desc = f"{internal_side} hits INTERNAL Error (should be clean error): {internal_msg or '(error)'}"
    elif peg_outcome == "OK" and pg_outcome == "ERR":
        desc = f"PEG should reject (currently accepts): {pg_error or '(error)'}"
    elif peg_outcome == "ERR" and pg_outcome == "OK":
        desc = f"PEG should accept (currently rejects): {peg_error or '(error)'}"
    else:
        desc = f"PEG={peg_outcome}, Postgres={pg_outcome}"

    # Both parsers should behave like Postgres.
    sections: list[list[str]] = [
        [f"# name: {rel}", f"# description: {desc}", "# group: [peg_parser]"],
        ["require autocomplete"],
        ["statement ok", "CALL enable_peg_parser();"],
        _block(pg_outcome, sql, pg_error),
        ["statement ok", "CALL disable_peg_parser();"],
        _block(pg_outcome, sql, pg_error),
    ]

    return "\n\n".join("\n".join(s) for s in sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert interesting/diverge_*.sql into DuckDB sqllogictest .test files"
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"Where to write .test files (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--interesting-dir",
        default=str(INTERESTING_DIR),
        help=f"Source directory of diverge_*.sql files (default: {INTERESTING_DIR})",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    interesting_dir = Path(args.interesting_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sql_files = sorted(
        list(interesting_dir.glob("diverge_*.sql"))
        + list(interesting_dir.glob("internal_*.sql"))
    )
    if not sql_files:
        print(f"No diverge_*.sql or internal_*.sql files found in {interesting_dir}")
        return

    written = skipped = 0
    for path in sql_files:
        text = path.read_text(encoding="utf-8")
        kind, peg_outcome, peg_error, pg_outcome, pg_error = parse_header(text)
        sql = extract_sql(text)

        if not peg_outcome or not pg_outcome or not sql:
            print(f"SKIP {path.name}: could not parse header")
            skipped += 1
            continue

        stem = path.stem
        content = gen_test(stem, kind, peg_outcome, peg_error, pg_outcome, pg_error, sql, out_dir)
        out_path = out_dir / f"{stem}.test"
        out_path.write_text(content, encoding="utf-8")
        written += 1

    print(f"Wrote {written} test files to {out_dir}" + (f" ({skipped} skipped)" if skipped else ""))


if __name__ == "__main__":
    main()
