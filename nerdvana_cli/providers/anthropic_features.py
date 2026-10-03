"""Anthropic request features beyond the plain message call: effort, server-side tool search and compaction.

Author: 최진호
Date:   2026-10-03

Everything here is pure request shaping: tables of which model takes what, the declarations and headers a
request needs, and the small state that keeps a conversation's effort changes cache friendly. The provider
(``anthropic_provider.py``) owns the network calls. Sources: the Anthropic documentation pages for effort,
compaction on demand, tool search and the memory tool (platform.claude.com/docs).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from nerdvana_cli.types import ToolSpec

logger = logging.getLogger(__name__)

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Beta headers; a request carries only the ones its content needs.
BETA_TURN_EFFORT = "mid-conversation-output-config-2026-07-01"
BETA_COMPACTION  = "compact-2026-09-04"

# Models that take ``output_config.effort``, by id prefix. Every one takes low, medium and high; the tuples
# below name the models that also take the higher levels (the effort page's availability table). A model that
# is not listed gets no effort field at all.
_EFFORT_MAX = (
    "claude-fable-5", "claude-mythos-5", "claude-mythos-preview", "claude-opus-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6",
)
_EFFORT_ANY = (*_EFFORT_MAX, "claude-opus-4-5")
_EFFORT_XHIGH = (
    "claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5",
)
# Models where an effort-only system message changes the level without restarting the prompt cache.
_TURN_EFFORT = ("claude-fable-5-1", "claude-mythos-5-1", "claude-opus-5", "claude-sonnet-5-5")
# Models that take the compaction request (same list as ``max`` effort on the compaction page).
_COMPACTION = _EFFORT_MAX

# The server-side tool search tools, keyed by the ``anthropic_tool_search`` setting.
TOOL_SEARCH_TOOLS: dict[str, dict[str, str]] = {
    "bm25":  {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"},
    "regex": {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
}

# Content blocks the API returns that must be sent back unchanged with the assistant turn.
ECHOED_BLOCKS = frozenset({"thinking", "redacted_thinking", "server_tool_use", "tool_search_tool_result", "compaction"})


def supported_efforts(model: str) -> tuple[str, ...]:
    """The effort levels *model* accepts; empty when it takes none."""
    if not model.startswith(_EFFORT_ANY):
        return ()
    levels: tuple[str, ...] = EFFORT_LEVELS[:3]
    if model.startswith(_EFFORT_XHIGH):
        levels += ("xhigh",)
    if model.startswith(_EFFORT_MAX):
        levels += ("max",)
    return levels


def normalize_effort(model: str, value: str) -> str:
    """The effort to send for the configured *value*: lower case, or empty for nothing.

    Empty when *value* is empty or *model* takes no effort at all. Raises ValueError for a value the model
    does not accept, naming the levels it does.
    """
    level = value.strip().lower()
    allowed = supported_efforts(model)
    if not level or not allowed:
        return ""
    if level not in allowed:
        raise ValueError(f"reasoning_effort {value!r} is not an effort level of {model} (use one of: {', '.join(allowed)})")
    return level


def supports_turn_effort(model: str) -> bool:
    """True when an effort-only system message changes *model*'s effort without restarting the cache."""
    return model.startswith(_TURN_EFFORT)


def supports_compaction(model: str) -> bool:
    """True when *model* takes the on-demand compaction request."""
    return model.startswith(_COMPACTION)


