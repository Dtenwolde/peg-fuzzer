"""Main fuzzing loop: grammar -> generate -> run both parsers -> compare."""

from __future__ import annotations

import re
import time
import random
from collections import Counter, defaultdict
from pathlib import Path

from tqdm import tqdm

from peg_fuzzer.coverage import RuleCoverage
from peg_fuzzer.dedup import KnownIssues
from peg_fuzzer.grammar.parser import load_grammar_dir
from peg_fuzzer.generator.catalog import build_schema_setup, load_catalog_pools
from peg_fuzzer.generator.generator import Generator
from peg_fuzzer.minimizer import minimize
from peg_fuzzer.runner.result import Outcome, error_class
from peg_fuzzer.runner.runner import FuzzSession

_REPO_ROOT = Path(__file__).parent.parent
_INTERESTING_DIR = Path("interesting")
_KNOWN_ISSUES_FILE = _INTERESTING_DIR / "known_issues.json"
_COVERAGE_DB = _INTERESTING_DIR / "coverage.db"
_WORK_DIR = _REPO_ROOT / "test"


def _tag(outcome: Outcome) -> str:
    return {Outcome.OK: "OK", Outcome.ERROR: "ERR", Outcome.CRASH: "CRASH"}[outcome]


def _parse_duration(s: str) -> float:
    """Parse a human duration string like '30s', '10m', '2h', '1h30m' -> seconds."""
    pattern = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s.strip())
    if not pattern or not any(pattern.groups()):
        raise ValueError(
            f"Invalid duration {s!r}. Use e.g. '30s', '10m', '2h', '1h30m'."
        )
    h, m, sec = (int(g or 0) for g in pattern.groups())
    return h * 3600 + m * 60 + sec


