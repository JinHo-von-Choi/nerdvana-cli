"""session.statusline_command: a user command whose output the status bar shows."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

STATUSLINE_TIMEOUT = 2.0
STATUSLINE_MAX_CHARS = 120


async def run_statusline(command: str, payload: dict[str, Any], cwd: str) -> str:
    """The first line *command* prints when given *payload* as JSON; empty when it fails or is slow."""
    try:
        process = await asyncio.create_subprocess_shell(
            command, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.info("statusline command could not start: %s", exc)
        return ""
    try:
        out, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), STATUSLINE_TIMEOUT)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        logger.info("statusline command took longer than %.0fs", STATUSLINE_TIMEOUT)
        return ""
    if process.returncode != 0:
        return ""
    line = next((part.strip() for part in out.decode(errors="replace").splitlines() if part.strip()), "")
    return line[:STATUSLINE_MAX_CHARS]
