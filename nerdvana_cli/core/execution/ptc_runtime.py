"""The PTC runtime: a sandboxed Python runner with the host's read-only tools bound into it.

Programmatic tool calling is for the moments when a task needs many files and
one answer: comparing twenty modules, counting every call site, gathering the
lines that mention a symbol. Done with one tool call per file, each of those
files costs an LLM turn. Done here, the model writes the loop once and the
sandbox runs it, and the run gives back one JSON result.

The shape of the thing:

- the code runs in a separate process, confined by Landlock: it may read
  nothing that was not named, may write nothing at all, may execute nothing,
  and may not reach the network;
- the only way it sees a file is through a binding -- ``FileRead``, ``Grep``,
  ``Glob``, ``lsp_find_references`` -- and every call of a binding travels
  back to the host, which runs it through the same monotonic guards as any
  other tool call and records it as a ``tool/ptc-dispatch`` event;
- the host answers with the result, and the run ends with a
  :class:`PtcRunResult` the model can read in one piece.

What Landlock does not say: it governs the filesystem and the TCP network, not
the Unix sockets, so a host that offers one can still be reached from inside.
The code is the model's own, and the bindings are the audited way in.

Task 3.3 of the Harness2/DSH enhancement plan.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nerdvana_cli.core.safety.monotonic_guard import MonotonicGuardChain

# A binding: the host tool, called with the arguments the sandboxed code passed.
PtcBinding = Callable[[dict[str, Any]], Awaitable[Any]]

# The audit sink: what the runtime records about every dispatch.
PtcAudit = Callable[[str, dict[str, Any]], None]

# The event every dispatch is recorded as.
PTC_DISPATCH_EVENT = "tool/ptc-dispatch"

# How long a run may take before it is stopped.
DEFAULT_TIMEOUT_MS = 30_000

# The bootstrap of the child: it reads its own source from the pipe and runs it.
_BOOTSTRAP = (
    "import sys;"
    "ns = {'__name__': '__ptc_child__'};"
    "exec(compile(sys.stdin.read(), '<ptc-child>', 'exec'), ns);"
    "ns['main']()"
)

# The environment variable that tells the child which socket is its channel.
_FD_VARIABLE = "NERDVANA_PTC_FD"


class PtcError(RuntimeError):
    """The PTC run could not happen."""


@dataclass(frozen=True)
class PtcRunResult:
    """What one PTC run produced."""

    ok:         bool
    value:      Any  = None
    error:      str  = ""
    dispatches: int = 0
    duration_ms: int = 0
    cancelled:  bool = False
    timed_out:  bool = False
    stdout:     str  = ""

    def to_dict(self) -> dict[str, Any]:
        """The result as a plain mapping, ready to be serialized."""
        return {
            "ok":          self.ok,
            "value":       self.value,
            "error":       self.error,
            "dispatches":  self.dispatches,
            "duration_ms": self.duration_ms,
            "cancelled":   self.cancelled,
            "timed_out":   self.timed_out,
            "stdout":      self.stdout,
        }

    def to_json(self) -> str:
        """The result as JSON, the one shape the model reads."""
        return json.dumps(self.to_dict(), indent=2, default=str)


def readable_paths() -> list[str]:
    """The paths a sandboxed run may read: the Python it runs on, and nothing else.

    The project is not among them: a file is read through a binding, which the
    host audits. The home directory is not among them either, so a run cannot
    reach the credentials that live there.
    """
    import sysconfig

    candidates: list[str] = []
    for key in ("stdlib", "platstdlib"):
        path = sysconfig.get_paths().get(key, "")
        if path:
            candidates.append(path)
    for prefix in (sys.base_prefix,):
        if prefix:
            candidates.append(prefix)
    candidates.extend(("/usr/lib", "/lib", "/lib64", "/usr/lib64", "/usr/local/lib", "/etc/ld.so.cache"))
    return [path for path in candidates if os.path.exists(path)]


def sandbox_path() -> str:
    """The file that holds the Landlock policy the child loads."""
    from nerdvana_cli.core.safety import sandbox

    return str(Path(sandbox.__file__).resolve())


@dataclass
class PtcRuntime:
    """The runtime that runs a piece of code with the host's bindings in it.

    The bindings are plain functions, so this module does not have to know what
    a tool is: the tools layer decides which tools are bound and how they are
    called.
    """

    bindings:   dict[str, PtcBinding]
    guard_chain: MonotonicGuardChain
    audit:      PtcAudit | None = None
    cwd:        str = "."
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    async def run(self, code: str, abort: asyncio.Event | None = None) -> PtcRunResult:
        """Run *code* in the sandbox and answer with what it produced.

        The run stops at once when *abort* is set, and it never outlives the
        timeout. A run that was stopped says so in its result.
        """
        started  = time.monotonic()
        process, sock = self._start_child()
        try:
            reader, writer = await asyncio.open_unix_connection(sock=sock)
        except OSError as exc:
            self._stop(process)
            raise PtcError(f"cannot talk to the sandbox: {exc}") from exc
        watcher   = asyncio.create_task(self._watch_abort(abort, process))
        # The pipes of the child are read while it runs: a program that prints
        # more than the pipe holds would otherwise block and never answer.
        printed  = asyncio.create_task(self._drain(process.stdout))
        complained = asyncio.create_task(self._drain(process.stderr))
        try:
            await self._send(writer, self._request(code))
            result = await asyncio.wait_for(self._pump(reader, writer), self._timeout())
        except TimeoutError:
            result = PtcRunResult(ok=False, error=f"the run did not finish within {self.timeout_ms} ms", timed_out=True)
        except asyncio.CancelledError:
            self._stop(process)
            raise
        except PtcError as exc:
            result = PtcRunResult(ok=False, error=str(exc))
        except (OSError, ValueError) as exc:
            result = PtcRunResult(ok=False, error=f"the sandbox stopped: {exc}")
        finally:
            watcher.cancel()
            writer.close()
            self._stop(process)
        output, noise = await asyncio.gather(printed, complained)
        if abort is not None and abort.is_set():
            return PtcRunResult(ok=False, error="the run was cancelled", cancelled=True, duration_ms=_elapsed(started))
        return PtcRunResult(
            ok          = result.ok,
            value       = result.value,
            error       = result.error,
            dispatches  = result.dispatches,
            duration_ms = _elapsed(started),
            cancelled   = result.cancelled,
            timed_out   = result.timed_out,
            stdout      = _tail(output) + _tail(noise),
        )

    def _timeout(self) -> float:
        """The timeout of a run, in seconds."""
        return max(self.timeout_ms, 1) / 1000

    def _request(self, code: str) -> dict[str, Any]:
        """The message that starts the run."""
        return {
            "code":        code,
            "bindings":    sorted(self.bindings),
            "read_paths":  readable_paths(),
            "sandbox_path": sandbox_path(),
        }

    def _start_child(self) -> tuple[subprocess.Popen[bytes], socket.socket]:
        """Start the sandboxed process and the socket it talks over."""
        parent_sock, child_sock = socket.socketpair()
        source = Path(__file__).with_name("ptc_child.py").read_text(encoding="utf-8")
        env = dict(os.environ)
        env[_FD_VARIABLE] = str(child_sock.fileno())
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-c", _BOOTSTRAP],
                stdin           = subprocess.PIPE,
                stdout          = subprocess.PIPE,
                stderr          = subprocess.PIPE,
                env             = env,
                cwd             = self.cwd,
                pass_fds        = (child_sock.fileno(),),
                start_new_session = True,
            )
        except OSError as exc:
            parent_sock.close()
            child_sock.close()
            raise PtcError(f"cannot start the sandbox: {exc}") from exc
        finally:
            child_sock.close()
        if process.stdin is not None:
            process.stdin.write(source.encode("utf-8"))
            process.stdin.close()
        parent_sock.setblocking(False)
        return process, parent_sock

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> PtcRunResult:
        """Answer the calls of the sandbox until it says it is done."""
        dispatches = 0
        while True:
            line = await reader.readline()
            if not line:
                raise PtcError("the sandbox closed the channel without an answer")
            message = json.loads(line)
            kind = message.get("type")
            if kind == "call":
                dispatches += 1
                await self._answer(writer, message)
            elif kind == "done":
                return PtcRunResult(
                    ok         = bool(message.get("ok")),
                    value      = message.get("value"),
                    error      = str(message.get("error", "")),
                    dispatches = dispatches,
                )
            else:
                raise PtcError(f"the sandbox sent an unknown message: {kind}")

    async def _answer(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        """Run one binding call of the sandbox and answer it."""
        name    = str(message.get("name", ""))
        args    = message.get("args") or {}
        call_id = message.get("id")
        binding = self.bindings.get(name)
        if binding is None:
            await self._send(writer, {"type": "result", "id": call_id, "ok": False, "error": f"no binding named '{name}'"})
            return
        verdict = self.guard_chain.evaluate(name, args)
        if verdict.denied:
            self._record(name, args, denied=True)
            await self._send(writer, {
                "type": "result", "id": call_id, "ok": False,
                "error": f"refused by the {verdict.guard_name} guard: {verdict.reason}",
            })
            return
        self._record(name, args, denied=False)
        try:
            value = await binding(args)
        except Exception as exc:  # noqa: BLE001 - the sandbox has to learn what happened
            await self._send(writer, {"type": "result", "id": call_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        await self._send(writer, {"type": "result", "id": call_id, "ok": True, "value": value})

    def _record(self, name: str, args: dict[str, Any], denied: bool) -> None:
        """Record one dispatch in the audit trail."""
        if self.audit is None:
            return
        self.audit(PTC_DISPATCH_EVENT, {"tool": name, "args": args, "denied": denied})

    async def _send(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        """One message to the sandbox."""
        writer.write(json.dumps(message, default=str).encode("utf-8") + b"\n")
        await writer.drain()

    async def _drain(self, stream: Any) -> str:
        """Everything a pipe of the sandbox holds, read without blocking the loop.

        The pipe holds bytes and the result holds text, so the bytes are decoded
        here: a program that printed something has to have its output come back
        as something the model can read.
        """
        if stream is None:
            return ""
        try:
            raw = await asyncio.to_thread(stream.read)
        except (OSError, ValueError):
            return ""
        return bytes(raw).decode("utf-8", errors="replace")

    async def _watch_abort(self, abort: asyncio.Event | None, process: subprocess.Popen[bytes]) -> None:
        """End the run as soon as *abort* is set."""
        if abort is None:
            return
        await abort.wait()
        self._stop(process)

    def _stop(self, process: subprocess.Popen[bytes]) -> None:
        """End the sandboxed process, insisting when it does not stop."""
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            with contextlib.suppress(OSError):
                process.kill()


def _elapsed(started: float) -> int:
    """The milliseconds since *started*."""
    return int((time.monotonic() - started) * 1000)


# How much of what a program printed is kept in the result.
_OUTPUT_LIMIT = 8_000


def _tail(text: str) -> str:
    """The end of *text*, where the end is what a program said last."""
    if not text:
        return ""
    if len(text) <= _OUTPUT_LIMIT:
        return text
    return f"... [{len(text) - _OUTPUT_LIMIT} characters omitted]\n{text[-_OUTPUT_LIMIT:]}"
