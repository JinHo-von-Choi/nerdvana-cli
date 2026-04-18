"""Bash tool — shell command execution."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult


class BashArgs:
    def __init__(self, command: str, timeout: int = 120, description: str = ""):
        self.command = command
        self.timeout = timeout
        self.description = description


class BashTool(BaseTool[BashArgs]):
    name = "Bash"
    description_text = """Execute a bash shell command.

Use this for running shell commands, scripts, and programs.
Commands run in the current working directory.
Long-running commands will be terminated after the timeout.
Output is captured (stdout + stderr).

Examples:
- ls -la
- npm install
- python script.py
- git status"""
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)", "default": 120},
            "description": {"type": "string", "description": "Brief description of what this command does"},
        },
        "required": ["command"],
    }
    is_concurrency_safe    = False
    is_destructive         = False
    args_class             = BashArgs
    category               = ToolCategory.WRITE
    side_effects           = ToolSideEffect.PROCESS
    tags: ClassVar[frozenset[str]]  = frozenset({"shell"})
    requires_confirmation          = False

    _DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"\brm\s+(?:-\w*r\w*f|-\w*f\w*r)\s+[/~*]"),
        re.compile(r"\brm\s+(?:-\w*r\w*f|-\w*f\w*r)\s+\*"),
        re.compile(r"\brm\s+-r\s+-f\s+[/~*]"),
        re.compile(r"\brm\s+-f\s+-r\s+[/~*]"),
        re.compile(r"\brm\s+(?:-\w*r\w*f|-\w*f\w*r)\s+\."),
        re.compile(r"\bmkfs\b"),
        re.compile(r"\bdd\s+if=/dev/(?:sd|nvme|vd|hd)"),
        re.compile(r">\s*/dev/(?:sd|nvme|vd|hd)"),
        re.compile(r":\(\)\s*\{.*\}"),
        re.compile(r":\(\)\{"),
        re.compile(r"\|\s*(?:/(?:usr/)?(?:bin/)?)?(?:ba)?sh\b"),
        re.compile(r"\|\s*(?:/(?:usr/)?(?:bin/)?)?zsh\b"),
        re.compile(r"base64\s+.*\|\s*(?:ba)?sh"),
        re.compile(r"(?:curl|wget)\s.*\|\s"),
        re.compile(r"\bchmod\s+(?:-R\s+|--recursive\s+)?(?:000|777)\b"),
        re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b"),
        re.compile(r"\bgit\s+push\s+(?:.*\s)?(?:--force|-f)\b"),
        # Long-option variants for rm
        re.compile(r"\brm\s+.*--no-preserve-root\b"),
        re.compile(r"\brm\s+.*--recursive\s+.*--force\b"),
        re.compile(r"\brm\s+.*--force\s+.*--recursive\b"),
        # Gap fixes — interpreter -c/-e/-r arbitrary code execution
        re.compile(r"\b(?:python|python2|python3)(?:\d*(?:\.\d+)?)?(?:\s+-\w+)*\s+-c\b"),
        re.compile(r"\b(?:perl|ruby|node|nodejs)(?:\s+-\w+)*\s+-e\b"),
        re.compile(r"\bphp(?:\s+-\w+)*\s+-r\b"),
        # Gap fixes — download+source/exec across commands
        re.compile(r"(?:curl|wget)\s.*(?:&&|;|\|\||\n).*\b(?:ba)?sh\s+\S"),
        re.compile(r"(?:curl|wget)\s.*(?:&&|;|\|\||\n).*\bzsh\s+\S"),
        re.compile(r"(?:curl|wget)\s.*(?:&&|;|\|\||\n).*\bsource\s+\S"),
        re.compile(r"(?:curl|wget)\s.*(?:&&|;|\|\||\n)\s*\.\s+\S"),
        # Gap fixes — env-var home deletion
        re.compile(r"\brm\s+(?:-\w*r\w*f|-\w*f\w*r)\s+[\"']?\$\{?(?:HOME|PWD|OLDPWD)\}?"),
        re.compile(r"\brm\s+-r\s+-f\s+[\"']?\$\{?(?:HOME|PWD|OLDPWD)\}?"),
        re.compile(r"\brm\s+-f\s+-r\s+[\"']?\$\{?(?:HOME|PWD|OLDPWD)\}?"),
        # Gap fixes — block device writes via tee
        re.compile(r"\btee\b.*/dev/(?:sd|nvme|vd|hd)"),
        # Gap fixes — find -delete / find -exec rm combos
        re.compile(r"\bfind\s+.*\s-delete\b"),
        re.compile(r"\bfind\s+.*\s-exec\s+rm\b"),
        # Substitution bypass prevention — command/variable/backtick substitution
        # These patterns detect injection vehicles that wrap blacklisted commands.
        # We re-check the *inner content* of each substitution form separately.
        re.compile(r"\$\([^)]*\brm\s+(?:-\w*r\w*f|-\w*f\w*r|-r\s+-f|-f\s+-r)[^)]*\)"),
        re.compile(r"\$\([^)]*\bmkfs\b[^)]*\)"),
        re.compile(r"\$\([^)]*\bdd\s+if=/dev/(?:sd|nvme|vd|hd)[^)]*\)"),
        re.compile(r"\$\([^)]*\b(?:shutdown|reboot|halt|poweroff)\b[^)]*\)"),
        re.compile(r"\$\{[^}]*\brm\s+(?:-\w*r\w*f|-\w*f\w*r)[^}]*\}"),
        re.compile(r"`[^`]*\brm\s+(?:-\w*r\w*f|-\w*f\w*r|-r\s+-f|-f\s+-r)[^`]*`"),
        re.compile(r"`[^`]*\bmkfs\b[^`]*`"),
        re.compile(r"`[^`]*\b(?:shutdown|reboot|halt|poweroff)\b[^`]*`"),
        # Gap fix — eval/exec as arbitrary code execution paths
        re.compile(r"\beval\s+"),
        re.compile(r"\bexec\s+"),
        # Gap fix — dd writing to block devices (dd of= variant)
        re.compile(r"\bdd\s+(?:\S+\s+)*of=/dev/(?:sd|nvme|vd|hd)"),
        # Gap fix — env-prefix sudo bypass: FOO=1 sudo rm ...
        re.compile(r"(?:\w+=\S+\s+)+sudo\s+"),
    ]

    _ASK_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"^\s*(?:printenv|env)\s*$"),
        re.compile(r"\bcurl\s+.*-d\b"),
        re.compile(r"\bwget\s+--post"),
    ]

    _MAX_TIMEOUT: int = 600  # seconds — hard ceiling to prevent indefinite occupation

    def check_permissions(self, args: BashArgs, context: ToolContext) -> PermissionResult:
        # Clamp timeout to hard ceiling — callers cannot exceed this.
        if args.timeout > self._MAX_TIMEOUT:
            args.timeout = self._MAX_TIMEOUT

        cmd_stripped = re.sub(r"^\s*sudo\s+", "", args.command.strip())
        full_cmd = args.command.strip()

        for pattern in self._DANGEROUS_PATTERNS:
            if pattern.search(cmd_stripped) or pattern.search(full_cmd):
                return PermissionResult(
                    behavior=PermissionBehavior.DENY,
                    message=f"Dangerous command blocked: {pattern.pattern}",
                )

        for pattern in self._ASK_PATTERNS:
            if pattern.search(cmd_stripped):
                return PermissionResult(
                    behavior=PermissionBehavior.ASK,
                    message=f"This command may expose sensitive information: {args.command[:50]}",
                )

        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    async def call(
        self,
        args: BashArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            env = {**os.environ, "PWD": context.cwd}
            proc = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.cwd,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=args.timeout)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(
                    tool_use_id="",
                    content=f"Command timed out after {args.timeout}s: {args.command}",
                    is_error=True,
                )

            output = ""
            if stdout:
                output += stdout.decode("utf-8", errors="replace")
            if stderr:
                err_text = stderr.decode("utf-8", errors="replace")
                if err_text.strip():
                    output += f"\n[stderr]\n{err_text}" if output else err_text

            exit_code = proc.returncode or 0
            if exit_code != 0:
                output = f"[exit code: {exit_code}]\n{output}"

            return ToolResult(tool_use_id="", content=self.truncate_result(output))

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error executing command: {e}", is_error=True)


def create_bash_tool() -> BashTool:
    return BashTool()
