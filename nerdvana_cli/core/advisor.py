"""The advisor: a stronger model the agent consults at decision points.

Author: 최진호
Date:   2026-10-03

The agent hands a short question and an excerpt of the recent conversation to the model named by
``advisor.model`` and gets guidance back. The advisor has no tools and does no work; unlike an escalation it
does not take over the session. What it is sent is bounded: the last ``advisor.max_context_messages``
messages, each cut short with tool output cut hardest, and every secret-looking value replaced (see
``core/secrets.py``), never the whole history. Each consultation is a request on the advisor's model: it is
recorded in the ledger under the agent type ``advisor``, priced for that model, and counts against
``session.max_cost_usd`` and the token limit like any other request of the session.

At most ``advisor.max_calls`` consultations are made per run, however they were started: by the model through
the ``Advisor`` tool, or by the loop when an escalation signal reaches its threshold (``advisor.on_signals``).
A consultation that cannot be made says why in plain words and the caller carries on without it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core import signals
from nerdvana_cli.core.provider_recovery import parse_fallback
from nerdvana_cli.core.secrets import SecretMasker
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.factory import create_provider, resolve_api_key
from nerdvana_cli.types import Message, Role

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

# Output budget of one consultation, and the longest question, message, tool output and tool call input
# (in characters) that goes into the prompt.
MAX_REPLY_TOKENS = 1500
_QUESTION_CHARS  = 2000
_MESSAGE_CHARS   = 3000
_TOOL_CHARS      = 1200
_CALL_CHARS      = 300

_SYSTEM = (
    "You advise a coding agent that is working in a software repository. You have no tools and cannot see the "
    "files: you get its question and an excerpt of its recent conversation, in which tool output is shortened and "
    "secrets are masked. Answer the question directly and briefly, in under 250 words: the recommendation "
    "first, then the reasons and the risks that matter, with concrete next steps. Do not write the whole "
    "solution and do not ask questions back."
)


@dataclass(frozen=True)
class Reply:
    """What one request to the advisor's model produced: the text, the usage it reported and an error if it failed."""

    text:  str
    usage: dict[str, int] = field(default_factory=dict)
    error: str            = ""


@dataclass(frozen=True)
class Advice:
    """The outcome of a consultation: guidance, or the reason there is none (``ok`` False)."""

    text: str
    ok:   bool = True


OneShot = Callable[[str | None, str, str, str], Awaitable[Reply]]


def provider_completion(settings: NerdvanaSettings) -> OneShot:
    """The request function used for real: one non-streaming completion on ``provider:model``, without tools.

    On the session's own provider the session's key and base URL are used; another provider gets the key from
    its environment variable.
    """

    async def complete(provider: str | None, model: str, system: str, prompt: str) -> Reply:
        config = settings.model
        name   = provider or config.provider
        own    = name == config.provider
        client = create_provider(
            provider    = name,
            model       = model,
            api_key     = config.api_key if own else resolve_api_key(ProviderName(name)),
            base_url    = config.base_url if own else "",
            max_tokens  = MAX_REPLY_TOKENS,
            temperature = config.temperature,
        )
        result = await client.send(system, [{"role": "user", "content": prompt}], [])
        if result.get("is_error"):
            return Reply("", {}, str(result.get("content") or "the request failed"))
        return Reply(str(result.get("content", "")), dict(result.get("usage") or {}))

    return complete


