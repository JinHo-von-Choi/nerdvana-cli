"""File tools — Read, Write, Edit."""

from __future__ import annotations

import contextlib
import difflib
import errno
import hashlib
import os
import re
import stat
from typing import Any, ClassVar
from uuid import uuid4

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.tools import read_ledger
from nerdvana_cli.types import ToolResult
from nerdvana_cli.utils.path import safe_makedirs, safe_open_fd, validate_path

_O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW:  int = getattr(os, "O_NOFOLLOW", 0)

_HASH_LEN:         int = 6
_RELOCATE_WINDOW:  int = 20
_CONTEXT_RADIUS:   int = 5
_ANCHOR_RE = re.compile(rf"^(\d+)#([0-9a-f]{{{_HASH_LEN}}})$")


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


def _line_hash(line: str) -> str:
    """Return the first 6 hex chars of sha256 over the line without its newline."""
    return hashlib.sha256(line.rstrip("\n").encode()).hexdigest()[:_HASH_LEN]


def _parse_anchor(anchor: str) -> tuple[int, str] | None:
    """Split an ``N#hhhhhh`` anchor into (line number, hash); None when malformed."""
    match = _ANCHOR_RE.match(anchor.strip())
    if match is None:
        return None
    return int(match.group(1)), match.group(2)


def _resolve_anchor(lineno: int, line_hash: str, lines: list[str]) -> int | None:
    """Return the 0-based index of the line an anchor names, or None.

    The hash at ``lineno`` wins outright.  Otherwise the lines within
    ``_RELOCATE_WINDOW`` of it are searched for the hash, and a relocation is
    accepted only when exactly one of them carries it.  No similarity matching
    is attempted.
    """
    idx = lineno - 1
    if 0 <= idx < len(lines) and _line_hash(lines[idx]) == line_hash:
        return idx
    low        = max(0, idx - _RELOCATE_WINDOW)
    high       = min(len(lines) - 1, idx + _RELOCATE_WINDOW)
    candidates = [i for i in range(low, high + 1) if _line_hash(lines[i]) == line_hash]
    return candidates[0] if len(candidates) == 1 else None


def _format_with_hashes(lines: list[str], start_lineno: int = 1) -> str:
    """Format lines as ``N#hhhhhh    content`` so each line carries its own anchor."""
    return "\n".join(
        f"{i}#{_line_hash(line)}    {line.rstrip(chr(10))}"
        for i, line in enumerate(lines, start=start_lineno)
    )


def _lines_around(lines: list[str], lineno: int) -> str:
    """Return the current ``N#hhhhhh`` lines surrounding ``lineno``."""
    if not lines:
        return "(file is empty)"
    centre = min(max(lineno, 1), len(lines))
    first  = max(1, centre - _CONTEXT_RADIUS)
    last   = min(len(lines), centre + _CONTEXT_RADIUS)
    return _format_with_hashes(lines[first - 1:last], start_lineno=first)


def _read_bytes(relative_path: str, cwd: str) -> bytes:
    """Read the whole file through the symlink-safe descriptor walk."""
    fd = safe_open_fd(relative_path, cwd, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


_BINARY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff",       "JPEG image"),
    (b"GIF87a",             "GIF image"),
    (b"GIF89a",             "GIF image"),
    (b"%PDF-",              "PDF document"),
    (b"PK\x03\x04",         "ZIP archive"),
    (b"\x1f\x8b",           "gzip data"),
    (b"\x7fELF",            "ELF executable"),
)


def _binary_kind(data: bytes) -> str | None:
    """Name the kind of binary *data* is, or None when it reads as text."""
    for magic, label in _BINARY_MAGIC:
        if data.startswith(magic):
            return label
    if b"\x00" in data[:8192]:
        return "binary data"
    return None


def _decode_text(data: bytes, errors: str) -> str:
    """Decode UTF-8 bytes and fold CRLF/CR into LF, as text-mode reads do."""
    return data.decode("utf-8", errors=errors).replace("\r\n", "\n").replace("\r", "\n")


# Longest diff shown in a confirmation, in lines.
_MAX_PREVIEW_LINES = 120


def _current_text(path: str, cwd: str) -> str | None:
    """The text a path holds now ("" for a new file), or None when it cannot be read as text."""
    if validate_path(path, cwd):
        return None
    try:
        return _decode_text(_read_bytes(path, cwd), "replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return None


def unified_preview(path: str, before: str, after: str) -> str | None:
    """A unified diff of *before* to *after*, shortened to a readable length; None when nothing changes."""
    diff = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile = f"a/{path}" if before else "/dev/null",
        tofile   = f"b/{path}",
    ))
    if not diff:
        return None
    shown = [line if line.endswith("\n") else line + "\n" for line in diff[:_MAX_PREVIEW_LINES]]
    if len(diff) > _MAX_PREVIEW_LINES:
        shown.append(f"... {len(diff) - _MAX_PREVIEW_LINES} more diff line(s) not shown\n")
    return "".join(shown).rstrip("\n")


