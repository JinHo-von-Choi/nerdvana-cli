"""Anthropic's client-side memory tool, backed by a per-project directory of the data root.

Author: 최진호
Date:   2026-10-03

The model sees a ``/memories`` directory and edits it with six commands (view, create, str_replace, insert,
delete, rename). The API declares the tool itself (``{"type": "memory_20250818", "name": "memory"}``), so the
model already knows the commands; this module answers them. The files live in ``paths.project_memory_tool_dir``,
not in the ``MemoriesManager`` store: that store keeps named ``.md`` entries in scopes with a restricted
character set and has no insert, no directories as such and no size listing, none of which the command set can
do without. Every path the model sends is checked to stay inside the root, and file, store and view sizes are
capped. Return strings follow the documentation page for the tool (platform.claude.com/docs).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import unquote

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.tools.memory_tools import _scan_secrets
from nerdvana_cli.types import ToolResult

ROOT_PATH       = "/memories"
MAX_PATH_CHARS  = 512
MAX_FILE_BYTES  = 100_000
MAX_TOTAL_BYTES = 5_000_000
MAX_FILES       = 500
MAX_VIEW_CHARS  = 16_000
_SNIPPET_LINES  = 4

_COMMANDS = ("view", "create", "str_replace", "insert", "delete", "rename")


class MemoryToolError(Exception):
    """A command the store refuses; the message is what the model is told."""


def _human(size: int) -> str:
    """*size* bytes as the listing shows it: ``12B``, ``5.5K``, ``1.2M``."""
    if size < 1024:
        return f"{size}B"
    value = size / 1024
    for unit in "KMG":
        if value < 1024 or unit == "G":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


def _traverses(path: str) -> bool:
    """True when *path*, as sent or after up to three rounds of percent-decoding, holds a parent step, a backslash or NUL."""
    candidate = path
    for _ in range(4):
        if "\\" in candidate or "\x00" in candidate or ".." in re.split(r"/+", candidate):
            return True
        decoded = unquote(candidate)
        if decoded == candidate:
            return False
        candidate = decoded
    return True


def _numbered(lines: list[str], first: int = 1) -> str:
    """Lines with six-wide right-aligned numbers and a tab, counting from *first*."""
    return "\n".join(f"{number:>6}\t{line}" for number, line in enumerate(lines, start=first))


def _line_range(raw: Any, count: int) -> tuple[int, int]:
    """The 1-indexed inclusive ``[start, end]`` of a ``view_range`` over *count* lines; end -1 means to the last line."""
    if not (isinstance(raw, list) and len(raw) == 2 and all(isinstance(n, int) and not isinstance(n, bool) for n in raw)):
        raise MemoryToolError("Error: `view_range` must be a list of two integers [start_line, end_line]")
    start, end = raw
    end = count if end == -1 else end
    if start < 1 or end < start or start > max(count, 1):
        raise MemoryToolError(f"Error: Invalid `view_range`: {raw}. It should be within the range of lines of the file: [1, {count}]")
    return start, min(end, count)


class MemoryStore:
    """The ``/memories`` directory of one project: path checks, size caps and the six commands."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    # ── paths ────────────────────────────────────────────────────────────

    def _locate(self, path: Any) -> Path:
        """The real path of the virtual *path*; refuses anything that is not inside ``/memories``."""
        if not isinstance(path, str) or not path:
            raise MemoryToolError("Error: a path is required")
        if len(path) > MAX_PATH_CHARS or _traverses(path) or not (path == ROOT_PATH or path.startswith(ROOT_PATH + "/")):
            raise MemoryToolError(f"Error: The path {path} is not a valid path inside {ROOT_PATH}")
        target = (self._root / path[len(ROOT_PATH):].lstrip("/")).resolve()
        if target != self._root and self._root not in target.parents:
            raise MemoryToolError(f"Error: The path {path} is not a valid path inside {ROOT_PATH}")
        return target

    def _file(self, path: Any, *, error: str = "The path {path} does not exist. Please provide a valid path.") -> Path:
        """The existing regular file at *path*."""
        target = self._locate(path)
        if not target.is_file():
            raise MemoryToolError(error.format(path=path))
        return target

    @staticmethod
    def _read(target: Path, path: str) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise MemoryToolError(f"Error: {path} is not a UTF-8 text file") from exc

    # ── caps ─────────────────────────────────────────────────────────────

    def _check_write(self, content: str, replacing: Path | None = None) -> None:
        """Refuse *content* that is too large, would overfill the store, or looks like a secret."""
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise MemoryToolError(f"Error: the file would be {size} bytes; the limit is {MAX_FILE_BYTES}")
        files = [p for p in self._root.rglob("*") if p.is_file()] if self._root.is_dir() else []
        total = sum(p.stat().st_size for p in files if p != replacing)
        if total + size > MAX_TOTAL_BYTES or (replacing is None and len(files) >= MAX_FILES):
            raise MemoryToolError(f"Error: the memory store is full ({MAX_TOTAL_BYTES} bytes or {MAX_FILES} files); delete files first")
        if found := _scan_secrets(content):
            raise MemoryToolError(f"Error: the content looks like it holds a secret ({', '.join(found)}); remove it before storing")

    # ── commands ─────────────────────────────────────────────────────────

    def execute(self, args: dict[str, Any]) -> str:
        """Run one memory command and return the text for the model; raises MemoryToolError for a refusal."""
        command = args.get("command")
        if command not in _COMMANDS:
            raise MemoryToolError(f"Error: unknown command {command}")
        self._root.mkdir(parents=True, exist_ok=True)
        try:
            return str(getattr(self, f"_{command}")(args))
        except KeyError as exc:
            raise MemoryToolError(f"Error: `{exc.args[0]}` is required for {command}") from exc
        except OSError as exc:
            raise MemoryToolError(f"Error: {command} failed: {exc.strerror or exc}") from exc

    def _view(self, args: dict[str, Any]) -> str:
        path   = args["path"]
        target = self._locate(path)
        if target.is_dir():
            return self._listing(path, target)
        if not target.is_file():
            raise MemoryToolError(f"The path {path} does not exist. Please provide a valid path.")
        lines = self._read(target, path).splitlines()
        first = 1
        if args.get("view_range") is not None:
            start, end = _line_range(args["view_range"], len(lines))
            lines, first = lines[start - 1:end], start
        body = f"Here's the content of {path} with line numbers:\n{_numbered(lines, first)}"
        if len(body) > MAX_VIEW_CHARS:
            body = body[:MAX_VIEW_CHARS] + f"\n... [output cut at {MAX_VIEW_CHARS} characters; read the rest with view_range]"
        return body

    def _listing(self, path: str, directory: Path) -> str:
        """Files and directories up to two levels below *directory*, hidden items and node_modules left out."""
        rows = [f"{_human(self._size(directory))}\t{path}"]
        for entry in sorted(directory.rglob("*")):
            parts = entry.relative_to(directory).parts
            if len(parts) <= 2 and not any(part.startswith(".") or part == "node_modules" for part in parts):
                rows.append(f"{_human(self._size(entry))}\t{path.rstrip('/')}/{'/'.join(parts)}")
        return f"Here're the files and directories up to 2 levels deep in {path}, excluding hidden items and node_modules:\n" + "\n".join(rows)

    @staticmethod
    def _size(entry: Path) -> int:
        return entry.stat().st_size if entry.is_file() else sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())

    def _create(self, args: dict[str, Any]) -> str:
        path, text = args["path"], args["file_text"]
        target = self._locate(path)
        if target == self._root or target.exists():
            raise MemoryToolError(f"Error: File {path} already exists")
        self._check_write(text)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return f"File created successfully at: {path}"

    def _str_replace(self, args: dict[str, Any]) -> str:
        path, old, new = args["path"], args["old_str"], args.get("new_str") or ""
        target = self._file(path, error="Error: The path {path} does not exist. Please provide a valid path.")
        text   = self._read(target, path)
        if not old or old not in text:
            raise MemoryToolError(f"No replacement was performed, old_str `{old}` did not appear verbatim in {path}.")
        if text.count(old) > 1:
            where = list(dict.fromkeys(str(text.count("\n", 0, m.start()) + 1) for m in re.finditer(re.escape(old), text)))
            raise MemoryToolError(f"No replacement was performed. Multiple occurrences of old_str `{old}` in lines: {', '.join(where)}. Please ensure it is unique")
        edited = text.replace(old, new, 1)
        self._check_write(edited, replacing=target)
        target.write_text(edited, encoding="utf-8")
        line = text.count("\n", 0, text.index(old))
        low  = max(0, line - _SNIPPET_LINES)
        high = line + new.count("\n") + _SNIPPET_LINES + 1
        return f"The memory file has been edited. Here's a snippet of {path}:\n{_numbered(edited.splitlines()[low:high], low + 1)}"

    def _insert(self, args: dict[str, Any]) -> str:
        path, at, new = args["path"], args["insert_line"], args["insert_text"]
        target = self._file(path, error="Error: The path {path} does not exist")
        text   = self._read(target, path)
        lines  = text.splitlines()
        if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at <= len(lines):
            raise MemoryToolError(f"Error: Invalid `insert_line` parameter: {at}. It should be within the range of lines of the file: [0, {len(lines)}]")
        edited = "\n".join([*lines[:at], *new.splitlines(), *lines[at:]]) + ("\n" if text.endswith("\n") or not text else "")
        self._check_write(edited, replacing=target)
        target.write_text(edited, encoding="utf-8")
        return f"The file {path} has been edited."

    def _delete(self, args: dict[str, Any]) -> str:
        path   = args["path"]
        target = self._locate(path)
        if target == self._root:
            raise MemoryToolError(f"Error: The {ROOT_PATH} directory itself cannot be deleted")
        if not target.exists():
            raise MemoryToolError(f"Error: The path {path} does not exist")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return f"Successfully deleted {path}"

    def _rename(self, args: dict[str, Any]) -> str:
        old_path, new_path = args["old_path"], args["new_path"]
        old, new = self._locate(old_path), self._locate(new_path)
        if old == self._root:
            raise MemoryToolError(f"Error: The {ROOT_PATH} directory itself cannot be renamed")
        if not old.exists():
            raise MemoryToolError(f"Error: The path {old_path} does not exist")
        if new.exists():
            raise MemoryToolError(f"Error: The destination {new_path} already exists")
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return f"Successfully renamed {old_path} to {new_path}"


