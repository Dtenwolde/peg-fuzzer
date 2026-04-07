"""Load keyword sets from .list files."""

from __future__ import annotations

from pathlib import Path


def _load_list(path: Path) -> frozenset[str]:
    return frozenset(
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


def load_keywords(grammar_dir: str | Path) -> dict[str, frozenset[str]]:
    """Return keyword sets keyed by category name.

    Keys: 'reserved', 'unreserved', 'col_name', 'type_func'
    """
    kw_dir = Path(grammar_dir) / "keywords"
    return {
        "reserved": _load_list(kw_dir / "reserved_keyword.list"),
        "unreserved": _load_list(kw_dir / "unreserved_keyword.list"),
        "col_name": _load_list(kw_dir / "column_name_keyword.list"),
        "type_func": _load_list(kw_dir / "func_name_keyword.list")
        | _load_list(kw_dir / "type_name_keyword.list"),
    }