class EffortTracker:
    """The effort levels of one conversation: a top-level base and, where the model allows, changes between turns.

    The base goes in ``output_config.effort`` and stays the same for the whole conversation, because changing
    it restarts the prompt cache. A model that takes a per-message change gets an effort-only system message
    in the history instead; the message stays at the position where it was added, so the cached prefix before
    it keeps matching on later requests. A model without that feature holds the first level it was given.
    """

    def __init__(self, model: str, configured: str = "") -> None:
        self.model  = model
        self._base  = normalize_effort(model, configured)
        self._level = self._base
        self._marks: dict[int, str] = {}
        self._sent  = False

    def set_turn(self, level: str) -> bool:
        """Run the following turns at *level*; False when the model cannot change effort cache friendly any more.

        Before the first request the level simply becomes the base. After it, a model with per-message effort
        takes the change from the next user message on, and any other model keeps the level it already runs at.
        """
        wanted = normalize_effort(self.model, level)
        if not wanted:
            return False
        if not self._sent:
            self._base = self._level = wanted
            return True
        if not supports_turn_effort(self.model):
            logger.debug("effort %s not applied: %s holds %s for the conversation", wanted, self.model, self._level or "its default")
            return False
        self._level = wanted
        return True

    def prepare(self, messages: Sequence[dict[str, Any]]) -> tuple[str, dict[int, str]]:
        """The base effort and the effort changes (index of the message each precedes, level) for a request."""
        self._sent = True
        self._marks = {index: level for index, level in self._marks.items() if index < len(messages)}
        current = self._marks[max(self._marks)] if self._marks else self._base
        if self._level != current and supports_turn_effort(self.model) and messages and messages[-1].get("role") == "user":
            self._marks[len(messages) - 1] = self._level
        return self._base, dict(self._marks)


def effort_message(level: str) -> dict[str, Any]:
    """An effort-only system message: no text, the new level in ``output_config``."""
    return {"role": "system", "content": [], "output_config": {"effort": level}}


def beta_headers(api_messages: Sequence[dict[str, Any]]) -> dict[str, str]:
    """The ``anthropic-beta`` header the request needs, judged from the messages it carries; empty when none."""
    betas: list[str] = []
    if any(message["role"] == "system" for message in api_messages):
        betas.append(BETA_TURN_EFFORT)
    if any(
        isinstance(message["content"], list) and any(block.get("type") == "compaction" for block in message["content"])
        for message in api_messages
    ):
        betas.append(BETA_COMPACTION)
    return {"anthropic-beta": ",".join(betas)} if betas else {}


def with_beta(headers: dict[str, str], beta: str) -> dict[str, str]:
    """*headers* with *beta* added to the ``anthropic-beta`` list."""
    betas = [name for name in headers.get("anthropic-beta", "").split(",") if name]
    return {**headers, "anthropic-beta": ",".join(betas if beta in betas else [*betas, beta])}


def plain(value: Any) -> Any:
    """*value* as plain JSON data: SDK models and namespaces dumped, ``None`` fields dropped (the API rejects them)."""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return plain(dump(mode="json"))
    if hasattr(value, "__dict__"):
        return plain(vars(value))
    return value


def api_tool(tool: ToolSpec, model: str) -> dict[str, Any]:
    """One tool's declaration: the API's own definition for a tool that has one on Claude models, else a function tool.

    A tool names its native form in ``anthropic_native`` (the memory tool does). An Anthropic-compatible
    endpoint that serves another model gets the plain function declaration, which any such server understands.
    """
    native = getattr(tool, "anthropic_native", None)
    if native and model.startswith("claude-"):
        return dict(native)
    return {"name": tool.name, "description": tool.description_text, "input_schema": tool.input_schema}


def is_deferrable(tool: ToolSpec) -> bool:
    """True for the tools server-side search may defer: the ones that came from an MCP server."""
    return "mcp" in getattr(tool, "tags", frozenset())


def declare_tools(tools: Sequence[ToolSpec], model: str, search: str = "off") -> list[dict[str, Any]]:
    """The ``tools`` array of a request.

    With ``search`` set to ``bm25`` or ``regex`` on a Claude model, MCP tools are declared with
    ``defer_loading`` and follow the search tool; everything else stays loaded. The search tool itself is never
    deferred, which satisfies the API's rule that at least one tool is not. Without MCP tools nothing is
    deferred and no search tool is added.
    """
    declared = [(tool, api_tool(tool, model)) for tool in tools]
    search_tool = TOOL_SEARCH_TOOLS.get(search)
    if search_tool is None or not model.startswith("claude-"):
        return [item for _, item in declared]
    deferred = [{**item, "defer_loading": True} for tool, item in declared if is_deferrable(tool)]
    if not deferred:
        return [item for _, item in declared]
    return [*(item for tool, item in declared if not is_deferrable(tool)), dict(search_tool), *deferred]
