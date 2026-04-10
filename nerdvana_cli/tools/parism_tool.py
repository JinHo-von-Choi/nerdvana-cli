"""Parism tool -- structured shell execution via @nerdvana/parism MCP."""

from __future__ import annotations

import json
from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult


class ParismArgs:
    def __init__(self, cmd: str, args: list[str] | None = None,
                 cwd: str = "", output_format: str = "json"):
        self.cmd    = cmd
        self.args   = args or []
        self.cwd    = cwd
        self.format = output_format


class ParismTool(BaseTool[ParismArgs]):
    name = "Parism"
    description_text = """Execute a shell command with structured JSON output and security guard.

Preferred over Bash for all standard commands. Returns structured parsed output
alongside raw text. Commands are validated against a whitelist with injection prevention.

Supported: ls, find, stat, du, df, tree, ps, ping, curl, netstat, grep, wc,
head, tail, cat, git, env, pwd, which, free, uname, docker, kubectl, npm, and more (44 commands).

Use Bash only when Parism blocks the command (e.g., piped commands, non-whitelisted binaries).

Output format: JSON with ok, exitCode, stdout.raw, stdout.parsed, stderr, duration_ms.
Use format="compact" to reduce token cost for list-type outputs."""

    input_schema = {
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "Command name (e.g., 'ls', 'git', 'ps')"},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command arguments (default: [])",
                "default": [],
            },
            "cwd": {"type": "string", "description": "Working directory (default: current)", "default": ""},
            "format": {
                "type": "string",
                "enum": ["json", "compact"],
                "description": "Output format. 'compact' reduces tokens for lists.",
                "default": "json",
            },
        },
        "required": ["cmd"],
    }
    args_class          = ParismArgs
    is_concurrency_safe = False
    is_read_only        = False
    is_destructive      = False

    _client: Any = None

    def set_client(self, client: Any) -> None:
        """Inject the ParismClient instance."""
        self._client = client

    async def call(
        self,
        args: ParismArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        if self._client is None or not self._client.is_connected:
            return ToolResult(
                tool_use_id="",
                content="Parism is not connected. Use Bash tool as fallback.",
                is_error=True,
            )

        try:
            result = await self._client.run(
                cmd=args.cmd,
                args=args.args,
                cwd=args.cwd or context.cwd,
                output_format=args.format,
            )

            if not result.get("ok", False):
                guard_error = result.get("guard_error", {})
                if guard_error:
                    return ToolResult(
                        tool_use_id="",
                        content=f"Guard blocked: {guard_error.get('message', 'Unknown guard error')}",
                        is_error=True,
                    )
                stderr    = result.get("stderr", {}).get("raw", "")
                exit_code = result.get("exitCode", 1)
                return ToolResult(
                    tool_use_id="",
                    content=f"[exit code: {exit_code}]\n{stderr}",
                    is_error=True,
                )

            stdout   = result.get("stdout", {})
            parsed   = stdout.get("parsed")
            raw      = stdout.get("raw", "")
            duration = result.get("duration_ms", 0)

            output = json.dumps(parsed, ensure_ascii=False, indent=2) if parsed is not None else raw

            footer = f"\n[{args.cmd} completed in {duration}ms]"
            return ToolResult(tool_use_id="", content=self.truncate_result(output + footer))

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Parism error: {e}", is_error=True)