def edit_result_text(content: str, args: Any) -> str | None:
    """What *content* becomes under a FileEdit *args*, or None when the edit would be refused."""
    if args.anchor_hash is not None:
        parsed = _parse_anchor(args.anchor_hash)
        if parsed is None:
            return None
        lines = content.splitlines(keepends=True)
        index = _resolve_anchor(parsed[0], parsed[1], lines)
        if index is None:
            return None
        lines[index] = args.new_string
        return "".join(lines)
    old = args.old_string
    if not old or old not in content:
        return None
    if args.replace_all:
        return content.replace(old, args.new_string)
    return None if content.count(old) > 1 else content.replace(old, args.new_string, 1)


def _ledger_error(
    verb:     str,
    path:     str,
    session:  str,
    key:      str,
    digest:   str,
) -> str | None:
    """Return an error text unless the file is unchanged since it was read."""
    recorded = read_ledger.lookup(session, key)
    if recorded is None:
        return f"{path} has not been read in this session. Call FileRead on it before {verb} (or find_symbol with include_body for the symbol you are changing)."
    if recorded != digest:
        return f"{path} changed since it was last read. Call FileRead again before {verb}."
    return None


def _range_authority(session: str, key: str, anchor: str, raw_lines: list[str]) -> tuple[int, int] | None:
    """The shown range an anchor edit may rely on instead of a read of the whole file.

    A symbol read shows lines ``start``..``end``; the edit is allowed when the anchored line is inside such a
    range and those lines are exactly as they were shown.
    """
    parsed = _parse_anchor(anchor)
    if parsed is None:
        return None
    lineno = parsed[0]
    for start, end, digest in read_ledger.lookup_ranges(session, key):
        if start <= lineno <= end <= len(raw_lines) and read_ledger.range_digest(raw_lines[start - 1:end]) == digest:
            return start, end
    return None


def _keep_shown_range(session: str, key: str, shown: tuple[int, int], new_content: str, old_line_count: int) -> None:
    """After an edit made on the strength of a shown range, keep only that range, resized.

    The whole file was not read, so no whole-file digest is recorded; the other ranges are dropped because
    the edit may have moved their lines.
    """
    lines = new_content.splitlines(keepends=True)
    start, end = shown[0], shown[1] + len(lines) - old_line_count
    read_ledger.drop_ranges(session, key)
    if 1 <= start <= end <= len(lines):
        read_ledger.record_range(session, key, start, end, read_ledger.range_digest(lines[start - 1:end]))


def _record_written(relative_path: str, cwd: str, session: str, key: str) -> None:
    """Record the on-disk digest of a file this session just wrote."""
    read_ledger.record(session, key, read_ledger.content_digest(_read_bytes(relative_path, cwd)))


class FileReadArgs:
    def __init__(self, path: str, offset: int = 0, limit: int = 0):
        self.path = path
        self.offset = offset
        self.limit = limit


