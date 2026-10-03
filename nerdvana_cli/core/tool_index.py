"""Tools whose full declaration is sent only after the model asks for them.

Author: 최진호
Date:   2026-10-03

Every request carries the declaration (name, description and parameter schema) of every tool. A few
built-in tools cost a few thousand tokens; an MCP server can add dozens of tools and tens of thousands.
When that cost gets large, the MCP tools are *deferred*: the system prompt lists their names with one line
each, and a ``ToolSearch`` tool lets the model load the ones it needs. A loaded tool is declared in full
from the next request on and stays declared for the session. Loading appends to the end of the declaration
list, so the part of the request that came before keeps its place.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nerdvana_cli.core.token_estimator import approx_tokens

MODES = ("auto", "always", "never")
DEFAULT_THRESHOLD = 3000
_WORDS = re.compile(r"[a-z0-9]+")


def declaration_tokens(tool: Any) -> int:
    """Estimated size of one tool's declaration."""
    return approx_tokens(json.dumps(
        {"name": tool.name, "description": tool.description_text, "input_schema": tool.input_schema}, ensure_ascii=False,
    ))


def _is_mcp(tool: Any) -> bool:
    return "mcp" in getattr(tool, "tags", frozenset())


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
        """Decide which of *tools* to defer: MCP tools, when ``mode`` says so or their declarations are large."""
        candidates = [t for t in tools if _is_mcp(t)]
        if mode == "never" or not candidates:
            return cls()
        if mode == "auto" and sum(declaration_tokens(t) for t in candidates) <= threshold:
            return cls()
        return cls({t.name: t for t in candidates})

    def declared(self, tools: list[Any]) -> list[Any]:
        """The tools to declare in a request: everything except deferred tools that are not loaded yet."""
        return [t for t in tools if t.name not in self.deferred or t.name in self.loaded]

    def is_unloaded(self, name: str) -> bool:
        """True for a deferred tool the model has not loaded."""
        return name in self.deferred and name not in self.loaded

    def index_lines(self) -> list[str]:
        """One line per deferred tool that is not loaded, for the system prompt."""
        return [f"- {name}: {_first_line(tool.description_text)}" for name, tool in sorted(self.deferred.items()) if name not in self.loaded]

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
