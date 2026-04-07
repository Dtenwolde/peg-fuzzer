#!/usr/bin/env python3
"""
Build DuckDB from the submodule and re-run every SQL file in interesting/
against both the PEG parser and the Postgres parser.

The build uses extension_config_local.cmake (at the repo root) so the
autocomplete extension is compiled in and enable_peg_parser() works.

Usage:
    python scripts/verify_interesting.py
    python scripts/verify_interesting.py --branch my-fix-branch
    python scripts/verify_interesting.py --rebuild
    python scripts/verify_interesting.py --no-build  # skip build, use existing binary
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DUCKDB_DIR = REPO_ROOT / "duckdb"
BUILD_DIR = DUCKDB_DIR / "build" / "release"
BINARY = BUILD_DIR / "duckdb"
INTERESTING_DIR = REPO_ROOT / "interesting"
# Path to the local extension config, relative to DUCKDB_DIR
EXTENSION_CONFIG = "../extension_config_local.cmake"


# ------------------------------------------------------------------
# Build
# ------------------------------------------------------------------

def build(branch: str | None, jobs: int) -> None:
    if branch:
        print(f"==> Checking out branch: {branch}")
        subprocess.run(["git", "checkout", branch], cwd=DUCKDB_DIR, check=True)

    print(f"==> Building DuckDB (jobs={jobs}) with autocomplete extension...")
    env = os.environ.copy()
    env["EXTENSION_CONFIGS"] = EXTENSION_CONFIG

    result = subprocess.run(
        ["make", "release", f"-j{jobs}"],
        cwd=DUCKDB_DIR,
        env=env,
    )
    if result.returncode != 0:
        print("ERROR: Build failed.", file=sys.stderr)
        sys.exit(1)

    print(f"==> Build complete: {BINARY}\n")


# ------------------------------------------------------------------
# SQL extraction
# ------------------------------------------------------------------

def extract_sql(path: Path) -> str:
    """Return just the SQL from an interesting/ file, stripping the comment header."""
    text = path.read_text(encoding="utf-8")
    # Header comment block ends at the first blank line
    _, _, sql = text.partition("\n\n")
    return sql.strip()


# ------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------

def run_sql(sql: str, peg: bool, timeout: int) -> tuple[str, str]:
    """
    Run a SQL statement via the built DuckDB CLI binary.

    Returns (outcome, first_line_of_error) where outcome is OK / ERR / CRASH.
    """
    setup = "CALL enable_peg_parser();" if peg else "CALL disable_peg_parser();"
    stdin_data = f"{setup}\n{sql};\n"

    try:
        result = subprocess.run(
            [str(BINARY)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return "OK", ""
        # DuckDB CLI writes errors to stderr
        err_text = (result.stderr or result.stdout).strip()
        first_line = err_text.splitlines()[0] if err_text else "(no error message)"
        return "ERR", first_line
    except subprocess.TimeoutExpired:
        return "CRASH", f"timed out after {timeout}s"
    except Exception as exc:
        return "CRASH", str(exc)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run interesting/ SQL files against a freshly built DuckDB"
    )
    parser.add_argument(
        "--branch",
        help="Git branch or commit to check out before building (default: current HEAD)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force a rebuild even if the binary already exists",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip the build step and use an existing binary",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 4,
        help="Parallel build jobs (default: number of CPUs)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Per-query timeout in seconds (default: 15)",
    )
    args = parser.parse_args()

    # Build
    needs_build = args.rebuild or args.branch or not BINARY.exists()
    if not args.no_build and needs_build:
        build(args.branch, args.jobs)

    if not BINARY.exists():
        print(f"ERROR: binary not found at {BINARY}", file=sys.stderr)
        print("Run without --no-build, or build manually first.", file=sys.stderr)
        sys.exit(1)

    # Print DuckDB version from built binary
    ver = subprocess.run(
        [str(BINARY), "-c", "SELECT version()"],
        capture_output=True, text=True,
    )
    print(f"DuckDB (built): {ver.stdout.strip()}")

    # Collect SQL files
    sql_files = sorted(
        p for p in INTERESTING_DIR.glob("*.sql")
        if p.name != "known_issues.json"
    )
    if not sql_files:
        print(f"\nNo SQL files found in {INTERESTING_DIR}.")
        sys.exit(0)

    print(f"Running {len(sql_files)} queries from {INTERESTING_DIR}/\n")

    # Table header
    col_file = 35
    col_out = 8
    header = f"{'File':<{col_file}} {'PEG':<{col_out}} {'Postgres':<{col_out}} Status"
    print(header)
    print("-" * (len(header) + 20))

    same = diverged = 0

    for path in sql_files:
        sql = extract_sql(path)
        if not sql:
            continue

        peg_out, peg_err = run_sql(sql, peg=True, timeout=args.timeout)
        pg_out, pg_err = run_sql(sql, peg=False, timeout=args.timeout)

        if peg_out == pg_out:
            same += 1
            status = f"same ({peg_out})"
        else:
            diverged += 1
            status = "DIVERGE"

        print(f"{path.name:<{col_file}} {peg_out:<{col_out}} {pg_out:<{col_out}} {status}")
        if peg_err:
            print(f"  {'':>{col_file}} PEG: {peg_err}")
        if pg_err:
            print(f"  {'':>{col_file}} PG:  {pg_err}")

    print("-" * (len(header) + 20))
    print(f"Total: {len(sql_files)}  Diverged: {diverged}  Same: {same}")


if __name__ == "__main__":
    main()
