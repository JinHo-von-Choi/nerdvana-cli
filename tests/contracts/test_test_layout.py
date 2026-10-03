"""Where tests live: under tests/<package>/, in directories that are packages.

A test module at the top of tests/ must be listed in ``LOOSE_TESTS``, which may
only shrink. Every directory under tests/ that holds test modules has an
``__init__.py``, so two test modules with the same file name in different
directories never collide on import.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

LOOSE_TESTS: frozenset[str] = frozenset()


def _loose() -> set[str]:
    return {path.name for path in TESTS.glob("test_*.py")}


def test_no_test_module_sits_at_the_top_of_tests_outside_the_list() -> None:
    assert sorted(_loose() - LOOSE_TESTS) == []


def test_every_listed_loose_test_still_exists() -> None:
    assert sorted(LOOSE_TESTS - _loose()) == []


def test_every_directory_with_tests_is_a_package() -> None:
    missing = sorted(
        directory.relative_to(TESTS).as_posix()
        for directory in {path.parent for path in TESTS.rglob("test_*.py")}
        if not (directory / "__init__.py").is_file()
    )
    assert missing == []
