"""Main fuzzing loop: grammar -> generate -> run both parsers -> compare."""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

from peg_fuzzer.coverage import RuleCoverage
from peg_fuzzer.dedup import KnownIssues
from peg_fuzzer.grammar.parser import load_grammar_dir
from peg_fuzzer.generator.generator import Generator
from peg_fuzzer.runner.result import Outcome
from peg_fuzzer.runner.runner import run_both

_REPO_ROOT = Path(__file__).parent.parent
_INTERESTING_DIR = Path("interesting")
_KNOWN_ISSUES_FILE = _INTERESTING_DIR / "known_issues.json"
_COVERAGE_DB = _INTERESTING_DIR / "coverage.db"
_WORK_DIR = _REPO_ROOT / "test"


def _tag(outcome: Outcome) -> str:
    return {Outcome.OK: "OK", Outcome.ERROR: "ERR", Outcome.CRASH: "CRASH"}[outcome]


def run_fuzzer(
    grammar_dir: str,
    start_rule: str = "Statement",
    count: int = 100,
    seed: int | None = None,
    verbose: bool = False,
) -> None:
    if seed is None:
        seed = random.randrange(2**32)
    print(f"Seed: {seed}  (rerun with --seed {seed} to reproduce)")
    rng = random.Random(seed)
    grammar = load_grammar_dir(grammar_dir)
    gen = Generator(grammar, rng)
    known = KnownIssues(_KNOWN_ISSUES_FILE)

    interesting_index = _next_index(_INTERESTING_DIR)

    peg_counts: dict[Outcome, int] = defaultdict(int)
    pg_counts: dict[Outcome, int] = defaultdict(int)
    new_issues = 0
    known_skipped = 0

    bar = tqdm(
        total=count,
        unit="q",
        dynamic_ncols=True,
    )
    bar.set_postfix(new=0, skip=0)

    for _ in range(count):
        try:
            sql = gen.generate(start_rule)
        except Exception as e:
            tqdm.write(f"[GENFAIL] {e}")
            bar.update(1)
            continue

        cmp = run_both(sql, work_dir=_WORK_DIR)

        peg_counts[cmp.peg.outcome] += 1
        pg_counts[cmp.postgres.outcome] += 1

        if not (cmp.any_crash or cmp.diverged):
            if verbose:
                tqdm.write(f"[{_tag(cmp.peg.outcome):<5}] {sql!r}")
                if cmp.peg.error_msg:
                    tqdm.write(f"        PEG: {cmp.peg.error_msg.splitlines()[0]}")
            bar.update(1)
            continue

        if known.is_known(cmp):
            known_skipped += 1
            bar.set_postfix(new=new_issues, skip=known_skipped)
            bar.update(1)
            continue

        # New issue -- save and report
        known.mark_seen(cmp)
        new_issues += 1
        _INTERESTING_DIR.mkdir(exist_ok=True)

        if cmp.any_crash:
            save_file = _INTERESTING_DIR / f"crash_{interesting_index:04d}.sql"
            label = "[CRASH  ]"
        else:
            save_file = _INTERESTING_DIR / f"diverge_{interesting_index:04d}.sql"
            label = "[DIVERGE]"

        interesting_index += 1
        _write_interesting(save_file, cmp)

        peg_t = _tag(cmp.peg.outcome)
        pg_t = _tag(cmp.postgres.outcome)
        tqdm.write(f"{label} PEG={peg_t} PG={pg_t}  {sql!r}")
        if cmp.peg.error_msg:
            tqdm.write(f"           PEG: {cmp.peg.error_msg.splitlines()[0]}")
        if cmp.postgres.error_msg:
            tqdm.write(f"           PG:  {cmp.postgres.error_msg.splitlines()[0]}")
        tqdm.write(f"           => saved {save_file}")

        bar.set_postfix(new=new_issues, skip=known_skipped)
        bar.update(1)

    bar.close()

    # Persist coverage stats to DuckDB.
    cov_db = RuleCoverage(_COVERAGE_DB)
    cov_db.merge(gen.rule_hits, queries_run=count, seed=seed, start_rule=start_rule)

    cov = gen.coverage_stats()
    print(
        f"\nDone: {count} queries"
        f"\n  PEG      -- OK={peg_counts[Outcome.OK]}  ERR={peg_counts[Outcome.ERROR]}  CRASH={peg_counts[Outcome.CRASH]}"
        f"\n  Postgres -- OK={pg_counts[Outcome.OK]}  ERR={pg_counts[Outcome.ERROR]}  CRASH={pg_counts[Outcome.CRASH]}"
        f"\n  New issues={new_issues}  Known (skipped)={known_skipped}"
    )
    print(cov_db.report(set(gen.grammar.rules), top_n=15 if verbose else 10))
    if verbose and cov["uncovered"]:
        print(f"\n  All uncovered rules ({len(cov['uncovered'])}):")
        for name in cov["uncovered"]:
            print(f"    {name}")
    cov_db.close()


def _next_index(directory: Path) -> int:
    """Find the next available file index in interesting/ to avoid overwriting."""
    if not directory.exists():
        return 0
    existing = list(directory.glob("diverge_*.sql")) + list(directory.glob("crash_*.sql"))
    if not existing:
        return 0
    indices = []
    for p in existing:
        try:
            indices.append(int(p.stem.split("_")[-1]))
        except ValueError:
            pass
    return max(indices) + 1 if indices else 0


def _write_interesting(path: Path, cmp) -> None:
    reason = "CRASH" if cmp.any_crash else "DIVERGE"
    lines = [
        f"-- {reason}",
        f"-- PEG:      {_tag(cmp.peg.outcome)}  {cmp.peg.error_msg.splitlines()[0] if cmp.peg.error_msg else ''}",
        f"-- Postgres: {_tag(cmp.postgres.outcome)}  {cmp.postgres.error_msg.splitlines()[0] if cmp.postgres.error_msg else ''}",
        "",
        cmp.sql,
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
