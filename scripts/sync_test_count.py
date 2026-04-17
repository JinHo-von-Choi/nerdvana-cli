"""
Sync test count references in NIRNA.md and CHANGELOG.md with the actual
pytest --collect-only output.

Usage:
    python scripts/sync_test_count.py          # update in place
    python scripts/sync_test_count.py --check  # detect drift, exit 1 if found

Author: 최진호
Date:   2026-04-17
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
NIRNA_MD  = REPO_ROOT / "NIRNA.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches the pytest summary line regardless of surrounding `=` decoration:
#   "465 tests collected in 1.71s" or
#   "===... 465 tests collected in 1.71s ===..."
_COLLECTED_RE = re.compile(r"(\d+)\s+tests collected")

# NIRNA.md target: "(pytest-asyncio auto mode, 272 tests)"
# Captures the prefix "pytest-asyncio auto mode, " and the old count.
_NIRNA_RE = re.compile(
    r"(\(pytest-asyncio auto mode, )(\d+)( tests\))",
    re.ASCII,
)

# CHANGELOG.md: standalone lines that mention the *current total* test count
# as a bare number followed by "tests" (not part of a range like "316 → 399").
# Pattern: a 3+-digit number immediately followed by optional space + "tests"
# but NOT preceded by "→ " (which marks a range endpoint inside history lines).
_CHANGELOG_TOTAL_RE = re.compile(
    r"(?<!→ )(?<!\d)(\d{3,})(\s*tests collected)",
    re.ASCII,
)


def _collect_test_count() -> int:
    """Run pytest --collect-only -q and parse the collected count."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    output = result.stdout + result.stderr
    match  = _COLLECTED_RE.search(output)
    if not match:
        raise SystemExit(
            "ERROR: could not parse 'N tests collected' from pytest output.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return int(match.group(1))


def _update_nirna(new_count: int, *, check: bool) -> bool:
    """
    Replace the test-count number inside NIRNA.md's auto-mode marker.

    Returns True if a change was made / detected.
    """
    if not NIRNA_MD.exists():
        return False

    original = NIRNA_MD.read_text(encoding="utf-8")
    changed  = False

    def _replacer(m: re.Match[str]) -> str:
        nonlocal changed
        old = int(m.group(2))
        if old == new_count:
            return m.group(0)
        changed = True
        if not check:
            print(f"NIRNA.md: {old} -> {new_count} tests")
        else:
            print(f"NIRNA.md: drift detected ({old} != {new_count})")
        return m.group(1) + str(new_count) + m.group(3)

    replaced = _NIRNA_RE.sub(_replacer, original)

    if changed and not check:
        NIRNA_MD.write_text(replaced, encoding="utf-8")

    if not changed:
        print("NIRNA.md: no change")

    return changed


def _update_changelog(new_count: int, *, check: bool) -> bool:
    """
    Update standalone 'N tests collected' references in CHANGELOG.md.

    History lines like "grows from 316 to 463" are intentionally preserved
    because the regex requires the literal suffix " tests collected" rather
    than bare " tests", and does not match range endpoints preceded by "→ ".

    Returns True if a change was made / detected.
    """
    if not CHANGELOG.exists():
        return False

    original = CHANGELOG.read_text(encoding="utf-8")
    changed  = False

    def _replacer(m: re.Match[str]) -> str:
        nonlocal changed
        old = int(m.group(1))
        if old == new_count:
            return m.group(0)
        changed = True
        if not check:
            print(f"CHANGELOG.md: {old} -> {new_count} tests collected")
        else:
            print(f"CHANGELOG.md: drift detected ({old} != {new_count})")
        return str(new_count) + m.group(2)

    replaced = _CHANGELOG_TOTAL_RE.sub(_replacer, original)

    if changed and not check:
        CHANGELOG.write_text(replaced, encoding="utf-8")

    if not changed:
        print("CHANGELOG.md: no change")

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync test count references with pytest --collect-only output.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Detect drift and exit 1 without modifying files.",
    )
    args = parser.parse_args(argv)

    new_count = _collect_test_count()
    print(f"pytest collected: {new_count} tests")

    any_drift = False

    if _update_nirna(new_count, check=args.check):
        any_drift = True

    if _update_changelog(new_count, check=args.check):
        any_drift = True

    if args.check and any_drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
