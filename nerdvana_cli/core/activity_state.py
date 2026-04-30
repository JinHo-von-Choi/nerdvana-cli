"""Activity state model and tool-call summarizer for the REPL indicator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

Phase = Literal["idle", "thinking", "tool_running", "streaming", "waiting_api"]

_BASH_LIMIT    = 60
_GLOB_LIMIT    = 60
_GREP_PAT_LIM  = 40
_GREP_PATH_LIM = 30
_PARISM_LIMIT  = 40
_AGENT_TYPE_LM = 40
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


def summarize_tool_call(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, str]:
    """Return (label, detail) for a given tool invocation.

    Maps known tool names to concise summaries; falls back to (tool_name, "")
    for unknown tools so the indicator stays informative.
    """
    inp = tool_input or {}

    # ------------------------------------------------------------------ Bash
    if tool_name == "Bash":
        cmd = inp.get("command", "")
        return "Bash", _trunc(str(cmd), _BASH_LIMIT)

    # ---------------------------------------------- file tools (path-based)
    if tool_name in ("FileRead", "FileWrite", "FileEdit"):
        path = inp.get("file_path", inp.get("path", ""))
        return tool_name, _short_path(str(path)) if path else ""

    # ------------------------------------------------------------------ Glob
    if tool_name == "Glob":
        pattern = inp.get("pattern", "")
        return "Glob", _trunc(str(pattern), _GLOB_LIMIT)

    # ------------------------------------------------------------------ Grep
    if tool_name == "Grep":
        pattern = _trunc(str(inp.get("pattern", "")), _GREP_PAT_LIM)
        path    = _trunc(str(inp.get("path", inp.get("directory", ""))), _GREP_PATH_LIM)
        detail  = f"{pattern} in {path}" if path else pattern
        return "Grep", detail

    # ---------------------------------------------------------------- Parism
    if tool_name == "Parism":
        cmd    = str(inp.get("cmd", ""))
        args   = inp.get("args", [])
        args_s = " ".join(str(a) for a in args) if args else ""
        detail = f"{cmd} {_trunc(args_s, _PARISM_LIMIT)}" if args_s else cmd
        return "Parism", detail.strip()

    # ----------------------------------------------------------------- Agent
    if tool_name == "Agent":
        agent_type = inp.get("subagent_type", inp.get("agent_type", ""))
        return "Agent", f"spawn: {agent_type}"

    # ----------------------------------------------------------------- Swarm
    if tool_name == "Swarm":
        agents = inp.get("agents", inp.get("tasks", []))
        n = len(agents) if isinstance(agents, list) else 0
        return "Swarm", f"spawn {n} agents"

    # ------------------------------------------------------------- TeamCreate
    if tool_name == "TeamCreate":
        return "TeamCreate", str(inp.get("team_name", ""))

    # ------------------------------------------------------------ SendMessage
    if tool_name == "SendMessage":
        return "SendMessage", f"to: {inp.get('to', '')}"

    # ---------------------------------------------------------------- TaskGet
    if tool_name == "TaskGet":
        return "TaskGet", str(inp.get("task_id", ""))

    # --------------------------------------------------------------- TaskStop
    if tool_name == "TaskStop":
        return "TaskStop", str(inp.get("task_id", ""))

    # --------------------------------------------------------------- WebFetch
    if tool_name == "WebFetch":
        url  = str(inp.get("url", ""))
        host = urlparse(url).hostname or url
        return "WebFetch", host

    # -------------------------------------------------------------- WebSearch
    if tool_name == "WebSearch":
        query = inp.get("query", "")
        return "WebSearch", _trunc(str(query), _QUERY_LIMIT)

    # -------------------------------------------------------------- TodoWrite
    if tool_name == "TodoWrite":
        todos = inp.get("todos", [])
        n = len(todos) if isinstance(todos, list) else 0
        return "TodoWrite", f"{n} todos"

    # ---------------------------------------------------------------- LSP tools
    if tool_name in ("lsp_diagnostics", "lsp_goto_definition", "lsp_find_references", "lsp_rename"):
        file_path = inp.get("file_path", "")
        symbol    = inp.get("symbol", "")
        detail    = _short_path(str(file_path)) if file_path else ""
        if symbol:
            detail = f"{detail}:{symbol}" if detail else str(symbol)
        return tool_name, detail

    # ---------------------------------------------------------- Symbol tools
    if tool_name in (
        "get_symbols_overview",
        "find_symbol",
        "find_referencing_symbols",
        "replace_symbol_body",
        "insert_before_symbol",
        "insert_after_symbol",
        "safe_delete_symbol",
        "symbol_overview",
    ):
        rel  = inp.get("relative_path", inp.get("within_relative_path", ""))
        sym  = inp.get("name_path", inp.get("symbol", ""))
        file_part   = _short_path(str(rel)) if rel else ""
        sym_part    = str(sym) if sym else ""
        detail = f"{file_part}:{sym_part}" if file_part and sym_part else file_part or sym_part
        return tool_name, detail

    # -------------------------------------------------- External project tools
    if tool_name in ("ListQueryableProjects", "RegisterExternalProject", "QueryExternalProject"):
        alias = inp.get("name", inp.get("alias", ""))
        return tool_name, str(alias) if alias else ""

    # -------------------------------------------------------------- Fallback
    return tool_name, ""
