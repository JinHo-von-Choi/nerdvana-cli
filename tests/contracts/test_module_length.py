"""Module length ratchet for nerdvana_cli.

A module longer than ``LIMIT`` lines must be listed in ``KNOWN_LARGE`` with the
length it has today. Listed modules may not grow, and a listed module that has
shrunk to the limit or vanished must be removed from the list, so the list only
ever gets shorter.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from tests.contracts.import_graph import ROOT

LIMIT = 600

KNOWN_LARGE: dict[str, int] = {
    "core/loop/agent_loop.py": 825,
    "tools/file_tools.py": 706,
    "core/state/checkpoint.py": 642,
}


def _large_modules() -> dict[str, int]:
    lengths = {
        path.relative_to(ROOT).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(ROOT.rglob("*.py"))
    }
    return {name: length for name, length in lengths.items() if length > LIMIT}


def test_no_new_module_is_longer_than_the_limit_and_known_ones_do_not_grow() -> None:
    problems = [
        f"{name}: {length} lines" + (f" (was {KNOWN_LARGE[name]})" if name in KNOWN_LARGE else f" (limit {LIMIT})")
        for name, length in _large_modules().items()
        if length > KNOWN_LARGE.get(name, LIMIT)
    ]
    assert problems == []


def test_entries_that_shrank_or_vanished_are_removed_from_the_list() -> None:
    current = _large_modules()
    stale   = [name for name in KNOWN_LARGE if name not in current]
    assert stale == []
