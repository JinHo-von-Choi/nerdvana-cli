"""The ``run_code`` tool: a sandboxed Python program with the read-only tools in it.

The tool is the front of the PTC runtime (see ``core/execution/ptc_runtime.py``).
It decides which of the host's read-only tools are bound into the sandbox, and
it records every call of them in the session's event log.

Only the tools that read are bound: a program that runs in the sandbox may
look at the repository, and it may change nothing.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from nerdvana_cli.core.execution.ptc_runtime import (
    DEFAULT_TIMEOUT_MS,
    PtcBinding,
    PtcError,
    PtcRuntime,
)
from nerdvana_cli.core.safety.monotonic_guard import default_guard_chain
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

# The tools a program in the sandbox may call. They read; none of them writes.
READ_ONLY_BINDINGS: tuple[str, ...] = ("FileRead", "Grep", "Glob", "lsp_find_references")

# The header of a Glob answer, and the header of a Grep answer.
_GLOB_HEADER = "Found "
_GREP_HEADER = "Found "
_NO_FILES   = "No files matched"
_NO_MATCHES = "No matches found"

# The length of the hash in an anchor, and the spaces that follow it.
_HASH_LEN = 6
_ANCHOR_GAP = 4


def _paths_of(content: str) -> list[str]:
    """The paths of a Glob answer: the lines after its header."""
    if content.startswith(_NO_FILES):
        return []
    lines = content.splitlines()
    return [line for line in lines[1:] if line.strip()] if lines and lines[0].startswith(_GLOB_HEADER) else []


def _matches_of(content: str) -> list[str]:
    """The matches of a Grep answer: the lines after its header."""
    if content.startswith(_NO_MATCHES):
        return []
    lines = content.splitlines()
    return [line for line in lines[1:] if line.strip()] if lines and lines[0].startswith(_GREP_HEADER) else []


def _text_of(content: str) -> str:
    """The text of a FileRead answer: the lines after its header, without the anchors."""
    lines = content.splitlines()
    if not lines or not lines[0].startswith("[File: "):
        return content
    body: list[str] = []
    for line in lines[1:]:
        number, sep, rest = line.partition("#")
        if sep and number.strip().isdigit() and len(rest) >= _HASH_LEN + _ANCHOR_GAP:
            body.append(rest[_HASH_LEN + _ANCHOR_GAP:])
        else:
            body.append(line)
    return "\n".join(body)


class RunCodeArgs:
    def __init__(self, code: str, timeout_ms: int = 0):
        self.code = code
        self.timeout_ms = timeout_ms


class RunCodeTool(BaseTool[RunCodeArgs]):
    name = "run_code"
    description_text = """Run a Python program with the read-only tools bound into it.

The program runs in a sandbox: it cannot write, execute or reach the network,
and it sees no file except through the bindings. Use it when a task needs many
files and one answer, so the loop costs one tool call instead of one per file.

Bound tools, each returning data: FileRead(path, offset, limit) gives the text
of the file, Grep(pattern, path, include, case_sensitive) gives the matching
lines as "path:line: text", Glob(pattern, path) gives the paths, and
lsp_find_references(file_path, line, symbol) gives the places. A failed call
returns "ERROR: ...".

The value of the last expression is the result. The program may import json,
re, math and a few other modules of the standard library.

Example: code: "sum(len(FileRead(path=f).splitlines()) for f in Glob(pattern='**/*.py'))"
"""
    input_schema = {
        "type": "object",
        "properties": {
            "code":       {"type": "string",  "description": "The Python program to run"},
            "timeout_ms": {"type": "integer", "description": "How long the run may take (default 30000)", "default": 0},
        },
        "required": ["code"],
    }
    is_concurrency_safe    = False
    args_class             = RunCodeArgs
    max_result_tokens      = 60_000
    category               = ToolCategory.READ
    side_effects           = ToolSideEffect.PROCESS
    tags: ClassVar[frozenset[str]] = frozenset({"code", "analysis"})
    requires_confirmation  = False

    def __init__(self, parent_registry: Any, settings: Any = None) -> None:
        super().__init__()
        self._registry = parent_registry
        self._settings = settings
        self._session: Any | None = None

    def set_session(self, session: Any | None) -> None:
        """Let the tool record the calls it dispatches in the session's event log."""
        self._session = session

    async def call(
        self,
        args:        RunCodeArgs,
        context:     ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        """Run the program in the sandbox and answer with what it produced."""
        bindings = self._bindings(context)
        if not bindings:
            return ToolResult(tool_use_id="", content="No read-only tool is bound; there is nothing to run.", is_error=True)
        runtime = PtcRuntime(
            bindings    = bindings,
            guard_chain = default_guard_chain(Path(context.cwd), self._protected(context)),
            audit       = self._audit,
            cwd         = context.cwd,
            timeout_ms  = args.timeout_ms or DEFAULT_TIMEOUT_MS,
        )
        try:
            result = await runtime.run(args.code, abort=context.state.get("abort"))
        except PtcError as exc:
            return ToolResult(tool_use_id="", content=f"The sandboxed runner is not available: {exc}", is_error=True)
        return ToolResult(
            tool_use_id = "",
            content     = self.truncate_result(result.to_json()),
            is_error    = not result.ok,
        )

    def _protected(self, context: ToolContext) -> frozenset[str]:
        """The test files this run may not change, as the guards know them."""
        return frozenset(str(path) for path in (context.state.get("protected_tests") or ()))

    def _bindings(self, context: ToolContext) -> dict[str, PtcBinding]:
        """The host tools the program may call, each wrapped for the sandbox."""
        bindings: dict[str, PtcBinding] = {}
        for name in READ_ONLY_BINDINGS:
            tool = self._registry.get(name)
            if tool is not None:
                bindings[name] = self._binding_for(name, tool, context)
        return bindings

    def _binding_for(self, name: str, tool: Any, context: ToolContext) -> PtcBinding:
        """One host tool, as the sandbox sees it: data, not a formatted answer.

        The tool formats its answer for a model to read; a program needs the
        answer itself, so the binding hands over the paths, the matches or the
        text, and never the header the tool put in front of them.
        """
        async def call(args: dict[str, Any]) -> Any:
            result = await tool.call(tool.parse_args(args), context, None)
            if result.is_error:
                return f"ERROR: {result.content}"
            if name == "Glob":
                return _paths_of(result.content)
            if name == "Grep":
                return _matches_of(result.content)
            if name == "FileRead":
                return _text_of(result.content)
            return result.content

        return call

    def _audit(self, event: str, data: dict[str, Any]) -> None:
        """Record one dispatch in the session's event log."""
        if self._session is not None:
            self._session.record(event, data)