class AnthropicMemoryTool(BaseTool[dict[str, Any]]):
    """The ``memory`` tool: Claude's persistent notes for this project, kept outside the repository."""

    name             = "memory"
    description_text = (
        "Store and retrieve notes that persist across sessions, in a memory directory under /memories. "
        "Commands: view (a directory listing, or a file with line numbers and an optional view_range), create, "
        "str_replace, insert, delete, rename. Check the directory before starting a task and keep it organized."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command":     {"type": "string", "enum": list(_COMMANDS)},
            "path":        {"type": "string", "description": "File or directory under /memories (view, create, str_replace, insert, delete)"},
            "view_range":  {"type": "array", "items": {"type": "integer"}, "description": "view: [start_line, end_line], 1-indexed, end -1 for the end"},
            "file_text":   {"type": "string", "description": "create: the content of the new file"},
            "old_str":     {"type": "string", "description": "str_replace: the text to replace, which must occur once"},
            "new_str":     {"type": "string", "description": "str_replace: the replacement; omitted deletes old_str"},
            "insert_line": {"type": "integer", "description": "insert: the line after which to insert; 0 inserts at the start"},
            "insert_text": {"type": "string", "description": "insert: the text to insert"},
            "old_path":    {"type": "string", "description": "rename: the current path"},
            "new_path":    {"type": "string", "description": "rename: the new path"},
        },
        "required": ["command"],
    }
    # The API declares this tool itself on Claude models; other models get the function declaration above.
    anthropic_native:    ClassVar[dict[str, str]] = {"type": "memory_20250818", "name": "memory"}
    is_concurrency_safe                           = False
    category:            ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects:        ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM

    def __init__(self, root: Path) -> None:
        self._store = MemoryStore(root)

    async def call(
        self,
        args: dict[str, Any],
        context: ToolContext,
        can_use_tool: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        """Run the command; a refusal comes back as an error result the model can read."""
        try:
            return ToolResult(tool_use_id="", content=self._store.execute(args), is_error=False)
        except MemoryToolError as exc:
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)


def create_memory_tool(settings: Any) -> AnthropicMemoryTool | None:
    """The memory tool for *settings*, or None unless ``model.anthropic_memory_tool`` is on and the model is Anthropic's."""
    from nerdvana_cli.providers.base import ProviderName, detect_provider

    model = getattr(settings, "model", None)
    if model is None or getattr(model, "anthropic_memory_tool", False) is not True:
        return None
    provider = model.provider or detect_provider(model.model)
    if str(provider) != ProviderName.ANTHROPIC.value:
        return None
    return AnthropicMemoryTool(core_paths.project_memory_tool_dir(settings.cwd))
