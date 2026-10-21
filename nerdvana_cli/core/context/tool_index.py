"""Tools whose full declaration is sent only after the model asks for them.

Author: 최진호
Date:   2026-10-03

Every request carries the declaration (name, description and parameter schema) of every tool. A few
built-in tools cost a few thousand tokens; an MCP server can add dozens of tools and tens of thousands.
When that cost gets large, the MCP tools are *deferred*: the system prompt lists their names with one line
each, and a ``ToolSearch`` tool lets the model load the ones it needs. Built-in tools that most tasks never
call (``BaseTool.deferrable``) are deferred the same way. A loaded tool is declared in full
from the next request on and stays declared for the session. Loading appends to the end of the declaration
list, so the part of the request that came before keeps its place.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nerdvana_cli.core.context.token_estimator import approx_tokens

DEFAULT_THRESHOLD = 3000
# An MCP server with more deferred tools than this is listed on one line, by tool name.
SERVER_LINE_THRESHOLD = 3
_WORDS = re.compile(r"[a-z0-9]+")


def declaration_tokens(tool: Any) -> int:
    """Estimated size of one tool's declaration."""
    return approx_tokens(json.dumps(
        {"name": tool.name, "description": tool.description_text, "input_schema": tool.input_schema}, ensure_ascii=False,
    ))


def _is_mcp(tool: Any) -> bool:
    return "mcp" in getattr(tool, "tags", frozenset())


def _is_deferrable(tool: Any) -> bool:
    return bool(getattr(tool, "deferrable", False))


def _mcp_server(name: str) -> str | None:
    """The server part of an ``mcp__{server}__{tool}`` name, or None for another name."""
    parts = name.split("__")
    return parts[1] if len(parts) >= 3 and parts[0] == "mcp" else None


def _first_line(text: str, limit: int = 110) -> str:
    line = (text.strip().splitlines() or [""])[0].strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


class ToolIndex:
    """Which tools are deferred and which of those the model has loaded."""

    def __init__(self, deferred: dict[str, Any] | None = None) -> None:
        self.deferred: dict[str, Any] = dict(deferred or {})
        self.loaded:   set[str]       = set()

    @classmethod
    def build(cls, tools: list[Any], mode: str = "auto", threshold: int = DEFAULT_THRESHOLD) -> ToolIndex:
        """Decide which of *tools* to defer.

        Built-in tools marked ``deferrable`` are deferred unless ``mode`` is ``never``. MCP tools are deferred
        when ``mode`` is ``always``, or under ``auto`` when their declarations add up to more than *threshold*.
        """
        if mode == "never":
            return cls()
        deferred = [t for t in tools if _is_deferrable(t) and not _is_mcp(t)]
        mcp      = [t for t in tools if _is_mcp(t)]
        if mcp and (mode == "always" or sum(declaration_tokens(t) for t in mcp) > threshold):
            deferred += mcp
        return cls({t.name: t for t in deferred})

    def declared(self, tools: list[Any]) -> list[Any]:
        """The tools to declare in a request: everything except deferred tools that are not loaded yet."""
        return [t for t in tools if t.name not in self.deferred or t.name in self.loaded]

    def is_unloaded(self, name: str) -> bool:
        """True for a deferred tool the model has not loaded."""
        return name in self.deferred and name not in self.loaded

    def index_lines(self) -> list[str]:
        """The deferred tools that are not loaded, for the system prompt.

        A built-in tool, and the tools of an MCP server with a few of them, get a line each with
        the first line of their description. A server with more tools gets one line with its tool
        names, so hundreds of MCP tools cost a few hundred tokens instead of thousands.
        """
        pending  = [(name, tool) for name, tool in sorted(self.deferred.items()) if name not in self.loaded]
        servers: dict[str, list[str]] = {}
        for name, tool in pending:
            server = _mcp_server(name) if _is_mcp(tool) else None
            if server is not None:
                servers.setdefault(server, []).append(name)
        lines: list[str] = []
        for name, tool in pending:
            server = _mcp_server(name) if _is_mcp(tool) else None
            if server is None or len(servers[server]) <= SERVER_LINE_THRESHOLD:
                lines.append(f"- {name}: {_first_line(tool.description_text)}")
            elif servers[server] and servers[server][0] == name:
                prefix = f"mcp__{server}__"
                short  = ", ".join(n[len(prefix):] for n in servers[server])
                lines.append(f"- {prefix}* ({len(servers[server])} tools): {short}")
        return lines

    def search(self, query: str, limit: int = 8) -> list[Any]:
        """Deferred tools matching *query*: ``select:a,b`` names tools exactly, otherwise words are scored."""
        query = query.strip()
        if query.startswith("select:"):
            wanted = [n.strip() for n in query[len("select:"):].split(",") if n.strip()]
            return [self.deferred[n] for n in wanted if n in self.deferred]
        if query in self.deferred:
            return [self.deferred[query]]
        words = set(_WORDS.findall(query.lower()))
        scored: list[tuple[int, str]] = []
        for name, tool in self.deferred.items():
            name_words = set(_WORDS.findall(name.lower()))
            text_words = set(_WORDS.findall(tool.description_text.lower()))
            score      = 3 * len(words & name_words) + len(words & text_words)
            if score:
                scored.append((score, name))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [self.deferred[name] for _, name in scored[:limit]]

    def load(self, tools: list[Any]) -> list[str]:
        """Mark *tools* as loaded; returns the names that were not loaded before."""
        fresh = [t.name for t in tools if t.name in self.deferred and t.name not in self.loaded]
        self.loaded.update(fresh)
        return fresh
