"""Search tools — Glob and Grep."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from collections.abc import Iterator
from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult
from nerdvana_cli.utils.path import safe_open_fd, validate_path


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
    is_concurrency_safe    = True
    args_class             = GlobArgs
    category               = ToolCategory.READ
    side_effects           = ToolSideEffect.FILESYSTEM
    tags: ClassVar[frozenset[str]] = frozenset({"search"})
    requires_confirmation  = False

    async def call(
        self,
        args: GlobArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = validate_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)
            search_path = os.path.join(context.cwd, args.path)
            matches     = sorted(await asyncio.to_thread(_glob, search_path, context.cwd, args.pattern))
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
- pattern: "TODO|FIXME", include: "*.ts"
- pattern: "class \\w+Tool\\b", path: "src/tools", include: "*.py", case_sensitive: true (without case_sensitive the match ignores case)"""
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
    is_concurrency_safe    = True
    args_class             = GrepArgs
    category               = ToolCategory.READ
    side_effects           = ToolSideEffect.FILESYSTEM
    tags: ClassVar[frozenset[str]] = frozenset({"search"})
    requires_confirmation  = False

    async def call(
        self,
        args: GrepArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = validate_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)

            regex = re.compile(args.pattern, 0 if args.case_sensitive else re.IGNORECASE)
            # The walk and the reads block, so they run off the event loop.
            match_count, file_count, results = await asyncio.to_thread(
                _grep, os.path.join(context.cwd, args.path), context.cwd, regex, args.include,
            )
            if match_count == 0:
                return ToolResult(tool_use_id="", content=f"No matches found for pattern: {args.pattern}")

            header = f"Found {match_count} match(es) in {file_count} file(s):\n"
            return ToolResult(tool_use_id="", content=self.truncate_result(header + "\n".join(results)))

        except re.error as e:
            return ToolResult(tool_use_id="", content=f"Invalid regex pattern: {e}", is_error=True)
        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error in grep search: {e}", is_error=True)


_GREP_SHOWN_MATCHES = 100


def _searched_files(search_path: str, cwd: str, include: str) -> Iterator[str]:
    """Paths relative to *cwd* of the files under *search_path*, hidden directories skipped."""
    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not include or fnmatch.fnmatch(name, include):
                yield os.path.relpath(os.path.join(root, name), cwd)


def _matching_lines(rel_path: str, cwd: str, regex: re.Pattern[str]) -> Iterator[tuple[int, str]]:
    """Line numbers and lines of *rel_path* that match; an unreadable file has none.

    The file is opened through the O_NOFOLLOW walk so a symlink planted inside cwd
    cannot make the reader follow it out of the sandbox: a symlinked component
    raises ELOOP and the file is skipped, exactly as an unreadable file is.
    """
    try:
        fd = safe_open_fd(rel_path, cwd, os.O_RDONLY)
    except OSError:
        return
    try:
        with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, 1):
                if regex.search(line):
                    yield line_num, line.rstrip()
    except OSError:
        return


def _grep(search_path: str, cwd: str, regex: re.Pattern[str], include: str) -> tuple[int, int, list[str]]:
    """Total matches, files with a match, and the first matches formatted as ``path:line: text``."""
    match_count = 0
    files_with_matches: set[str] = set()
    results: list[str] = []
    for rel_path in _searched_files(search_path, cwd, include):
        for line_num, line in _matching_lines(rel_path, cwd, regex):
            match_count += 1
            files_with_matches.add(rel_path)
            if match_count <= _GREP_SHOWN_MATCHES:
                results.append(f"{rel_path}:{line_num}: {line}")
    return match_count, len(files_with_matches), results


def glob_regex(pattern: str) -> re.Pattern[str]:
    """*pattern* as a regex over ``/``-separated paths: ``**/`` is zero or more directories, ``*`` and ``?`` stay within one."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and (end := pattern.find("]", i + 1)) > i:
            body = pattern[i + 1:end]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _glob(search_path: str, cwd: str, pattern: str) -> list[str]:
    """Paths relative to *cwd* under *search_path* that match *pattern*; a pattern without ``/`` also matches a file name anywhere."""
    regex = glob_regex(pattern)
    by_name = "/" not in pattern
    found: list[str] = []
    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            relpath = os.path.relpath(os.path.join(root, name), cwd).replace(os.sep, "/")
            if regex.match(relpath) or (by_name and regex.match(name)):
                found.append(relpath)
    return found
