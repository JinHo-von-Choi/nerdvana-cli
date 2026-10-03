"""Immutable loop iteration state for AgentLoop.

Centralises all per-iteration state into a single frozen dataclass.
State transitions are performed exclusively via .evolve(), which returns
a new instance — the original is never mutated.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from nerdvana_cli.core.loop.tool_ids import new_tool_use_id


@dataclasses.dataclass(frozen=True)
class LoopState:
    """Immutable snapshot of one agent loop iteration.

    Fields:
        iteration:          Current iteration counter (1-based).
        stop_reason:        Last stop reason reported by the provider.
        continuation_hint:  Optional text hint for context-limit continuation.
        token_budget_used:  Estimated token count at the start of this turn.
        session_id:         Identifier of the session this loop belongs to.
    """

    iteration:         int
    stop_reason:       Literal["continue", "end_turn", "max_tokens", "tool_error"]
    continuation_hint: str | None
    token_budget_used: int
    session_id:        str

    def evolve(self, **changes: object) -> LoopState:
        """Return a new LoopState with the specified fields overridden.

        Unchanged fields are copied from the current instance.
        This is a thin wrapper around dataclasses.replace to enforce the
        immutability contract at call sites.

        Example::

            new_state = state.evolve(iteration=state.iteration + 1, stop_reason="end_turn")
        """
        return dataclasses.replace(self, **changes)  # type: ignore[arg-type]


@dataclass
class LoopFlow:
    """Whether the run ends after the current step, and the context size measured for it."""

    finished:       bool = False
    context_tokens: int  = 0


@dataclass
class LoopTurn:
    """One request to the provider and what its response has delivered so far."""

    messages:        list[dict[str, Any]]
    used_ids:        set[str]
    sent_count:      int
    asst_text:       str                          = ""
    provider_blocks: list[dict[str, Any]]         = field(default_factory=list)
    thinking_buffer: str                          = ""
    tool_uses:       list[dict[str, Any]]         = field(default_factory=list)
    seen_calls:      set[tuple[str, str, str]]    = field(default_factory=set)

    def add_call(self, call_id: str, name: str, arguments: dict[str, Any] | None, thought_signature: str = "") -> None:
        """Collect a tool call, keeping ids unique and dropping a repeated copy.

        A provider may echo a call it already sent (same id, name and arguments),
        which must run once, or reuse an id for a different call, which gets a
        fresh id because providers reject a request holding two calls with one id.
        """
        call: dict[str, Any] = {"id": call_id or "", "name": name, "input": arguments or {}}
        if thought_signature:
            call["thought_signature"] = thought_signature
        signature = (call["id"], call["name"], json.dumps(call["input"], sort_keys=True, default=str))
        if signature in self.seen_calls:
            return
        self.seen_calls.add(signature)
        if not call["id"] or call["id"] in self.used_ids:
            call["id"] = new_tool_use_id(call["name"], self.used_ids)
        self.used_ids.add(call["id"])
        self.tool_uses.append(call)
