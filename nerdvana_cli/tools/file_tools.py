"""File tools — Read, Write, Edit."""

from __future__ import annotations

import os
from typing import Any

from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult


def _validate_path(path: str, cwd: str) -> str | None:
    """Validate that resolved path stays within cwd. Returns error message or None."""
    if os.path.isabs(path):
        return f"Absolute paths are not allowed: {path}"
    resolved     = os.path.realpath(os.path.join(cwd, path))
    cwd_resolved = os.path.realpath(cwd)
    if not resolved.startswith(cwd_resolved + os.sep) and resolved != cwd_resolved:
        return f"Path traversal blocked: {path} resolves outside working directory"
    return None


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
            path_error = _validate_path(args.path, context.cwd)
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
            total_lines = content.count("\n") + 1
            header = f"[File: {args.path}] ({total_lines} lines)\n"
            return ToolResult(tool_use_id="", content=self.truncate_result(header + content))

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
            path_error = _validate_path(args.path, context.cwd)
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
    def __init__(self, path: str, old_string: str, new_string: str, replace_all: bool = False):
        self.path = path
        self.old_string = old_string
        self.new_string = new_string
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
            "path": {"type": "string", "description": "Path to the file to edit"},
            "old_string": {"type": "string", "description": "The exact string to find and replace"},
            "new_string": {"type": "string", "description": "The string to replace with"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default: false)",
                "default": False,
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    is_concurrency_safe = False
    is_read_only = False
    is_destructive = False
    args_class = FileEditArgs

    def validate_input(self, args: FileEditArgs, context: ToolContext) -> str | None:
        if args.old_string == args.new_string:
            return "old_string and new_string are identical — no change would be made"
        if not args.old_string.strip():
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
            path_error = _validate_path(args.path, context.cwd)
            if path_error:
                return ToolResult(tool_use_id="", content=path_error, is_error=True)
            full_path = os.path.join(context.cwd, args.path)
            if not os.path.exists(full_path):
                return ToolResult(tool_use_id="", content=f"File not found: {args.path}", is_error=True)

            with open(full_path, encoding="utf-8") as f:
                content = f.read()

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
