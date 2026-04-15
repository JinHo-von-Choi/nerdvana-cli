"""Git status helper for the sidebar modified-files section."""
from __future__ import annotations

import asyncio
import subprocess


def _run_sync(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


async def fetch_porcelain(cwd: str) -> str:
    """Run git status porcelain in cwd. Returns empty string on failure."""
    return await asyncio.to_thread(_run_sync, cwd)


def parse_porcelain(raw: str) -> list[tuple[str, str]]:
    """Parse git status porcelain output.

    Returns a list of (status_letter, path). The letter is the first
    non-space char of the 2-column status (index or worktree, whichever is set).
    """
    rows: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if len(line) < 3:
            continue
        xy   = line[:2]
        path = line[3:]
        letter = xy.strip()[:1] or " "
        if letter == "?":
            rows.append(("?", path))
        else:
            rows.append((letter, path))
    return rows
