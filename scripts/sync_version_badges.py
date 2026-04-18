"""
Sync shields.io version badge URLs in README files with pyproject.toml version.

Usage:
    python scripts/sync_version_badges.py          # update in place
    python scripts/sync_version_badges.py --check  # detect drift, exit 1 if found

Author: 최진호
Date:   2026-04-17
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT    = Path(__file__).parent.parent
PYPROJECT    = REPO_ROOT / "pyproject.toml"
TARGET_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "README.ko.md",
]

# Matches the version segment inside a shields.io badge URL.
# Group 1 captures the old version string.
_BADGE_RE = re.compile(
    r"(https://img\.shields\.io/badge/version-)([^-]+)(-[^\"'> ]+)",
    re.ASCII,
)


def _read_version() -> str:
    """Extract [project].version from pyproject.toml using tomllib (stdlib ≥ 3.11)."""
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        return str(data["project"]["version"])
    except KeyError as exc:
        raise SystemExit(f"ERROR: {PYPROJECT} missing [project].version") from exc


def _process_file(
    path: Path,
    new_version: str,
    *,
    check: bool,
) -> bool:
    """
    Scan *path* for version badges and optionally rewrite.

    Returns True if the file contained at least one badge that differs from
    *new_version* (i.e. a drift was detected / a change was made).
    """
    if not path.exists():
        return False

    original = path.read_text(encoding="utf-8")
    changed  = False

    def _replacer(m: re.Match[str]) -> str:
        nonlocal changed
        old_version: str = str(m.group(2))
        if old_version == new_version:
            return str(m.group(0))
        changed = True
        if not check:
            print(f"{path.name}: {old_version} -> {new_version}")
        else:
            print(f"{path.name}: drift detected ({old_version} != {new_version})")
        return str(m.group(1)) + new_version + str(m.group(3))

    replaced = _BADGE_RE.sub(_replacer, original)

    if changed and not check:
        path.write_text(replaced, encoding="utf-8")

    if not changed:
        print(f"{path.name}: no change")

    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync shields.io version badges with pyproject.toml version.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Detect drift and exit 1 without modifying files.",
    )
    args = parser.parse_args(argv)

    new_version = _read_version()
    any_drift   = False

    for target in TARGET_FILES:
        if _process_file(target, new_version, check=args.check):
            any_drift = True

    if args.check and any_drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
