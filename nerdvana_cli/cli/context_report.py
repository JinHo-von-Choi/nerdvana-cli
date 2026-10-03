"""Where the context window goes: a breakdown of the next request by source.

Author: 최진호
Date:   2026-10-03

Every request carries the system prompt, the tool declarations and the conversation. This module
splits the three into the parts a user can act on (the skills catalog, the project documents, one
tool's declaration, the results of one tool) and puts the totals next to the context window and the
compaction threshold. The figures are the loop's own estimate (``core/context/token_estimator.py``), not a
provider's count.

Everything here is a pure function of a system prompt, tool objects, messages and ``SessionConfig``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.config.settings import SessionConfig
from nerdvana_cli.core.context.context_budget import message_tokens
from nerdvana_cli.core.context.observation_mask import mask_observations
from nerdvana_cli.core.context.token_estimator import approx_tokens
from nerdvana_cli.core.context.tool_index import declaration_tokens
from nerdvana_cli.types import Role

# Where the sections of the system prompt that carry a heading of their own begin.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("# Skills",                                 "skills catalog"),
    ("# Deferred tools",                         "deferred tool index"),
    ("# Environment",                            "environment"),
    ("# User & Project Instructions (NIRNA.md)", "project documents (NIRNA.md, AGENTS.md, CLAUDE.md)"),
)
_PROJECT_DOCS  = _MARKERS[-1][1]
_BASE          = "base instructions"
_GIT           = "git summary"
_GIT_LINES     = ("- Is a git repository", "- Git branch", "- Main branch", "- Git status", "- Recent commits:")

# Share of the used window a part must reach to be called out, and the least tokens masking must save to be worth saying.
LARGE_SHARE    = 0.10
MASK_HINT_MIN  = 2000


@dataclass(frozen=True)
class Item:
    """One named part of the context and its estimated tokens."""

    label:  str
    tokens: int
    count:  int = 0


@dataclass(frozen=True)
class ContextReport:
    """The context of the next request, by source, against the window."""

    system:        tuple[Item, ...]
    tools:         tuple[Item, ...]
    deferred:      int
    roles:         tuple[Item, ...]
    results:       tuple[Item, ...]
    window:        int
    threshold:     int
    maskable:      int
    mask_enabled:  bool
    mask_trigger:  int
    mask_due:      bool

    @property
    def system_tokens(self) -> int:
        return sum(i.tokens for i in self.system)

    @property
    def tool_tokens(self) -> int:
        return sum(i.tokens for i in self.tools)

    @property
    def conversation_tokens(self) -> int:
        return sum(i.tokens for i in self.roles)

    @property
    def total(self) -> int:
        return self.system_tokens + self.tool_tokens + self.conversation_tokens

    def contributors(self) -> list[Item]:
        """Every leaf part of the context, largest first; the tokens add up to ``total``."""
        leaves = [
            *self.system,
            *(Item(f"tool declaration {i.label}", i.tokens) for i in self.tools),
            *(i for i in self.roles if i.label != Role.TOOL.value),
            *(Item(f"tool results {i.label}", i.tokens, i.count) for i in self.results),
        ]
        return sorted(leaves, key=lambda item: -item.tokens)


def _git_split(environment: str) -> tuple[str, str]:
    """The environment section without its git lines, and those lines."""
    rest: list[str] = []
    git:  list[str] = []
    in_git = False
    for line in environment.splitlines():
        in_git = line.startswith(_GIT_LINES) or (in_git and line.startswith("  "))
        (git if in_git else rest).append(line)
    return "\n".join(rest), "\n".join(git)


def _split_sections(prompt: str) -> list[tuple[str, str]]:
    """(label, text) per part of a system prompt built by ``build_system_prompt``.

    A part ends where the next heading of ``_MARKERS`` begins. The project documents come last and may
    hold headings of their own, so nothing after them is split further.
    """
    parts: list[tuple[str, str]] = [(_BASE, "")]
    for block in prompt.split("\n\n"):
        label = next((name for heading, name in _MARKERS if block.startswith(heading)), None)
        if label is not None and parts[-1][0] != _PROJECT_DOCS:
            parts.append((label, block))
        else:
            parts[-1] = (parts[-1][0], f"{parts[-1][1]}\n\n{block}" if parts[-1][1] else block)
    return parts


def system_prompt_items(prompt: str, extras: Mapping[str, str] | None = None) -> tuple[Item, ...]:
    """The parts of *prompt* and of what the loop appends to it (*extras*: label to text), empty ones left out."""
    texts: list[tuple[str, str]] = []
    for label, text in _split_sections(prompt):
        if label == "environment":
            text, git = _git_split(text)
            texts.append((_GIT, git))
        texts.append((label, text))
    texts.extend((extras or {}).items())
    return tuple(Item(label, approx_tokens(text)) for label, text in texts if text.strip())


def _tool_names(messages: Sequence[Any]) -> dict[str, str]:
    """Tool call id to tool name for every call the assistant made."""
    return {
        str(use.get("id", "")): str(use.get("name", ""))
        for message in messages
        if message.role == Role.ASSISTANT
        for use in message.tool_uses
    }


def role_items(messages: Sequence[Any]) -> tuple[tuple[Item, ...], tuple[Item, ...]]:
    """(tokens per role, tokens of tool results per tool name), each largest first."""
    names = _tool_names(messages)
    roles:   dict[str, list[int]] = {}
    results: dict[str, list[int]] = {}
    for message in messages:
        tokens = message_tokens([message])
        roles.setdefault(message.role.value, []).append(tokens)
        if message.role == Role.TOOL:
            results.setdefault(names.get(str(message.tool_use_id or ""), "(unknown tool)"), []).append(tokens)

    def items(groups: dict[str, list[int]]) -> tuple[Item, ...]:
        return tuple(sorted((Item(k, sum(v), len(v)) for k, v in groups.items()), key=lambda i: -i.tokens))

    return items(roles), items(results)


def masking_effect(messages: Sequence[Any], session: SessionConfig) -> tuple[int, bool]:
    """(tokens clearing every old read-type result would save now, whether masking would trigger on it now).

    Works on copies of the messages; nothing in *messages* changes.
    """
    def run(trigger: int) -> int:
        return mask_observations([dataclasses.replace(m) for m in messages], keep_last=session.mask_keep_last, trigger_tokens=trigger).tokens_saved

    return run(0), run(session.mask_trigger_tokens) > 0


def build_report(
    system_prompt: str,
    tools:         Sequence[Any],
    messages:      Sequence[Any],
    session:       SessionConfig,
    deferred:      int = 0,
    extras:        Mapping[str, str] | None = None,
) -> ContextReport:
    """The breakdown of a request made of *system_prompt* (plus *extras*), the declared *tools* and *messages*.

    *deferred* is how many tools are not declared yet because they sit behind ToolSearch.
    """
    roles, results   = role_items(messages)
    maskable, due    = masking_effect(messages, session)
    declared         = tuple(sorted((Item(t.name, declaration_tokens(t)) for t in tools), key=lambda i: -i.tokens))
    return ContextReport(
        system       = system_prompt_items(system_prompt, extras),
        tools        = declared,
        deferred     = deferred,
        roles        = roles,
        results      = results,
        window       = session.max_context_tokens,
        threshold    = int(session.max_context_tokens * session.compact_threshold),
        maskable     = maskable,
        mask_enabled = session.observation_masking,
        mask_trigger = session.mask_trigger_tokens,
        mask_due     = due,
    )


def advice(report: ContextReport) -> list[str]:
    """What the numbers say: the compaction threshold, the parts that dominate, and whether masking would help."""
    notes: list[str] = []
    used = report.total
    if report.threshold and used > report.threshold:
        notes.append(f"The context is past the compaction threshold ({used:,} of {report.threshold:,} tokens); the next turn compacts it.")
    for item in report.contributors()[:3]:
        if used and item.tokens / used >= LARGE_SHARE:
            notes.append(f"Largest: {item.label} is {item.tokens:,} tokens, {item.tokens / used:.0%} of the context.")
    if report.maskable >= MASK_HINT_MIN and not report.mask_enabled:
        notes.append(
            f"Observation masking would clear about {report.maskable:,} tokens of old read results "
            "(file reads, searches, shell output); set session.observation_masking to true to turn it on.",
        )
    elif report.maskable > 0 and report.mask_enabled and not report.mask_due:
        notes.append(
            f"Observation masking is on and will clear about {report.maskable:,} tokens of old read results "
            f"once they add up to session.mask_trigger_tokens ({report.mask_trigger:,}).",
        )
    return notes


def _line(label: str, tokens: int, used: int, indent: int = 2, note: str = "") -> str:
    share = f"{tokens / used:>5.1%}" if used else "     "
    return f"{' ' * indent + label:<56}{tokens:>9,}  {share}{note}"


def _rows(items: Sequence[Item], used: int, top: int, noun: str, indent: int = 2) -> list[str]:
    """The *top* largest *items* as lines and one line for the rest."""
    lines = [_line(i.label, i.tokens, used, indent, f"  ({i.count})" if i.count else "") for i in items[:top]]
    rest  = items[top:]
    if rest:
        lines.append(_line(f"{len(rest)} more {noun}", sum(i.tokens for i in rest), used, indent))
    return lines


def render(report: ContextReport, top: int = 8) -> str:
    """The report as plain text."""
    used = report.total
    pct  = f"{used / report.window:.0%}" if report.window else "n/a"
    lines = [
        f"Context window  {used:,} of {report.window:,} tokens ({pct}); compaction at {report.threshold:,}",
        "",
        _line("System prompt", report.system_tokens, used, 0),
        *_rows(report.system, used, len(report.system), "parts"),
        _line("Tool declarations", report.tool_tokens, used, 0, f"  ({len(report.tools)} declared, {report.deferred} deferred)"),
        *_rows(report.tools, used, top, "tools"),
        _line("Conversation", report.conversation_tokens, used, 0, f"  ({sum(i.count for i in report.roles)} messages)"),
        *_rows(report.roles, used, len(report.roles), "roles"),
    ]
    if report.results:
        lines += ["  Tool results by tool", *_rows(report.results, used, top, "tools", 4)]
    notes = advice(report)
    return "\n".join([*lines, "", *notes] if notes else lines)
