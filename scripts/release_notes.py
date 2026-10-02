#!/usr/bin/env python3
"""Release helpers used by .github/workflows/release.yml (stdlib only).

Usage:
    python scripts/release_notes.py version              # print the pyproject version
    python scripts/release_notes.py check-tag <tag>      # exit 1 unless tag == "v" + version
    python scripts/release_notes.py notes <version>      # print the CHANGELOG section

`notes` exits 1 when CHANGELOG.md has no non-empty section for the version, so
the caller can fall back to generated release notes.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

_HEADING_RE = re.compile(r"^##\s+\[([^\]]+)\]", re.MULTILINE)


def project_version(pyproject: Path = PYPROJECT) -> str:
    """Return the version declared in pyproject.toml."""
    with pyproject.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def tag_matches(tag: str, version: str) -> bool:
    """True when the tag is exactly "v" followed by the project version."""
    return tag == f"v{version}"


def extract_section(changelog: str, version: str) -> str | None:
    """Return the body of the `## [version]` section, or None when absent or empty."""
    headings = list(_HEADING_RE.finditer(changelog))
    for index, match in enumerate(headings):
        if match.group(1).strip() != version:
            continue
        end  = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        body = changelog[match.end():end]
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = re.split(r"^\[[^\]]+\]:\s", body, maxsplit=1, flags=re.MULTILINE)[0].strip()
        return body or None
    return None


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "version" and len(argv) == 2:
        print(project_version())
        return 0
    if len(argv) == 3 and argv[1] == "check-tag":
        version = project_version()
        if tag_matches(argv[2], version):
            return 0
        print(f"tag {argv[2]} does not match pyproject version v{version}", file=sys.stderr)
        return 1
    if len(argv) == 3 and argv[1] == "notes":
        section = extract_section(CHANGELOG.read_text(encoding="utf-8"), argv[2])
        if section is None:
            print(f"no CHANGELOG section for {argv[2]}", file=sys.stderr)
            return 1
        print(section)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