def run_fuzzer(
    grammar_dir: str,
    start_rule: str = "Statement",
    count: int | None = None,
    duration: str | None = None,
    seed: int | None = None,
    verbose: bool = False,
) -> None:
    # Resolve run limit: duration takes priority over count; default 100 queries.
    deadline: float | None = None
    if duration is not None:
        deadline = time.monotonic() + _parse_duration(duration)
        limit = None
        print(f"Running for {duration}")
    else:
        limit = count if count is not None else 100

    if seed is None:
        seed = random.randrange(2**32)
    print(f"Seed: {seed}  (rerun with --seed {seed} to reproduce)")
    rng = random.Random(seed)
    grammar = load_grammar_dir(grammar_dir)
    pools = load_catalog_pools()
    schema_pools, setup_sql = build_schema_setup()
    pools.update(schema_pools)
    cov_db_pre = RuleCoverage(_COVERAGE_DB)
    coverage_hits = cov_db_pre.load_hits()
    cov_db_pre.close()
    gen = Generator(grammar, rng, pools=pools, coverage_hits=coverage_hits)
    known = KnownIssues(_KNOWN_ISSUES_FILE)
    session = FuzzSession(setup_sql=setup_sql, work_dir=_WORK_DIR)

    interesting_index = _next_index(_INTERESTING_DIR)

    peg_counts: dict[Outcome, int] = defaultdict(int)
    pg_counts: dict[Outcome, int] = defaultdict(int)
    peg_error_classes: Counter[str] = Counter()
    pg_error_classes: Counter[str] = Counter()
    new_issues = 0
    known_skipped = 0

    bar = tqdm(
        total=limit,  # None -> indefinite (no ETA shown)
        unit="q",
        dynamic_ncols=True,
    )
    bar.set_postfix(new=0, skip=0)

    iteration = 0
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if limit is not None and iteration >= limit:
            break
        iteration += 1

        try:
            sql = gen.generate(start_rule)
        except Exception as e:
            tqdm.write(f"[GENFAIL] {e}")
            bar.update(1)
            continue

        cmp = session.run(sql)

        peg_counts[cmp.peg.outcome] += 1
        pg_counts[cmp.postgres.outcome] += 1
        if cmp.peg.error_msg:
            peg_error_classes[error_class(cmp.peg.error_msg)] += 1
        if cmp.postgres.error_msg:
            pg_error_classes[error_class(cmp.postgres.error_msg)] += 1

        if not (cmp.any_crash or cmp.diverged or cmp.any_internal):
            if verbose:
                # Flag ERR/ERR cases where the error classes differ -- both
                # parsers reject, but at different validation stages.  Not
                # saved as an interesting file, but worth seeing in verbose mode.
                if (
                    cmp.peg.outcome == Outcome.ERROR
                    and cmp.postgres.outcome == Outcome.ERROR
                    and error_class(cmp.peg.error_msg) != error_class(cmp.postgres.error_msg)
                ):
                    tqdm.write(f"[ERR-CLR] {sql!r}")
                    tqdm.write(f"           PEG: {cmp.peg.error_msg.splitlines()[0]}")
                    tqdm.write(f"           PG:  {cmp.postgres.error_msg.splitlines()[0]}")
                else:
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
        elif cmp.diverged:
            save_file = _INTERESTING_DIR / f"diverge_{interesting_index:04d}.sql"
            label = "[DIVERGE]"
        else:
            save_file = _INTERESTING_DIR / f"internal_{interesting_index:04d}.sql"
            label = "[INTERNAL]"

        interesting_index += 1

        # Minimize the SQL before saving, using a predicate that preserves
        # the specific interesting property (crash / diverge / internal).
        if cmp.any_crash:
            check_fn = lambda c: c.any_crash
        elif cmp.diverged:
            check_fn = lambda c: c.diverged
        else:
            check_fn = lambda c: c.any_internal
        orig_token_count = len(sql.split())
        minimized = minimize(sql, session, check_fn)
        save_cmp = session.run(minimized) if minimized != sql else cmp

        _write_interesting(save_file, save_cmp)

        peg_t = _tag(save_cmp.peg.outcome)
        pg_t = _tag(save_cmp.postgres.outcome)
        tqdm.write(f"{label} PEG={peg_t} PG={pg_t}  {save_cmp.sql!r}")
        if save_cmp.peg.error_msg:
            tqdm.write(f"           PEG: {save_cmp.peg.error_msg.splitlines()[0]}")
        if save_cmp.postgres.error_msg:
            tqdm.write(f"           PG:  {save_cmp.postgres.error_msg.splitlines()[0]}")
        min_token_count = len(save_cmp.sql.split())
        if min_token_count < orig_token_count:
            tqdm.write(f"           Minimized: {orig_token_count} -> {min_token_count} tokens")
        tqdm.write(f"           => saved {save_file}")

        bar.set_postfix(new=new_issues, skip=known_skipped)
        bar.update(1)

    bar.close()
    session.close()

    # Persist coverage stats to DuckDB.
    cov_db = RuleCoverage(_COVERAGE_DB)
    cov_db.merge(
        gen.rule_hits,
        queries_run=iteration,
        seed=seed,
        start_rule=start_rule,
        new_issues=new_issues,
        ok_count=peg_counts[Outcome.OK],
        err_count=peg_counts[Outcome.ERROR],
        crash_count=peg_counts[Outcome.CRASH],
        diverge_count=new_issues,
        peg_error_classes=peg_error_classes,
        pg_error_classes=pg_error_classes,
    )

    cov = gen.coverage_stats()
    print(
        f"\nDone: {iteration} queries"
        f"\n  PEG      -- OK={peg_counts[Outcome.OK]}  ERR={peg_counts[Outcome.ERROR]}  CRASH={peg_counts[Outcome.CRASH]}"
        f"\n  Postgres -- OK={pg_counts[Outcome.OK]}  ERR={pg_counts[Outcome.ERROR]}  CRASH={pg_counts[Outcome.CRASH]}"
        f"\n  New issues={new_issues}  Known (skipped)={known_skipped}"
    )
    print(cov_db.report(set(gen.grammar.rules), top_n=15 if verbose else 10))
    run_ecr = _format_error_classes(peg_error_classes, pg_error_classes, top_n=10)
    if run_ecr:
        print(f"\n  Error class breakdown (this run):{run_ecr}")
    ecr = cov_db.error_class_report()
    if ecr:
        print(f"\n  Error class breakdown (all runs):{ecr}")
    if verbose and cov["uncovered"]:
        print(f"\n  All uncovered rules ({len(cov['uncovered'])}):")
        for name in cov["uncovered"]:
            print(f"    {name}")
    cov_db.close()


def _format_error_classes(
    peg_classes: Counter,
    pg_classes: Counter,
    top_n: int = 10,
) -> str:
    """Format per-parser error class counts as an indented string."""
    lines = []
    for label, counter in (("PEG", peg_classes), ("Postgres", pg_classes)):
        if not counter:
            continue
        lines.append(f"\n    {label}:")
        for cls, cnt in counter.most_common(top_n):
            if cls:
                lines.append(f"\n      {cls:<40s} {cnt:>6,}")
    return "".join(lines)


def _next_index(directory: Path) -> int:
    """Find the next available file index in interesting/ to avoid overwriting."""
    if not directory.exists():
        return 0
    existing = (
        list(directory.glob("diverge_*.sql"))
        + list(directory.glob("crash_*.sql"))
        + list(directory.glob("internal_*.sql"))
    )
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
    if cmp.any_crash:
        reason = "CRASH"
    elif cmp.diverged:
        reason = "DIVERGE"
    else:
        reason = "INTERNAL"
    lines = [
        f"-- {reason}",
        f"-- PEG:      {_tag(cmp.peg.outcome)}  {cmp.peg.error_msg.splitlines()[0] if cmp.peg.error_msg else ''}",
        f"-- Postgres: {_tag(cmp.postgres.outcome)}  {cmp.postgres.error_msg.splitlines()[0] if cmp.postgres.error_msg else ''}",
        "",
        cmp.sql,
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