def _cut(text: str, limit: int) -> str:
    """*text* at most about *limit* characters, keeping its start and its end."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - limit} characters cut ...]\n{text[-tail:]}"


def _text_of(content: str | list[dict[str, Any]]) -> str:
    """The text of a message body; an image block counts as a marker."""
    if isinstance(content, str):
        return content
    return " ".join(str(b.get("text", "")) if b.get("type") == "text" else f"[{b.get('type', 'block')}]" for b in content if isinstance(b, dict))


def render_context(messages: Sequence[Message], limit: int) -> str:
    """The last *limit* of *messages* as a short transcript: tool calls named, tool output cut hardest."""
    window = list(messages)[-limit:]
    names  = {str(call.get("id", "")): str(call.get("name", "tool")) for m in window for call in m.tool_uses}
    lines: list[str] = []
    for message in window:
        if message.role == Role.TOOL:
            name = names.get(message.tool_use_id or "", "tool")
            lines.append(f"[{name} result{' (error)' if message.is_error else ''}] {_cut(_text_of(message.content), _TOOL_CHARS)}")
            continue
        text = _text_of(message.content)
        if message.role == Role.ASSISTANT and message.tool_uses and text == "[tool execution]":
            text = ""
        lines.append(f"[{message.role.value}] {_cut(text, _MESSAGE_CHARS)}".rstrip())
        lines.extend(f"  call {c.get('name', '?')} {_cut(json.dumps(c.get('input', {}), ensure_ascii=False), _CALL_CHARS)}" for c in message.tool_uses)
    return "\n".join(lines)


class Advisor:
    """The consultations of one agent loop: what may be asked, how often, what it costs, what is sent."""

    def __init__(self, loop: AgentLoop, complete: OneShot | None = None) -> None:
        self._loop     = loop
        self._complete = complete or provider_completion(loop.settings)
        self.calls     = 0

    def start_run(self) -> None:
        """A new run gets its consultations back."""
        self.calls = 0

    @property
    def available(self) -> bool:
        """True for the main agent when ``advisor.enabled`` is on and a model is named; sub-agents never consult."""
        config = self._loop.settings.advisor
        return config.enabled and bool(config.model) and self._loop.origin.agent_type == "main"

    def _refusal(self) -> str:
        """Why no consultation can be made now, or an empty string."""
        loop, config = self._loop, self._loop.settings.advisor
        if not self.available:
            return "The advisor is not available: it is off or names no model (advisor.enabled, advisor.model)."
        if self.calls >= config.max_calls:
            return f"The advisor was not called: {config.max_calls} consultation(s) per run are used up. Decide with what you have."
        if loop.limits.exhausted()[0]:
            return "The advisor was not called: a session cost or token limit is reached."
        provider, _ = parse_fallback(config.model)
        if provider and provider != loop.settings.model.provider and not resolve_api_key(ProviderName(provider)):
            return f"The advisor was not called: no credential for provider {provider} is set in the environment."
        return ""

    def _masker(self) -> SecretMasker:
        """A masker for what leaves for the advisor's model: always on, whatever ``session.mask_secrets`` says."""
        settings = self._loop.settings
        key      = settings.model.api_key
        return SecretMasker.from_environment({"API_KEY": key} if key else None, settings.session.mask_extra_patterns)

    def build_prompt(self, question: str, reason: str) -> str:
        """The prompt for the advisor's model: the question, why it came up, and the masked recent conversation."""
        loop    = self._loop
        context = render_context(loop.state.messages, loop.settings.advisor.max_context_messages)
        parts   = [f"Question from the agent:\n{_cut(question.strip(), _QUESTION_CHARS)}"]
        if reason:
            parts.append(f"Why it came up: {reason}")
        parts.append(f"Recent conversation (oldest first):\n{context or '(empty)'}")
        masked = self._masker().mask("\n\n".join(parts))
        if masked.count:
            loop._signals[signals.SECRET_MASKED] += masked.count
        return masked.text

    async def advise(self, question: str, reason: str = "") -> Advice:
        """Ask the advisor *question*; *reason* says what prompted it when the loop asks on its own."""
        refusal = self._refusal()
        if refusal:
            return Advice(refusal, ok=False)
        loop, config = self._loop, self._loop.settings.advisor
        provider, model = parse_fallback(config.model)
        self.calls += 1
        loop._signals[signals.ADVISED] += 1
        try:
            reply = await self._complete(provider, model, _SYSTEM, self.build_prompt(question, reason))
        except Exception as exc:  # noqa: BLE001 - any failure of the request is reported, never raised into the run
            logger.warning("advisor request failed: %s", exc)
            return Advice(f"The advisor could not answer: {exc}", ok=False)
        origin = replace(loop.origin, agent_id="advisor", agent_type="advisor", turn=loop.turns_used, last_tool=loop._last_tool)
        loop.limits.record_other(provider or loop.settings.model.provider, model, reply.usage, origin)
        if reply.error:
            return Advice(f"The advisor could not answer: {reply.error}", ok=False)
        if not reply.text.strip():
            return Advice("The advisor returned no guidance.", ok=False)
        left = max(config.max_calls - self.calls, 0)
        return Advice(f"{reply.text.strip()}\n\n[Advisor consultations left in this run: {left}]")