class FileReadTool(BaseTool[FileReadArgs]):
    name = "FileRead"
    description_text = """Read the contents of a file.

Reads text files. Binary files (images, PDFs, archives, executables) are
reported by type and size without their contents.
Use offset/limit to read specific portions of large files.
Every line is prefixed with an anchor `N#hhhhhh` (line number, `#`, 6 hex chars
of the line hash). Pass that anchor to FileEdit as anchor_hash.
A file must be read in this session before FileEdit or an overwriting
FileWrite will touch it; reading again refreshes that record.

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
                data = _read_bytes(args.path, context.cwd)
            except OSError as exc:
                if _is_symlink_block_error(exc):
                    return ToolResult(
                        tool_use_id="",
                        content=f"Symbolic link blocked: {args.path}",
                        is_error=True,
                    )
                raise
            read_ledger.record(
                read_ledger.session_key(context),
                read_ledger.resolve_key(args.path, context.cwd),
                read_ledger.content_digest(data),
            )
            binary = _binary_kind(data)
            if binary is not None:
                return ToolResult(
                    tool_use_id = "",
                    content     = (
                        f"[File: {args.path}] {binary}, {len(data)} bytes. FileRead shows text only, so the "
                        "contents are not displayed. Use Bash (file, xxd, pdftotext) to inspect it."
                    ),
                )
            full_text = _decode_text(data, "replace")
            if args.offset == 0 and args.limit == 0:
                content = full_text
            else:
                lines   = full_text.splitlines(keepends=True)
                start   = args.offset
                end     = start + args.limit if args.limit > 0 else len(lines)
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
the contents if it does. Replacing an existing file requires that it was
read with FileRead in this session and has not changed since; creating a new
file needs no prior read. For partial edits, use FileEdit instead.

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

    def preview_change(self, args: FileWriteArgs, context: ToolContext) -> str | None:
        """The diff of replacing the file with ``content`` (or the whole text of a new file)."""
        before = _current_text(args.path, context.cwd)
        if before is None:
            return None
        return unified_preview(args.path, before, _decode_text(args.content.encode("utf-8"), "replace"))

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
            session = read_ledger.session_key(context)
            key     = read_ledger.resolve_key(args.path, context.cwd)
            parent_rel = os.path.dirname(args.path)
            try:
                try:
                    current: bytes | None = _read_bytes(args.path, context.cwd)
                except FileNotFoundError:
                    current = None
                if current is not None:
                    stale = _ledger_error(
                        "overwriting it", args.path, session, key, read_ledger.content_digest(current),
                    )
                    if stale:
                        return ToolResult(tool_use_id="", content=stale, is_error=True)
                if parent_rel:
                    safe_makedirs(parent_rel, context.cwd)
                _atomic_write(args.path, context.cwd, args.content)
                _record_written(args.path, context.cwd, session, key)
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
Alternatively set anchor_hash to an `N#hhhhhh` anchor from FileRead output
(line number, `#`, 6 hex chars of the line hash): that whole line, including
its trailing newline, is replaced by new_string. If lines shifted by your own
earlier edit, the anchor is relocated when exactly one line within 20 lines
carries the hash; otherwise the edit is rejected and the current anchors near
that line are returned.
The file must have been read with FileRead in this session and not changed
since; otherwise the edit is rejected and you must FileRead it again.
For creating new files or full replacements, use FileWrite.

Examples:
- path: "src/main.py", old_string: "def old():", new_string: "def new():"
- path: "config.py", old_string: "DEBUG = False", new_string: "DEBUG = True", replace_all: false
- path: "src/main.py", anchor_hash: "12#a1b2c3", new_string: "    return 42\n"

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
                "description": "Line anchor 'N#hhhhhh' copied from FileRead output (line number and 6-char hash)",
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

    def preview_change(self, args: FileEditArgs, context: ToolContext) -> str | None:
        """The diff this edit would make, computed without writing anything."""
        before = _current_text(args.path, context.cwd)
        if not before:
            return None
        after = edit_result_text(before, args)
        return None if after is None else unified_preview(args.path, before, after)

    def validate_input(self, args: FileEditArgs, context: ToolContext) -> str | None:
        if args.anchor_hash is None and not args.old_string:
            return "Provide either anchor_hash (from FileRead output) or old_string"
        if args.anchor_hash is None and args.old_string == args.new_string:
            return "old_string and new_string are identical — no change would be made"
        if args.anchor_hash is None and args.old_string and not args.old_string.strip():
            return "old_string cannot be empty or whitespace-only"
        return None

    def _replace_anchor_line(
        self,
        args:    FileEditArgs,
        context: ToolContext,
        content: str,
        session: str,
        key:     str,
        shown:   tuple[int, int] | None,
    ) -> ToolResult:
        """Replace the line an anchor names; *shown* is the symbol range the edit relies on instead of a whole-file read."""
        raw_lines = content.splitlines(keepends=True)
        parsed    = _parse_anchor(args.anchor_hash or "")
        if parsed is None:
            return ToolResult(
                tool_use_id="",
                content=(
                    f"Malformed anchor '{args.anchor_hash}'. Use the 'N#hhhhhh' form "
                    "shown by FileRead (line number, '#', 6 hex chars)."
                ),
                is_error=True,
            )
        lineno, line_hash = parsed
        target_idx        = _resolve_anchor(lineno, line_hash, raw_lines)
        if target_idx is None:
            return ToolResult(
                tool_use_id="",
                content=(
                    f"Anchor '{args.anchor_hash}' does not identify a unique line in "
                    f"{args.path}. Current lines around {lineno}:\n"
                    f"{_lines_around(raw_lines, lineno)}"
                ),
                is_error=True,
            )
        line_count            = len(raw_lines)
        raw_lines[target_idx] = args.new_string
        new_content = "".join(raw_lines)
        try:
            _atomic_write(args.path, context.cwd, new_content)
            if shown is None:
                _record_written(args.path, context.cwd, session, key)
            else:
                _keep_shown_range(session, key, shown, new_content, line_count)
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

            if args.anchor_hash is None and not args.old_string:
                return ToolResult(
                    tool_use_id="",
                    content="Provide either anchor_hash (from FileRead output) or old_string",
                    is_error=True,
                )

            try:
                data = _read_bytes(args.path, context.cwd)
            except OSError as exc:
                if _is_symlink_block_error(exc):
                    return ToolResult(
                        tool_use_id="",
                        content=f"Symbolic link blocked: {args.path}",
                        is_error=True,
                    )
                raise

            session = read_ledger.session_key(context)
            key     = read_ledger.resolve_key(args.path, context.cwd)
            stale   = _ledger_error("editing", args.path, session, key, read_ledger.content_digest(data))
            content = _decode_text(data, "strict")
            shown   = None
            if stale and args.anchor_hash is not None:
                shown = _range_authority(session, key, args.anchor_hash, content.splitlines(keepends=True))
            if stale and shown is None:
                return ToolResult(tool_use_id="", content=stale, is_error=True)

            if args.anchor_hash is not None:
                return self._replace_anchor_line(args, context, content, session, key, shown)
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
                _record_written(args.path, context.cwd, session, key)
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
