"""Run a verification command and say whether it passed.

Author: 최진호
Date:   2026-10-03

A goal is finished when a command exits with status 0, not when the model says so. This module runs
that command: in the project directory, under the same sandbox policy as the Bash tool, with a time
limit that also ends the processes it started, and with its output cut to the part that explains a
failure (the end).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from dataclasses import dataclass

from nerdvana_cli.core.sandbox import SandboxPolicy, plan_launch

DEFAULT_TIMEOUT = 300
DEFAULT_TAIL    = 4000


@dataclass(frozen=True)
class VerifyResult:
    """What one run of the verification command did."""

    passed:    bool
    exit_code: int
    timed_out: bool
    seconds:   float
    tail:      str
    note:      str = ""

    def summary(self) -> str:
        """One line for a person: the outcome and how long it took."""
        if self.timed_out:
            return f"timed out after {self.seconds:.0f}s"
        if self.note:
            return self.note
        return f"exit {self.exit_code} in {self.seconds:.1f}s"


def _tail(text: str, limit: int) -> str:
    """The last *limit* characters of *text*, cut at a line start when that loses little."""
    if len(text) <= limit:
        return text
    cut     = text[-limit:]
    newline = cut.find("\n")
    return "[output cut]\n" + (cut[newline + 1:] if 0 <= newline < limit // 4 else cut)


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """End the command and everything it started."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


async def run_verify(
    command: str,
    cwd:     str,
    *,
    timeout: float                 = DEFAULT_TIMEOUT,
    tail:    int                   = DEFAULT_TAIL,
    policy:  SandboxPolicy | None  = None,
    env:     dict[str, str] | None = None,
) -> VerifyResult:
    """Run *command* through the shell in *cwd*; passing means exit status 0.

    The command and its children share a process group that is killed when *timeout* runs out.
    With a sandbox *policy* that cannot be honoured (``require`` on a system without Landlock)
    nothing runs and the result says why.
    """
    launch = plan_launch(policy, command, cwd)
    if launch.refused:
        return VerifyResult(False, -1, False, 0.0, launch.notice, note=launch.notice)
    pipe    = asyncio.subprocess.PIPE
    started = time.monotonic()
    kwargs  = {"stdout": pipe, "stderr": asyncio.subprocess.STDOUT, "cwd": cwd, "env": env, "start_new_session": True}
    try:
        if launch.argv:
            process = await asyncio.create_subprocess_exec(*launch.argv, **kwargs)  # type: ignore[arg-type]
        else:
            process = await asyncio.create_subprocess_shell(command, **kwargs)  # type: ignore[arg-type]
    except OSError as exc:
        return VerifyResult(False, -1, False, 0.0, str(exc), note=f"could not start: {exc}")
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_group(process)
        with contextlib.suppress(Exception):
            await process.wait()
        return VerifyResult(False, -1, True, time.monotonic() - started, f"verification command exceeded {timeout:.0f}s and was stopped")
    except asyncio.CancelledError:
        _kill_group(process)
        raise
    code = process.returncode if process.returncode is not None else -1
    text = _tail(raw.decode("utf-8", errors="replace"), tail)
    return VerifyResult(code == 0, code, False, time.monotonic() - started, text)
