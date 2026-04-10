"""File tools — Read, Write, Edit."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult
from nerdvana_cli.utils.path import validate_path


def _hash4(line: str) -> str:
    """Return first 4 hex chars of sha256(line)."""
    return hashlib.sha256(line.encode()).hexdigest()[:4]


def _resolve_anchor(anchor: str, lines: list[str]) -> int | None:
    """Return 0-based line index for anchor (hash[:4] or hash[:4]#N).

    Returns None if no matching line is found.
    """
    if "#" in anchor:
        base_hash, _, nth_str = anchor.partition("#")
        target_n = int(nth_str)
    else:
        base_hash = anchor
        target_n  = 1

    seen = 0
    for idx, line in enumerate(lines):
        if _hash4(line) == base_hash:
            seen += 1
            if seen == target_n:
                return idx
    return None


def _format_with_hashes(lines: list[str], start_lineno: int = 1) -> str:
    """Format lines with hash anchors: 'N:xxxx    content'.

    Duplicate lines (same hash) are disambiguated with #N suffix.
    """
    hash_counts: dict[str, int] = {}
    hash_seen:   dict[str, int] = {}
    # first pass: count occurrences per hash
    for line in lines:
        h = _hash4(line)
        hash_counts[h] = hash_counts.get(h, 0) + 1

    result_parts: list[str] = []
    for i, line in enumerate(lines, start=start_lineno):
        h = _hash4(line)
        if hash_counts[h] > 1:
            hash_seen[h] = hash_seen.get(h, 0) + 1
            anchor = f"{h}#{hash_seen[h]}"
        else:
            anchor = h
        # strip trailing newline for display; content itself keeps it
        display = line.rstrip("\n")
        result_parts.append(f"{i}:{anchor}    {display}")

    return "\n".join(result_parts)


class FileReadArgs:
    def __init__(self, path: str, offset: int = 0, limit: int = 0):
        self.path = path
        self.offset = offset
        self.limit = limit


class FileReadTool(BaseTool[FileReadArgs]):
    name = "FileRead"
    description_text = """Read the contents of a file.

Supports text files, PDFs, and images.
Use offset/limit to read specific portions of large files.
Line numbers are included in the output.

Examples:
- path: "src/main.py"
- path: "README.md", offset: 100, limit: 50"""
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
            "offset": {"type": "integer", "description": "Starting line number (0-based, default: 0)", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to read (0 = all, default: 0)", "default": 0},
        },
        "required": ["path"],
    }
    is_concurrency_safe = True
    is_read_only = True
    args_class = FileReadArgs

    async def call(
        self,
        args: FileReadArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = validate_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)
            full_path = os.path.join(context.cwd, args.path)
            if not os.path.exists(full_path):
                return ToolResult(tool_use_id="", content=f"File not found: {args.path}", is_error=True)
            if os.path.isdir(full_path):
                entries = sorted(os.listdir(full_path))
                return ToolResult(tool_use_id="", content="Directory listing:\n" + "\n".join(entries))

            with open(full_path, encoding="utf-8", errors="replace") as f:
                if args.offset == 0 and args.limit == 0:
                    content = f.read()
                else:
                    lines = f.readlines()
                    start = args.offset
                    end = start + args.limit if args.limit > 0 else len(lines)
                    content = "".join(lines[start:end])

            context.file_state[args.path] = content
            raw_lines = content.splitlines(keepends=True)
            start_no  = args.offset + 1 if args.offset else 1
            hashed    = _format_with_hashes(raw_lines, start_lineno=start_no)
            total_lines = len(raw_lines)
            header = f"[File: {args.path}] ({total_lines} lines)\n"
            return ToolResult(tool_use_id="", content=self.truncate_result(header + hashed))

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error reading file: {e}", is_error=True)


class FileWriteArgs:
    def __init__(self, path: str, content: str):
        self.path = path
        self.content = content


class FileWriteTool(BaseTool[FileWriteArgs]):
    name = "FileWrite"
    description_text = """Create or overwrite a file with the given content.

This will create the file if it doesn't exist, or completely replace
the contents if it does. For partial edits, use FileEdit instead.

Examples:
- path: "src/new_module.py", content: "def hello(): ..."
- path: "docs/README.md", content: "# Documentation\n\n..."

WARNING: This replaces the entire file content."""
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "The content to write to the file"},
        },
        "required": ["path", "content"],
    }
    is_concurrency_safe = False
    is_read_only = False
    is_destructive = True
    args_class = FileWriteArgs

    async def call(
        self,
        args: FileWriteArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = validate_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)
            full_path = os.path.join(context.cwd, args.path)
            parent = os.path.dirname(full_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(args.content)

            context.file_state[args.path] = args.content
            return ToolResult(tool_use_id="", content=f"Successfully wrote {args.path} ({len(args.content)} chars)")

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error writing file: {e}", is_error=True)


class FileEditArgs:
    def __init__(
        self,
        path:        str,
        new_string:  str,
        old_string:  str | None  = None,
        anchor_hash: str | None  = None,
        replace_all: bool        = False,
    ):
        self.path        = path
        self.old_string  = old_string
        self.new_string  = new_string
        self.anchor_hash = anchor_hash
        self.replace_all = replace_all


class FileEditTool(BaseTool[FileEditArgs]):
    name = "FileEdit"
    description_text = """Perform a string replacement in a file.

Finds old_string and replaces it with new_string.
Use replace_all to replace all occurrences.
For creating new files or full replacements, use FileWrite.

Examples:
- path: "src/main.py", old_string: "def old():", new_string: "def new():"
- path: "config.py", old_string: "DEBUG = False", new_string: "DEBUG = True", replace_all: false

IMPORTANT: old_string must match exactly (including whitespace)."""
    input_schema = {
        "type": "object",
        "properties": {
            "path":        {"type": "string", "description": "Path to the file to edit"},
            "new_string":  {"type": "string", "description": "The replacement content"},
            "old_string":  {
                "type": ["string", "null"],
                "description": "Exact string to find (used when anchor_hash is absent)",
                "default": None,
            },
            "anchor_hash": {
                "type": ["string", "null"],
                "description": "4-char (optionally #N) hash anchor from FileRead output",
                "default": None,
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (ignored when anchor_hash is set)",
                "default": False,
            },
        },
        "required": ["path", "new_string"],
    }
    is_concurrency_safe = False
    is_read_only = False
    is_destructive = False
    args_class = FileEditArgs

    def validate_input(self, args: FileEditArgs, context: ToolContext) -> str | None:
        if args.anchor_hash is None and not args.old_string:
            return "Provide either anchor_hash (from FileRead output) or old_string"
        if args.anchor_hash is None and args.old_string == args.new_string:
            return "old_string and new_string are identical — no change would be made"
        if args.anchor_hash is None and args.old_string and not args.old_string.strip():
            return "old_string cannot be empty or whitespace-only"
        return None

    async def call(
        self,
        args: FileEditArgs,
        context: ToolContext,
        can_use_tool: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        try:
            path_error = validate_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)
            full_path = os.path.join(context.cwd, args.path)
            if not os.path.exists(full_path):
                return ToolResult(tool_use_id="", content=f"File not found: {args.path}", is_error=True)

            with open(full_path, encoding="utf-8") as f:
                content = f.read()

            if args.anchor_hash is None and not args.old_string:
                return ToolResult(
                    tool_use_id="",
                    content="Provide either anchor_hash (from FileRead output) or old_string",
                    is_error=True,
                )

            # ── anchor_hash path ──────────────────────────────────────────
            if args.anchor_hash is not None:
                raw_lines = content.splitlines(keepends=True)
                target_idx = _resolve_anchor(args.anchor_hash, raw_lines)
                if target_idx is None:
                    return ToolResult(
                        tool_use_id="",
                        content=(
                            f"Anchor '{args.anchor_hash}' not found in {args.path}. "
                            "File changed since last read. Re-read the file first."
                        ),
                        is_error=True,
                    )
                raw_lines[target_idx] = args.new_string
                new_content = "".join(raw_lines)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                context.file_state[args.path] = new_content
                return ToolResult(
                    tool_use_id="",
                    content=f"Replaced anchor line {target_idx + 1} in {args.path}",
                )
            # ── old_string path (backward-compatible) ─────────────────────
            assert args.old_string is not None
            if args.old_string not in content:
                return ToolResult(
                    tool_use_id="",
                    content=f"String not found in {args.path}. The old_string must match exactly.",
                    is_error=True,
                )

            if args.replace_all:
                new_content = content.replace(args.old_string, args.new_string)
                count = content.count(args.old_string)
            else:
                new_content = content.replace(args.old_string, args.new_string, 1)
                count = 1

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            context.file_state[args.path] = new_content
            return ToolResult(tool_use_id="", content=f"Replaced {count} occurrence(s) in {args.path}")

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error editing file: {e}", is_error=True)
