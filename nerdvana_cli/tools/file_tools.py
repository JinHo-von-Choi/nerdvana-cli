"""File tools — Read, Write, Edit."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
from typing import Any, ClassVar
from uuid import uuid4

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult
from nerdvana_cli.utils.path import safe_makedirs, safe_open_fd, validate_path

_O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW:  int = getattr(os, "O_NOFOLLOW", 0)


def _is_symlink_block_error(exc: OSError) -> bool:
    """Return True when an OSError indicates a blocked symlink traversal."""
    return exc.errno in (errno.ELOOP, errno.EMLINK)


def _open_parent_dir_fd(relative_path: str, cwd: str) -> int:
    """Open the directory holding ``relative_path`` and return its descriptor.

    The walk goes through :func:`safe_open_fd`, so every component is opened
    with ``O_NOFOLLOW`` and a symlinked directory raises ``OSError(ELOOP)``.
    A path with no directory part resolves to ``cwd`` itself.

    The caller owns the descriptor and must close it.
    """
    parent_rel = os.path.dirname(relative_path)
    if not parent_rel:
        return os.open(cwd, os.O_RDONLY | _O_DIRECTORY)
    return safe_open_fd(parent_rel, cwd, os.O_RDONLY | _O_DIRECTORY)


def _target_stat(base: str, dir_fd: int, relative_path: str) -> os.stat_result | None:
    """Return ``lstat`` of ``base`` inside ``dir_fd``, or None when absent.

    Raises ``OSError(ELOOP)`` when the name is a symbolic link, so that a
    rename can never take the place of a link that points elsewhere.
    """
    try:
        info = os.lstat(base, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        raise OSError(errno.ELOOP, "Symbolic link blocked", relative_path)
    return info


def _create_temp_sibling(base: str, dir_fd: int) -> tuple[int, str]:
    """Create an exclusive temporary file beside ``base`` inside ``dir_fd``."""
    name = f".{base}.{uuid4().hex}.tmp"
    fd   = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
        0o644,
        dir_fd=dir_fd,
    )
    return fd, name


def _atomic_write(relative_path: str, cwd: str, content: str) -> None:
    """Replace ``relative_path`` with ``content`` in a single rename.

    The payload lands in a temporary file created in the destination
    directory, is flushed to stable storage, and only then takes the place of
    the target through ``os.replace``.  An interrupted or failed write
    therefore leaves the previous file whole instead of truncated.

    Containment is preserved: the directory chain is walked with
    ``O_NOFOLLOW``, the rename is issued against that directory descriptor so
    no component is resolved a second time, and a symlink sitting at the
    target name is rejected rather than replaced.  Permission bits of an
    existing file are carried over to the replacement.
    """
    base     = os.path.basename(relative_path)
    dir_fd   = _open_parent_dir_fd(relative_path, cwd)
    tmp_name : str | None = None
    try:
        existing = _target_stat(base, dir_fd, relative_path)
        fd, tmp_name = _create_temp_sibling(base, dir_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if existing is not None:
                os.fchmod(fh.fileno(), stat.S_IMODE(existing.st_mode))
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, base, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
    finally:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=dir_fd)
        os.close(dir_fd)


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
    is_concurrency_safe    = True
    args_class             = FileReadArgs
    category               = ToolCategory.READ
    side_effects           = ToolSideEffect.FILESYSTEM
    tags: ClassVar[frozenset[str]] = frozenset({"file"})
    requires_confirmation  = False

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
                # Directory listing path: no file open, so O_NOFOLLOW does
                # not apply. validate_path() above already enforced
                # containment for the listing case.
                entries = sorted(os.listdir(full_path))
                return ToolResult(tool_use_id="", content="Directory listing:\n" + "\n".join(entries))

            try:
                fd = safe_open_fd(args.path, context.cwd, os.O_RDONLY)
            except OSError as exc:
                if _is_symlink_block_error(exc):
                    return ToolResult(
                        tool_use_id="",
                        content=f"Symbolic link blocked: {args.path}",
                        is_error=True,
                    )
                raise
            with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
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
    is_concurrency_safe    = False
    is_destructive         = True
    args_class             = FileWriteArgs
    category               = ToolCategory.WRITE
    side_effects           = ToolSideEffect.FILESYSTEM
    tags: ClassVar[frozenset[str]] = frozenset({"file"})
    requires_confirmation  = False

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
            parent_rel = os.path.dirname(args.path)
            try:
                if parent_rel:
                    safe_makedirs(parent_rel, context.cwd)
                _atomic_write(args.path, context.cwd, args.content)
            except OSError as exc:
                if _is_symlink_block_error(exc):
                    return ToolResult(
                        tool_use_id="",
                        content=f"Symbolic link blocked: {args.path}",
                        is_error=True,
                    )
                raise

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
    is_concurrency_safe    = False
    is_destructive         = False
    args_class             = FileEditArgs
    category               = ToolCategory.WRITE
    side_effects           = ToolSideEffect.FILESYSTEM
    tags: ClassVar[frozenset[str]] = frozenset({"file", "edit"})
    requires_confirmation  = False

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

            try:
                read_fd = safe_open_fd(args.path, context.cwd, os.O_RDONLY)
            except OSError as exc:
                if _is_symlink_block_error(exc):
                    return ToolResult(
                        tool_use_id="",
                        content=f"Symbolic link blocked: {args.path}",
                        is_error=True,
                    )
                raise
            with os.fdopen(read_fd, encoding="utf-8") as f:
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
                try:
                    _atomic_write(args.path, context.cwd, new_content)
                except OSError as exc:
                    if _is_symlink_block_error(exc):
                        return ToolResult(
                            tool_use_id="",
                            content=f"Symbolic link blocked: {args.path}",
                            is_error=True,
                        )
                    raise
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

            try:
                _atomic_write(args.path, context.cwd, new_content)
            except OSError as exc:
                if _is_symlink_block_error(exc):
                    return ToolResult(
                        tool_use_id="",
                        content=f"Symbolic link blocked: {args.path}",
                        is_error=True,
                    )
                raise

            context.file_state[args.path] = new_content
            return ToolResult(tool_use_id="", content=f"Replaced {count} occurrence(s) in {args.path}")

        except Exception as e:
            return ToolResult(tool_use_id="", content=f"Error editing file: {e}", is_error=True)
