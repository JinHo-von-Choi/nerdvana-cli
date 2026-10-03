"""Which files a shell command changed in a git working tree.

Author: 최진호
Date:   2026-10-03

The edit tools know what they changed; a shell command (a formatter, a code generator, ``sed -i``, a
test run that rewrites fixtures) changes files the model never sees named. A snapshot of the dirty files
before the command and another after it names the ones that differ, so the result can say so.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

MAX_TRACKED = 300
MAX_BYTES   = 2_000_000
MAX_LISTED  = 10


async def _git(cwd: str, *args: str) -> bytes | None:
    try:
        proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (OSError, TimeoutError):
        return None
    return out if proc.returncode == 0 else None


async def snapshot(cwd: str) -> dict[str, str] | None:
    """Path to content digest for every modified or untracked file; None outside a git working tree."""
    listed = await _git(cwd, "status", "--porcelain=v1", "-z", "-uall")
    if listed is None:
        return None
    entries = [item[3:].decode("utf-8", errors="replace") for item in listed.split(b"\0") if len(item) > 3]
    state: dict[str, str] = {}
    for relative in entries[:MAX_TRACKED]:
        path = Path(cwd) / relative
        try:
            state[relative] = hashlib.sha1(path.read_bytes()[:MAX_BYTES]).hexdigest() if path.is_file() else "gone"
        except OSError:
            state[relative] = "unreadable"
    return state


def changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Sorted paths that are new, gone or different between two snapshots."""
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def report(paths: list[str]) -> str:
    """The line appended to a command's output, or an empty string when nothing changed."""
    if not paths:
        return ""
    listed = ", ".join(paths[:MAX_LISTED])
    more   = f" (+{len(paths) - MAX_LISTED} more)" if len(paths) > MAX_LISTED else ""
    return f"\n[Files changed by this command: {listed}{more}]"
