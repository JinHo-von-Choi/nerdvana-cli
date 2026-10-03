"""How a NerdVana tool call is described to an ACP client: kind, title, file locations, diff and result content.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acp import text_block, tool_content, tool_diff_content
from acp.schema import PlanEntry, PlanEntryStatus, ToolCallLocation, ToolKind

from nerdvana_cli.core.safety.approvals import normalise
from nerdvana_cli.core.safety.edit_tools import EDIT_PATH_ATTRS
from nerdvana_cli.core.safety.policy import primary_argument

# Longest tool result and longest title sent to the editor; the model still gets the whole result.
MAX_RESULT_CHARS = 10_000
MAX_TITLE_CHARS  = 120

# Largest existing file read to show what a write replaces.
_MAX_OLD_BYTES = 1_000_000

_KINDS: dict[str, ToolKind] = {
    "FileRead":                  "read",
    "symbol_overview":           "read",
    "lsp_diagnostics":           "read",
    "FileWrite":                 "edit",
    "FileEdit":                  "edit",
    "replace_symbol_body":       "edit",
    "insert_before_symbol":      "edit",
    "insert_after_symbol":       "edit",
    "lsp_rename":                "edit",
    "safe_delete_symbol":        "delete",
    "Glob":                      "search",
    "Grep":                      "search",
    "find_symbol":               "search",
    "find_referencing_symbols":  "search",
    "lsp_find_references":       "search",
    "lsp_goto_definition":       "search",
    "ToolSearch":                "search",
    "WebSearch":                 "search",
    "Bash":                      "execute",
    "Parism":                    "execute",
    "WebFetch":                  "fetch",
}

_VERBS: dict[str, str] = {
    "FileRead":  "Read",
    "FileWrite": "Write",
    "FileEdit":  "Edit",
    "Bash":      "Run",
    "Glob":      "Glob",
    "Grep":      "Grep",
    "WebFetch":  "Fetch",
    "WebSearch": "Search",
}

# Kinds whose calls name a file the editor can jump to.
_FILE_KINDS = frozenset({"read", "edit", "delete"})

_SUBJECT_KEYS = ("command", "path", "relative_path", "file_path", "url", "query", "pattern")

_PLAN_STATUSES: dict[str, PlanEntryStatus] = {"pending": "pending", "in_progress": "in_progress", "completed": "completed"}


def tool_kind(name: str) -> ToolKind:
    """The ACP tool kind for *name*; ``other`` for a tool this table does not know (MCP tools among them)."""
    return _KINDS.get(name, "other")


def _absolute(path: str, cwd: str) -> str:
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


def tool_locations(name: str, tool_input: dict[str, Any], cwd: str) -> list[ToolCallLocation]:
    """The file a read, edit or delete call is about, as an absolute location; empty for every other call."""
    if tool_kind(name) not in _FILE_KINDS:
        return []
    for attr in EDIT_PATH_ATTRS:
        value = tool_input.get(attr)
        if isinstance(value, str) and value.strip():
            return [ToolCallLocation(path=_absolute(value.strip(), cwd))]
    return []


def _display(value: str, cwd: str) -> str:
    """*value* relative to *cwd* when it is an absolute path inside it."""
    if os.path.isabs(value) and Path(value).is_relative_to(cwd):
        return os.path.relpath(value, cwd)
    return value


def tool_title(name: str, tool_input: dict[str, Any], cwd: str) -> str:
    """A one-line description: a verb and the command, path, URL or pattern the call is about."""
    subject = next((str(v).strip() for key in _SUBJECT_KEYS if isinstance(v := tool_input.get(key), str) and v.strip()), "")
    title   = f"{_VERBS.get(name, name)} {_display(subject, cwd)}".strip()
    first = title.splitlines()[0] if title else name
    return first if len(first) <= MAX_TITLE_CHARS else first[: MAX_TITLE_CHARS - 3] + "..."


def _existing_text(path: str) -> str | None:
    """What *path* holds now, or None when it is missing, large or not text."""
    try:
        file = Path(path)
        if not file.is_file() or file.stat().st_size > _MAX_OLD_BYTES:
            return None
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def tool_diff(name: str, tool_input: dict[str, Any], cwd: str) -> list[Any]:
    """Diff content for an edit the call describes completely: a write, or an edit given as old and new text."""
    locations = tool_locations(name, tool_input, cwd)
    if not locations:
        return []
    path = locations[0].path
    if name == "FileWrite" and isinstance(tool_input.get("content"), str):
        return [tool_diff_content(path, tool_input["content"], _existing_text(path))]
    old, new = tool_input.get("old_string"), tool_input.get("new_string")
    if name == "FileEdit" and isinstance(old, str) and isinstance(new, str):
        return [tool_diff_content(path, new, old)]
    return []


def result_content(text: str) -> list[Any]:
    """Text content for a finished call, cut to ``MAX_RESULT_CHARS``."""
    if not text:
        return []
    shown = text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "\n[output cut]"
    return [tool_content(text_block(shown))]


def text_content(text: str) -> list[Any]:
    """Text content for a message that belongs to a call, such as the reason it needs approval."""
    return [tool_content(text_block(text))] if text else []


def plan_entries(tool_input: dict[str, Any]) -> list[PlanEntry]:
    """Plan entries for a TodoWrite call: its todos with their status."""
    entries: list[PlanEntry] = []
    for todo in tool_input.get("todos") or []:
        if not isinstance(todo, dict) or not str(todo.get("content", "")).strip():
            continue
        status = _PLAN_STATUSES.get(str(todo.get("status")), "pending")
        entries.append(PlanEntry(content=str(todo["content"]), priority="medium", status=status))
    return entries


def approval_key(name: str, tool_input: dict[str, Any]) -> tuple[str, str]:
    """What an always-allow or always-reject answer is remembered for: the tool and its main argument."""
    return name, normalise(primary_argument(name, tool_input))
