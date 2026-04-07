"""Persistent, cumulative rule-coverage tracking across fuzzing runs.

Stores data in interesting/coverage.db (DuckDB).

Schema:
  rule_hits(rule_name VARCHAR PK, hits BIGINT)
  run_log(id INTEGER, seed BIGINT, queries INTEGER, start_rule VARCHAR, ts TIMESTAMP)

Each run merges its per-rule hit counts into rule_hits and appends a run_log row,
so the picture improves the longer the fuzzer campaigns run.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import duckdb


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_hits (
    rule_name VARCHAR PRIMARY KEY,
    hits      BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    id         INTEGER PRIMARY KEY,
    seed       BIGINT,
    queries    INTEGER,
    new_issues INTEGER,
    start_rule VARCHAR,
    ts         TIMESTAMP DEFAULT now()
);
"""


class RuleCoverage:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(exist_ok=True)
        self._con = duckdb.connect(str(db_path))
        self._con.execute(_SCHEMA)

    def merge(
        self,
        run_hits: Counter[str],
        queries_run: int,
        seed: int,
        start_rule: str,
        new_issues: int = 0,
    ) -> None:
        """Merge one run's hit counts into the cumulative totals and log the run."""
        if run_hits:
            rows = list(run_hits.items())
            self._con.executemany(
                """
                INSERT INTO rule_hits (rule_name, hits) VALUES (?, ?)
                ON CONFLICT (rule_name) DO UPDATE SET hits = rule_hits.hits + excluded.hits
                """,
                rows,
            )
        next_id = self._con.execute("SELECT coalesce(max(id), 0) + 1 FROM run_log").fetchone()[0]
        self._con.execute(
            "INSERT INTO run_log (id, seed, queries, new_issues, start_rule) VALUES (?, ?, ?, ?, ?)",
            [next_id, seed, queries_run, new_issues, start_rule],
        )

    def total_queries(self) -> int:
        row = self._con.execute("SELECT coalesce(sum(queries), 0) FROM run_log").fetchone()
        return int(row[0])

    def report(self, all_rules: set[str], top_n: int = 15) -> str:
        """Return a human-readable coverage summary."""
        total = self.total_queries()
        if total == 0:
            return "  No coverage data."

        hit_map: dict[str, int] = {
            row[0]: row[1]
            for row in self._con.execute("SELECT rule_name, hits FROM rule_hits").fetchall()
        }

        covered = {r for r in all_rules if hit_map.get(r, 0) > 0}
        never = sorted(all_rules - covered)
        rare = sorted(
            (r for r in covered if hit_map[r] / total < 0.01),
            key=lambda r: hit_map[r],
        )

        lines = [
            f"  Rules covered: {len(covered)}/{len(all_rules)} "
            f"({100 * len(covered) // len(all_rules)}%)  "
            f"over {total:,} total queries",
        ]

        if rare:
            lines.append(f"\n  Rarely hit (<1% of queries) -- {top_n} least visited:")
            for rule in rare[:top_n]:
                pct = 100 * hit_map[rule] / total
                lines.append(f"    {rule:<50s} {hit_map[rule]:>6,} hits  ({pct:.3f}%)")

        if never:
            lines.append(f"\n  Never reached ({len(never)} rules):")
            for rule in never[:top_n]:
                lines.append(f"    {rule}")
            if len(never) > top_n:
                lines.append(f"    ... and {len(never) - top_n} more (use --verbose to list all)")

        return "\n".join(lines)

    def close(self) -> None:
        self._con.close()
