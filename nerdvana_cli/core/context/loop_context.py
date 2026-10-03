"""What the agent loop adds around the model's own history: the provider form, reports and session context.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.config.settings import ModelConfig, NerdvanaSettings
from nerdvana_cli.core.context.context_snapshot import collect_snapshot, format_snapshot
from nerdvana_cli.core.context.nirnamd import load_nirna_files
from nerdvana_cli.core.context.tool_index import ToolIndex
from nerdvana_cli.core.hooks.hooks import HookContext, HookEngine, HookEvent
from nerdvana_cli.core.state.todos import describe, load_todos, open_items
from nerdvana_cli.providers.anthropic_features import uses_server_search
from nerdvana_cli.providers.base import ProviderName, detect_provider
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.types import Message, Role

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

    # Provider classes are optional-extras imports: they are only named in the annotation below.
    from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
    from nerdvana_cli.providers.gemini_provider import GeminiProvider
    from nerdvana_cli.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# Status line prefix of a compaction, as the front ends read it.
COMPACT_STATUS_PREFIX = "\x00COMPACT:"

# Longest background task output quoted in a completion notice.
_BACKGROUND_REPORT_CHARS = 4_000


def new_provider(model: ModelConfig) -> AnthropicProvider | OpenAIProvider | GeminiProvider:
    """The provider adapter that *model* names, built from its settings."""
    return create_provider(
        provider=ProviderName(model.provider) if model.provider else None, model=model.model, api_key=model.api_key,
        base_url=model.base_url, max_tokens=model.max_tokens, temperature=model.temperature, prompt_caching=model.prompt_caching,
        extended_thinking=model.extended_thinking, thinking_budget=model.thinking_budget, show_thinking=model.show_thinking,
        reasoning_effort=model.reasoning_effort, openai_api=model.openai_api, gemini_api=model.gemini_api,
        anthropic_tool_search=model.anthropic_tool_search, anthropic_compaction=model.anthropic_compaction,
    )


def defer_mode(settings: NerdvanaSettings) -> str:
    """``session.defer_tools``, except that MCP tools the Anthropic API searches itself are not also deferred behind ToolSearch."""
    model    = settings.model
    provider = model.provider or detect_provider(model.model).value
    if provider == ProviderName.ANTHROPIC.value and uses_server_search(model.model, model.anthropic_tool_search):
        return "never"
    return settings.session.defer_tools


def prepare_tools(loop: AgentLoop) -> list[Any]:
    """The tools a run of *loop* may use, with MCP tools deferred behind ToolSearch when their declarations are large.

    What the model has loaded stays loaded across prompts of the session. Sub-agents never defer: they have
    no ToolSearch. A loop built without a ToolSearch factory never defers either, and neither does one whose
    provider searches the MCP tools on the server (see ``defer_mode``).
    """
    visible     = [t for t in loop.registry.all_tools() if loop.policy.is_visible(t.name) and t.name != "ToolSearch"]
    session     = loop.settings.session
    search_tool = loop._factories.tool_search
    deferring   = loop.origin.agent_type == "main" and search_tool is not None
    index       = ToolIndex.build(visible, defer_mode(loop.settings), session.defer_tools_threshold) if deferring else ToolIndex()
    if loop._tool_index is not None:
        index.loaded = loop._tool_index.loaded & set(index.deferred)
    loop._tool_index = index
    if index.deferred and search_tool is not None:
        search = search_tool(index)
        loop.registry.register(search)
        visible.append(search)
    return visible


def provider_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """*messages* in the form every provider adapter takes."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == Role.USER:
            out.append({"role": "user", "content": msg.content})
        elif msg.role == Role.ASSISTANT:
            d = {"role": "assistant", "content": msg.content, **({"tool_uses": msg.tool_uses} if msg.tool_uses else {})}
            if msg.provider_blocks:
                d["provider_blocks"] = msg.provider_blocks
            out.append(d)
        elif msg.role == Role.TOOL:
            out.append({"role": "tool", "content": msg.content, "tool_use_id": msg.tool_use_id or "", "is_error": msg.is_error})
    return out


def background_reports(task_registry: Any) -> list[Message]:
    """One message per background task that finished since the model last looked."""
    if task_registry is None or not hasattr(task_registry, "drain_unreported"):
        return []
    reports: list[Message] = []
    for task in task_registry.drain_unreported():
        body = task.output if task.output else (task.error or "")
        if len(body) > _BACKGROUND_REPORT_CHARS:
            body = body[:_BACKGROUND_REPORT_CHARS] + f"\n... [cut; TaskGet {task.id} returns the full output]"
        reports.append(Message(
            role    = Role.USER,
            content = f"[Background task {task.id} {task.status}] {task.description}\n{body}",
        ))
    return reports


def open_todos_note(session_id: str) -> list[Message]:
    """After compaction, the open todo items of the session that the summary may have lost."""
    pending = open_items(load_todos(session_id))
    if not pending:
        return []
    return [Message(role=Role.USER, content=f"Open todo items (kept across compaction):\n{describe(pending)}")]


async def session_start_context(settings: NerdvanaSettings, hooks: HookEngine, tools: list[Any]) -> str:
    """What the session's requests carry after the system prompt: a workspace snapshot and SESSION_START output."""
    parts: list[str] = []
    try:
        snapshot = format_snapshot(await collect_snapshot(settings.cwd or "."))
        if snapshot.strip():
            parts.append(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.debug("context snapshot skipped: %s", exc)
    documents = [{"path": doc.path, "type": doc.type, "chars": len(doc.content)} for doc in load_nirna_files(cwd=settings.cwd or ".")]
    hooks.emit(HookEvent.INSTRUCTIONS_LOADED, settings, files=documents)
    for result in hooks.fire(HookContext(event=HookEvent.SESSION_START, settings=settings, tools=tools)):
        if result.system_prompt_append:
            parts.append(result.system_prompt_append)
        parts.extend(str(message["content"]) for message in result.inject_messages if message.get("content"))
    return "\n\n".join(parts)
