"""Deduplication of known divergences and crashes.

Two results are considered the same issue when their (peg_outcome,
postgres_outcome, normalized_peg_error, normalized_pg_error) tuple matches.

Error normalization strips quoted tokens (specific identifiers/values) so that
  'Catalog "foo" does not exist'
and
  'Catalog "bar" does not exist'
collapse to the same signature.

known_issues.json stores one entry per unique issue:
  {
    "signature": "<dedup key>",
    "peg_outcome": "OK",
    "postgres_outcome": "ERR",
    "peg_error": "...",          # normalized
    "postgres_error": "...",     # normalized
    "example_sql": "DETACH IF EXISTS my_table",
    "hits": 3
  }
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from peg_fuzzer.runner.result import CompareResult


def _normalize(msg: str) -> str:
    """Return the first line of an error message with specific names removed."""
    line = msg.splitlines()[0] if msg else ""
    line = re.sub(r'"[^"]*"', '"<X>"', line)
    line = re.sub(r"'[^']*'", "'<X>'", line)
    line = re.sub(r"\b\d+\b", "<N>", line)
    return line


def _signature(cmp: CompareResult) -> str:
    return json.dumps([
        cmp.peg.outcome.name,
        cmp.postgres.outcome.name,
        _normalize(cmp.peg.error_msg),
        _normalize(cmp.postgres.error_msg),
    ])


def _make_entry(cmp: CompareResult) -> dict:
    return {
        "signature": _signature(cmp),
        "peg_outcome": cmp.peg.outcome.name,
        "postgres_outcome": cmp.postgres.outcome.name,
        "peg_error": _normalize(cmp.peg.error_msg),
        "postgres_error": _normalize(cmp.postgres.error_msg),
        "example_sql": cmp.sql,
        "hits": 1,
        "resolved": False,
    }


class KnownIssues:
    """Persists a set of seen divergence/crash signatures across runs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        # signature -> entry dict
        self._entries: dict[str, dict] = {}
        if path.exists():
            try:
                for entry in json.loads(path.read_text(encoding="utf-8")):
                    self._entries[entry["signature"]] = entry
            except (json.JSONDecodeError, ValueError, KeyError):
                self._entries = {}

    def is_known(self, cmp: CompareResult) -> bool:
        return _signature(cmp) in self._entries

    def mark_seen(self, cmp: CompareResult) -> None:
        sig = _signature(cmp)
        if sig in self._entries:
            self._entries[sig]["hits"] += 1
        else:
            self._entries[sig] = _make_entry(cmp)
        self._save()

    def mark_resolved(self, cmp: CompareResult) -> None:
        """Mark an issue as resolved (e.g. fixed in the source build)."""
        sig = _signature(cmp)
        if sig in self._entries:
            self._entries[sig]["resolved"] = True
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(exist_ok=True)
        entries = sorted(self._entries.values(), key=lambda e: e["signature"])
        self._path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
