"""Search tools — Glob and Grep."""

from __future__ import annotations

import fnmatch
import os
from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult


def _validate_search_path(path: str, cwd: str) -> str | None:
    """Validate that search path stays within cwd. Returns error message or None."""
    if os.path.isabs(path):
        return f"Absolute paths are not allowed: {path}"
    resolved     = os.path.realpath(os.path.join(cwd, path))
    cwd_resolved = os.path.realpath(cwd)
    if not resolved.startswith(cwd_resolved + os.sep) and resolved != cwd_resolved:
        return f"Path traversal blocked: {path} resolves outside working directory"
    return None


class GlobArgs:
    def __init__(self, pattern: str, path: str = "."):
        self.pattern = pattern
        self.path = path


class GlobTool(BaseTool[GlobArgs]):
    name = "Glob"
    description_text = """Fast file pattern matching using glob patterns.

Searches recursively from the given path.
Supports standard glob patterns: *, **, ?, [seq], [!seq].

Examples:
- pattern: "**/*.ts"
- pattern: "src/**/*.tsx"
- pattern: "**/test_*.py"
- pattern: "**/*.{js,ts}"""
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match (e.g., '**/*.ts')"},
            "path": {"type": "string", "description": "Directory to search from (default: '.')", "default": "."},
        },
        "required": ["pattern"],
    }
    is_concurrency_safe = True
    is_read_only = True
    args_class = GlobArgs

    async def call(
        self,
        args: GlobArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = _validate_search_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)
            search_path = os.path.join(context.cwd, args.path)
            matches = []
            for root, dirs, files in os.walk(search_path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in files:
                    filepath = os.path.relpath(os.path.join(root, name), context.cwd)
                    if fnmatch.fnmatch(filepath, args.pattern) or fnmatch.fnmatch(name, args.pattern):
                        matches.append(filepath)

            matches.sort()
            if not matches:
                return ToolResult(tool_use_id="", content=f"No files matched pattern: {args.pattern}")

            result = f"Found {len(matches)} file(s):\n" + "\n".join(matches)
            return ToolResult(tool_use_id="", content=self.truncate_result(result))

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error in glob search: {e}", is_error=True)


class GrepArgs:
    def __init__(self, pattern: str, path: str = ".", include: str = "", case_sensitive: bool = False):
        self.pattern = pattern
        self.path = path
        self.include = include
        self.case_sensitive = case_sensitive


class GrepTool(BaseTool[GrepArgs]):
    name = "Grep"
    description_text = """Search file contents using regular expressions.

Searches recursively through text files.
Supports regex patterns. Use include to filter by file pattern.

Examples:
- pattern: "def \\w+\\("
- pattern: "class \\w+", include: "*.py"
- pattern: "import.*react", include: "*.tsx"
- pattern: "TODO|FIXME", include: "*.ts\""""
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory to search from (default: '.')", "default": "."},
            "include": {
                "type": "string",
                "description": "File pattern filter (e.g., '*.py', default: all files)",
                "default": "",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive search (default: false)",
                "default": False,
            },
        },
        "required": ["pattern"],
    }
    is_concurrency_safe = True
    is_read_only = True
    args_class = GrepArgs

    async def call(
        self,
        args: GrepArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = _validate_search_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)

            import re

            flags = 0 if args.case_sensitive else re.IGNORECASE
            regex = re.compile(args.pattern, flags)

            search_path = os.path.join(context.cwd, args.path)
            results: list[str] = []
            files_with_matches: set[str] = set()
            match_count = 0

            for root, dirs, files in os.walk(search_path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in files:
                    if args.include and not fnmatch.fnmatch(name, args.include):
                        continue

                    filepath = os.path.join(root, name)
                    rel_path = os.path.relpath(filepath, context.cwd)

                    try:
                        with open(filepath, encoding="utf-8", errors="replace") as f:
                            for line_num, line in enumerate(f, 1):
                                if regex.search(line):
                                    match_count += 1
                                    files_with_matches.add(rel_path)
                                    if match_count <= 100:
                                        results.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                    except (PermissionError, OSError):
                        continue

            if match_count == 0:
                return ToolResult(tool_use_id="", content=f"No matches found for pattern: {args.pattern}")

            file_count = len(files_with_matches)
            header = f"Found {match_count} match(es) in {file_count} file(s):\n"
            return ToolResult(tool_use_id="", content=self.truncate_result(header + "\n".join(results)))

        except re.error as e:
            return ToolResult(tool_use_id="", content=f"Invalid regex pattern: {e}", is_error=True)
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error in grep search: {e}", is_error=True)
