"""Activity state model and tool-call summarizer for the REPL indicator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

Phase = Literal["idle", "thinking", "tool_running", "streaming", "waiting_api"]

_BASH_LIMIT    = 60
_GLOB_LIMIT    = 60
_GREP_PAT_LIM  = 40
_GREP_PATH_LIM = 30
_PARISM_LIMIT  = 40
_QUERY_LIMIT   = 40


@dataclass
class ActivityState:
    phase:      Phase       = "idle"
    label:      str         = "Ready"
    detail:     str         = ""
    tool_name:  str         = ""
    started_at: float | None = None


def _trunc(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters, appending '...' when cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _short_path(path: str) -> str:
    """Return basename + one parent component, e.g. '../core/agent_loop.py'."""
    parts = path.replace("\\", "/").rsplit("/", 2)
    return "/".join(parts[-2:])


def _path_detail(inp: dict[str, Any]) -> str:
    path = inp.get("file_path", inp.get("path", ""))
    return _short_path(str(path)) if path else ""


def _grep_detail(inp: dict[str, Any]) -> str:
    pattern = _trunc(str(inp.get("pattern", "")), _GREP_PAT_LIM)
    path    = _trunc(str(inp.get("path", inp.get("directory", ""))), _GREP_PATH_LIM)
    return f"{pattern} in {path}" if path else pattern


def _parism_detail(inp: dict[str, Any]) -> str:
    cmd    = str(inp.get("cmd", ""))
    args   = inp.get("args", [])
    args_s = " ".join(str(a) for a in args) if args else ""
    return (f"{cmd} {_trunc(args_s, _PARISM_LIMIT)}" if args_s else cmd).strip()


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _lsp_detail(inp: dict[str, Any]) -> str:
    file_path = inp.get("file_path", "")
    symbol    = inp.get("symbol", "")
    detail    = _short_path(str(file_path)) if file_path else ""
    if symbol:
        detail = f"{detail}:{symbol}" if detail else str(symbol)
    return detail


def _symbol_detail(inp: dict[str, Any]) -> str:
    rel       = inp.get("relative_path", inp.get("within_relative_path", ""))
    sym       = inp.get("name_path", inp.get("symbol", ""))
    file_part = _short_path(str(rel)) if rel else ""
    sym_part  = str(sym) if sym else ""
    return f"{file_part}:{sym_part}" if file_part and sym_part else file_part or sym_part


def _external_detail(inp: dict[str, Any]) -> str:
    alias = inp.get("name", inp.get("alias", ""))
    return str(alias) if alias else ""


_DETAIL: dict[str, Callable[[dict[str, Any]], str]] = {
    "Bash":      lambda inp: _trunc(str(inp.get("command", "")), _BASH_LIMIT),
    "FileRead":  _path_detail,
    "FileWrite": _path_detail,
    "FileEdit":  _path_detail,
    "Glob":      lambda inp: _trunc(str(inp.get("pattern", "")), _GLOB_LIMIT),
    "Grep":      _grep_detail,
    "Parism":    _parism_detail,
    "Agent":     lambda inp: f"spawn: {inp.get('subagent_type', inp.get('agent_type', ''))}",
    "Swarm":     lambda inp: f"spawn {_count(inp.get('agents', inp.get('tasks', [])))} agents",
    "TaskGet":   lambda inp: str(inp.get("task_id", "")),
    "TaskStop":  lambda inp: str(inp.get("task_id", "")),
    "WebFetch":  lambda inp: urlparse(str(inp.get("url", ""))).hostname or str(inp.get("url", "")),
    "WebSearch": lambda inp: _trunc(str(inp.get("query", "")), _QUERY_LIMIT),
    "TodoWrite": lambda inp: f"{_count(inp.get('todos', []))} todos",
    **dict.fromkeys(("lsp_diagnostics", "lsp_goto_definition", "lsp_find_references", "lsp_rename"), _lsp_detail),
    **dict.fromkeys((
        "get_symbols_overview", "find_symbol", "find_referencing_symbols", "replace_symbol_body",
        "insert_before_symbol", "insert_after_symbol", "safe_delete_symbol", "symbol_overview",
    ), _symbol_detail),
    **dict.fromkeys(("ListQueryableProjects", "RegisterExternalProject", "QueryExternalProject"), _external_detail),
}


def summarize_tool_call(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, str]:
    """Return (label, detail) for a given tool invocation.

    Maps known tool names to concise summaries; falls back to (tool_name, "")
    for unknown tools so the indicator stays informative.
    """
    detail = _DETAIL.get(tool_name)
    return tool_name, detail(tool_input or {}) if detail else ""
