"""Core agent loop — the heart of NerdVana CLI.

Inspired by Claude Code's query.ts: a streaming agent loop that
calls the model, executes tools, and repeats until completion.
Provider-agnostic design: works with Anthropic, OpenAI, Gemini, Groq, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import AsyncGenerator
from typing import Any

from rich.console import Console

from nerdvana_cli.core.compact import FALLBACK_PROMPT, CompactionState, ai_compact
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolContext, ToolRegistry
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.types import (
    Message,
    PermissionBehavior,
    Role,
    SessionState,
    ToolResult,
)

console = Console()
logger = logging.getLogger(__name__)

TOOL_STATUS_PREFIX   = "\x00TOOL:"
TOOL_DONE_PREFIX     = "\x00TOOL_DONE:"
CONTEXT_USAGE_PREFIX = "\x00CTX_USAGE:"
COMPACT_STATUS_PREFIX = "\x00COMPACT:"


_COMPLEXITY_SIGNALS: list[str] = [
    r"리팩터링|refactor",
    r"새로운\s+(기능|모듈|서비스|시스템)|new\s+(feature|module|service|system)",
    r"마이그레이션|migration",
    r"\d+개\s+(파일|클래스|모듈)|\d+\s+(files?|classes?|modules?)",
    r"아키텍처|architecture|전면\s+개편",
    r"처음부터|from\s+scratch",
]


def _needs_planning(prompt: str) -> bool:
    """Return True if prompt contains 2+ complexity signals."""
    hits = sum(
        1 for p in _COMPLEXITY_SIGNALS if re.search(p, prompt, re.IGNORECASE)
    )
    return hits >= 2


_RETRYABLE_PATTERNS = re.compile(
    r"(429|529|503|timeout|rate.?limit|too many requests|service unavailable)",
    re.IGNORECASE,
)


def _is_retryable_error(error_msg: str) -> bool:
    """Return True if the error should trigger a model fallback."""
    return bool(_RETRYABLE_PATTERNS.search(error_msg))


_ULTRAWORK_PATTERN = re.compile(r"\b(ultrawork|ulw)\b", re.IGNORECASE)


def _is_ultrawork(prompt: str) -> bool:
    """Return True if the prompt requests ultrawork (extended thinking) mode."""
    return bool(_ULTRAWORK_PATTERN.search(prompt))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def estimate_messages_tokens(messages: list[Any]) -> int:
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        total += estimate_tokens(content)
        if msg.tool_uses:
            total += estimate_tokens(json.dumps(msg.tool_uses))
    return total


def compact_messages(messages: list[Any], max_tokens: int) -> list[Any]:
    if not messages:
        return messages
    current = estimate_messages_tokens(messages)
    if current <= max_tokens:
        return messages
    keep_recent = min(10, len(messages))
    recent = messages[-keep_recent:]
    remaining_budget = max_tokens - estimate_messages_tokens(recent)
    if remaining_budget <= 0:
        return messages[-4:]
    early = []
    for msg in messages[:-keep_recent]:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        cost = estimate_tokens(content)
        if remaining_budget - cost < 0:
            break
        early.append(msg)
        remaining_budget -= cost
    dropped_count = len(messages) - len(early) - len(recent)
    if dropped_count > 0:
        summary = Message(role=Role.USER, content=f"[context compacted: {dropped_count} earlier messages removed to fit context window]")
        return early + [summary] + recent
    return early + recent


class AgentLoop:
    """Core agent loop: API call -> stream -> tool exec -> repeat."""

    def __init__(
        self,
        settings:      NerdvanaSettings,
        registry:      ToolRegistry,
        session:       SessionStorage | None = None,
        task_registry: Any = None,
        team_registry: Any = None,
    ):
        self.settings      = settings
        self.registry      = registry
        self.session       = session or SessionStorage()
        self.state         = SessionState()
        self._task_registry = task_registry
        self._team_registry = team_registry
        self.console = Console()

        from nerdvana_cli.core.builtin_hooks import (
            context_limit_recovery,
            json_parse_recovery,
            ralph_loop_check,
            session_start_context_injection,
        )
        from nerdvana_cli.core.hooks import HookEngine, HookEvent

        self.hooks = HookEngine()
        self.hooks.register(HookEvent.SESSION_START, session_start_context_injection)
        self.hooks.register(HookEvent.AFTER_API_CALL, context_limit_recovery)
        self.hooks.register(HookEvent.AFTER_API_CALL, ralph_loop_check)
        self.hooks.register(HookEvent.AFTER_TOOL, json_parse_recovery)

        # Load user-defined hooks (~/.config/nerdvana-cli/hooks/, .nerdvana/hooks/)
        from nerdvana_cli.core.user_hooks import load_user_hooks
        self._user_hook_paths = load_user_hooks(self.hooks, settings)

        from nerdvana_cli.core.context_reminder import ContextReminder
        from nerdvana_cli.core.skills import SkillLoader

        self.skill_loader = SkillLoader(project_dir=settings.cwd)
        self.skill_loader.load_all()
        self._active_skill: str | None = None
        self._reminder = ContextReminder(cwd=settings.cwd or ".", max_recent=5)
        self._turn = 0

        # Load compress-context.skill as compaction prompt
        _compact_skill = self.skill_loader.get_by_name("compress-context")
        self._compact_prompt: str = _compact_skill.body if _compact_skill else FALLBACK_PROMPT

        self._compaction_state = CompactionState(
            max_failures=settings.session.compact_max_failures
        )

        self._session_started = False
        self._sticky_session_context: str = ""
        self.provider = self.create_provider_from_settings()

    def create_provider_from_settings(self) -> AnthropicProvider | OpenAIProvider | GeminiProvider:
        """Create provider from current settings."""
        provider_name = None
        if self.settings.model.provider:
            provider_name = ProviderName(self.settings.model.provider)

        return create_provider(
            provider=provider_name,
            model=self.settings.model.model,
            api_key=self.settings.model.api_key,
            base_url=self.settings.model.base_url,
            max_tokens=self.settings.model.max_tokens,
            temperature=self.settings.model.temperature,
        )

    def reset_session(self) -> None:
        """Reset session state so the next run() re-fires SESSION_START hooks.

        Called by /clear and any other path that wants to start fresh
        without rebuilding the AgentLoop instance.
        """
        self._session_started = False
        self._sticky_session_context = ""
        self.state.messages.clear()

    def build_system_prompt(self) -> str:
        from nerdvana_cli.core.prompts import build_system_prompt as build_prompt

        return build_prompt(
            tools=self.registry.all_tools(),
            parism_active=self.registry.get("Parism") is not None,
            model=self.settings.model.model,
            provider=self.settings.model.provider,
            cwd=self.settings.cwd,
        )

    def _to_provider_messages(self) -> list[dict[str, Any]]:
        """Convert internal Message objects to provider-neutral dict format."""
        messages = []
        for msg in self.state.messages:
            if msg.role == Role.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                d = {"role": "assistant", "content": msg.content}
                if msg.tool_uses:
                    d["tool_uses"] = msg.tool_uses
                messages.append(d)
            elif msg.role == Role.TOOL:
                tool_msg: dict[str, Any] = {
                    "role": "tool",
                    "content": msg.content,
                    "tool_use_id": msg.tool_use_id or "",
                    "is_error": msg.is_error,
                }
                messages.append(tool_msg)
        return messages

    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """Main entry point: submit a message and run the agent loop."""
        # ── Planning gate (opt-in via settings.session.planning_gate) ───
        if self.settings.session.planning_gate and _needs_planning(prompt):
            plan_output = await self._run_plan_agent(prompt)
            if plan_output:
                yield f"\n[Plan]\n{plan_output}\n[/Plan]\n"
                self.state.messages.append(
                    Message(
                        role    = Role.USER,
                        content = f"[Auto-generated plan]\n{plan_output}",
                    )
                )
        # ────────────────────────────────────────────────────────────────

        # ── Ultrawork mode (extended thinking on trigger keyword) ───────
        original_extended_thinking = self.settings.model.extended_thinking
        if _is_ultrawork(prompt):
            self.settings.model.extended_thinking = True
            yield "[dim cyan][Ultrawork mode: extended thinking ON][/dim cyan]\n"
        # ────────────────────────────────────────────────────────────────

        self._turn += 1
        reminder_text = self._reminder.build(turn=self._turn)
        if reminder_text:
            self.state.messages.append(Message(role=Role.USER, content=reminder_text))

        user_msg = Message(role=Role.USER, content=prompt)
        self.state.messages.append(user_msg)
        self.session.record_user_message(prompt)

        system_prompt = self.build_system_prompt()
        tools = self.registry.all_tools()

        # Fire SESSION_START hooks once per session and cache their sticky output.
        # build_system_prompt() already loads NIRNA.md — do NOT pass it via ctx.extra
        # to avoid double injection.
        if not self._session_started:
            self._session_started = True
            from nerdvana_cli.core.context_snapshot import collect_snapshot, format_snapshot
            from nerdvana_cli.core.hooks import HookContext, HookEvent

            sticky_parts: list[str] = []

            # 1. Project snapshot (once per session)
            try:
                snap = await collect_snapshot(self.settings.cwd or ".")
                snap_text = format_snapshot(snap)
                if snap_text.strip():
                    sticky_parts.append(snap_text)
            except Exception:  # noqa: BLE001
                pass

            # 2. Existing hook fan-out
            hook_ctx = HookContext(
                event=HookEvent.SESSION_START,
                settings=self.settings,
                tools=tools,
            )
            hook_results = self.hooks.fire(hook_ctx)
            for hr in hook_results:
                if hr.system_prompt_append:
                    sticky_parts.append(hr.system_prompt_append)
                # Backwards-compat: legacy hooks that still use inject_messages
                # for sticky content are honored once on the very first turn only.
                for msg in hr.inject_messages:
                    if msg.get("content"):
                        sticky_parts.append(str(msg["content"]))
            if sticky_parts:
                self._sticky_session_context = "\n\n".join(sticky_parts)

        # Always append sticky context — it persists across turns.
        if self._sticky_session_context:
            system_prompt += f"\n\n{self._sticky_session_context}"

        if self._active_skill:
            system_prompt += f"\n\n# Active Skill\n{self._active_skill}"

        try:
            async for event in self._loop(system_prompt, tools):
                yield event
        finally:
            self.settings.model.extended_thinking = original_extended_thinking

    def activate_skill(self, skill_body: str) -> None:
        self._active_skill = skill_body

    def deactivate_skill(self) -> None:
        self._active_skill = None

    def _next_fallback_model(self) -> str | None:
        """Return the next fallback model from the chain, or None if exhausted.

        Tries to find the current model's position in fallback_models and
        advances. If the current model is not in the list, starts from index 0.
        """
        current   = self.settings.model.model
        fallbacks = self.settings.model.fallback_models
        if not fallbacks:
            return None
        try:
            idx = fallbacks.index(current)
            next_idx = idx + 1
        except ValueError:
            next_idx = 0
        if next_idx < len(fallbacks):
            return fallbacks[next_idx]
        return None

    async def _run_plan_agent(self, prompt: str) -> str:
        """Spawn a Plan subagent and return its output.

        Used by the planning gate when settings.session.planning_gate is enabled
        and the prompt triggers complexity signals. The child's planning_gate is
        disabled to prevent recursive planning.
        """
        from nerdvana_cli.core.subagent import SubagentConfig, run_subagent
        from nerdvana_cli.tools.registry import create_subagent_registry

        child_settings = self.settings.model_copy(deep=True)
        child_settings.session.planning_gate = False

        registry = create_subagent_registry(
            settings      = child_settings,
            allowed_tools = ["Glob", "Grep", "FileRead", "Bash"],
        )
        config = SubagentConfig(
            agent_id  = "plan_agent",
            name      = "Plan",
            prompt    = f"Create an implementation plan for the following task:\n\n{prompt}",
            settings  = child_settings,
            registry  = registry,
            max_turns = 20,
        )
        try:
            return await run_subagent(config, asyncio.Event())
        except Exception as exc:  # noqa: BLE001
            return f"[plan agent error] {exc}"

    async def _loop(
        self,
        system_prompt: str,
        tools: list[Any],
    ) -> AsyncGenerator[str, None]:
        """Core while-true agent loop."""
        tool_context = ToolContext(
            cwd           = self.settings.cwd,
            task_registry = self._task_registry,
            team_registry = self._team_registry,
        )
        turn = 0
        original_model = self.settings.model.model

        try:
            while True:
                turn += 1
                if turn > self.settings.session.max_turns:
                    yield f"\n[bold yellow]Max turns ({self.settings.session.max_turns}) reached.[/bold yellow]"
                    return

                max_ctx = self.settings.session.max_context_tokens
                threshold = int(max_ctx * self.settings.session.compact_threshold)
                current_tokens = estimate_messages_tokens(self.state.messages)
                if current_tokens > threshold:
                    if not self._compaction_state.is_circuit_open:
                        yield f"{COMPACT_STATUS_PREFIX}compressing ({current_tokens} tokens)..."
                        msg_count_before = len(self.state.messages)
                        summary_msg = await ai_compact(
                            self.state.messages,
                            self.provider,
                            self._compaction_state,
                            prompt=self._compact_prompt,
                        )
                        if summary_msg is not None:
                            recent = self.state.messages[-4:]
                            # TOOL role messages become user after provider conversion,
                            # causing consecutive user messages if placed right after
                            # summary_msg(user). Trim so recent starts with ASSISTANT.
                            while recent and recent[0].role == Role.TOOL:
                                recent = recent[1:]
                            self.state.messages = [summary_msg] + recent
                            self.session.record_compaction(
                                tokens_before=current_tokens,
                                messages_before=msg_count_before,
                                strategy="ai",
                            )
                            yield f"{COMPACT_STATUS_PREFIX}done"
                        else:
                            self.state.messages = compact_messages(self.state.messages, threshold)
                            self.session.record_compaction(
                                tokens_before=current_tokens,
                                messages_before=msg_count_before,
                                strategy="naive",
                            )
                    else:  # circuit open
                        msg_count_before = len(self.state.messages)
                        self.state.messages = compact_messages(self.state.messages, threshold)
                        self.session.record_compaction(
                            tokens_before=current_tokens,
                            messages_before=msg_count_before,
                            strategy="naive",
                        )

                usage_pct = min(100, int(current_tokens / max_ctx * 100)) if max_ctx > 0 else 0
                yield f"{CONTEXT_USAGE_PREFIX}{usage_pct}"

                if self.settings.verbose:
                    self.console.print(f"[dim]Turn {turn} — {len(self.state.messages)} messages[/dim]")

                messages = self._to_provider_messages()

                try:
                    assistant_text = ""
                    tool_uses: list[dict[str, Any]] = []
                    current_tool_input = ""

                    async for event in self.provider.stream(system_prompt, messages, tools):
                        if event.type == "content_delta":
                            assistant_text += event.content
                            yield event.content

                        elif event.type == "tool_use_start":
                            current_tool_input = ""

                        elif event.type == "tool_use_delta":
                            current_tool_input += event.tool_input_delta

                        elif event.type == "tool_use_complete":
                            tool_uses.append(
                                {
                                    "id": event.tool_use_id or f"call_{len(tool_uses)}",
                                    "name": event.tool_name,
                                    "input": event.tool_input_complete or {},
                                }
                            )

                        elif event.type == "usage":
                            if event.usage:
                                self.state.usage.input_tokens = event.usage.get("input_tokens", 0)
                                self.state.usage.output_tokens = event.usage.get("output_tokens", 0)

                        elif event.type == "done":
                            stop_reason = event.stop_reason
                            if stop_reason == "max_tokens":
                                from nerdvana_cli.core.hooks import HookContext, HookEvent

                                recovery_ctx = HookContext(
                                    event       = HookEvent.AFTER_API_CALL,
                                    settings    = self.settings,
                                    tools       = self.registry.all_tools(),
                                    messages    = self.state.messages,
                                    stop_reason = "max_tokens",
                                    extra       = {"agent_loop": self},
                                )
                                recovery_results = self.hooks.fire(recovery_ctx)
                                recovered = False
                                for hr in recovery_results:
                                    for msg in hr.inject_messages:
                                        self.state.messages.append(
                                            Message(
                                                role    = Role.USER,
                                                content = msg["content"],
                                            )
                                        )
                                        recovered = True
                                if not recovered:
                                    yield "\n\n[bold red]Max tokens reached.[/bold red]"
                                    return
                                break  # exit stream loop, retry from top of while

                            elif stop_reason == "end_turn":
                                if assistant_text:
                                    self.state.messages.append(Message(role=Role.ASSISTANT, content=assistant_text))
                                    self.session.record_assistant_message(assistant_text)
                                return

                            elif stop_reason == "tool_use" and tool_uses:
                                if assistant_text:
                                    self.session.record_assistant_message(assistant_text, tool_uses)

                                # Yield tool execution start markers
                                for tu in tool_uses:
                                    input_preview = json.dumps(tu["input"], ensure_ascii=False)[:80]
                                    yield f"{TOOL_STATUS_PREFIX}{tu['name']} {input_preview}"

                                tool_results = await self._execute_tools(tool_uses, tool_context)

                                # Yield tool execution done markers
                                for i, tr in enumerate(tool_results):
                                    tool_name = tool_uses[i]["name"] if i < len(tool_uses) else "unknown"
                                    status = "error" if tr.is_error else "done"
                                    yield f"{TOOL_DONE_PREFIX}{tool_name} [{status}]"

                                assistant_msg = Message(
                                    role=Role.ASSISTANT,
                                    content=assistant_text if assistant_text else "[tool execution]",
                                    tool_uses=tool_uses,
                                )
                                self.state.messages.append(assistant_msg)

                                for tr in tool_results:
                                    self.state.messages.append(
                                        Message(
                                            role=Role.TOOL,
                                            content=tr.content,
                                            tool_use_id=tr.tool_use_id,
                                            is_error=tr.is_error,
                                        )
                                    )
                                    self.session.record_tool_result(
                                        tool_name=tr.tool_use_id.split(":")[0] if ":" in tr.tool_use_id else "unknown",
                                        tool_use_id=tr.tool_use_id,
                                        content=tr.content,
                                        is_error=tr.is_error,
                                    )

                            else:
                                # Unknown stop_reason (e.g. stop_sequence) — preserve assistant text
                                logger.warning("Unhandled stop_reason: %s", stop_reason)
                                if assistant_text:
                                    self.state.messages.append(Message(role=Role.ASSISTANT, content=assistant_text))
                                    self.session.record_assistant_message(assistant_text)
                                return

                        elif event.type == "error":
                            error_msg = event.error or "Unknown error"
                            if (
                                "utf-8" in error_msg.lower()
                                or "decode" in error_msg.lower()
                                or "encoding" in error_msg.lower()
                            ):
                                yield "\n[dim yellow]Streaming error, retrying without streaming...[/dim yellow]\n"
                                async for fallback_event in self._fallback_to_send(
                                    system_prompt, messages, tools, tool_context
                                ):
                                    yield fallback_event
                                return
                            yield f"\n[bold red]Provider error: {error_msg}[/bold red]"
                            return

                except UnicodeDecodeError:
                    yield "\n[dim yellow]Encoding error, retrying without streaming...[/dim yellow]\n"
                    try:
                        async for fallback_chunk in self._fallback_to_send(system_prompt, messages, tools, tool_context):
                            yield fallback_chunk
                    except Exception as fallback_err:
                        yield f"\n[bold red]Fallback also failed: {fallback_err}[/bold red]"
                    return

                except Exception as e:
                    error_str = str(e)
                    if _is_retryable_error(error_str):
                        fallback = self._next_fallback_model()
                        if fallback:
                            yield f"\n[dim yellow][Fallback: {fallback}][/dim yellow]\n"
                            self.settings.model.model = fallback
                            self.provider = self.create_provider_from_settings()
                            continue
                    yield f"\n[bold red]Error: {e}[/bold red]"
                    self.state.messages.append(Message(role=Role.ASSISTANT, content=f"Error occurred: {e}"))
                    return
        finally:
            self.settings.model.model = original_model
            self.provider = self.create_provider_from_settings()

    async def _fallback_to_send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
        context: ToolContext,
    ) -> AsyncGenerator[str, None]:
        """Fallback to non-streaming API call. Loops until model stops requesting tools."""
        max_fallback_turns = 10
        for _ in range(max_fallback_turns):
            try:
                result = await self.provider.send(system_prompt, self._to_provider_messages(), tools)
            except Exception as e:
                yield f"\n[bold red]Fallback error: {e}[/bold red]"
                return

            content = result.get("content", "")
            tool_uses = result.get("tool_uses", [])
            usage = result.get("usage", {})

            if content:
                yield content

            if usage:
                self.state.usage.input_tokens = usage.get("input_tokens", 0)
                self.state.usage.output_tokens = usage.get("output_tokens", 0)

            if tool_uses:
                assistant_msg = Message(
                    role=Role.ASSISTANT,
                    content=content if content else "[tool execution]",
                    tool_uses=tool_uses,
                )
                self.state.messages.append(assistant_msg)
                if content:
                    self.session.record_assistant_message(content, tool_uses)

                tool_results = await self._execute_tools(tool_uses, context)
                for tr in tool_results:
                    self.state.messages.append(
                        Message(role=Role.TOOL, content=tr.content, tool_use_id=tr.tool_use_id, is_error=tr.is_error)
                    )
                continue

            if content:
                self.state.messages.append(Message(role=Role.ASSISTANT, content=content))
                self.session.record_assistant_message(content)
            return

    async def _execute_tools(
        self,
        tool_uses: list[dict[str, Any]],
        context: ToolContext,
    ) -> list[ToolResult]:
        """Execute tool calls with permission checking."""
        results: list[ToolResult] = []
        serial_tools = []
        concurrent_tools = []

        for tu in tool_uses:
            tool = self.registry.get(tu["name"])
            if tool is None:
                results.append(
                    ToolResult(
                        tool_use_id=tu["id"],
                        content=f"Unknown tool: {tu['name']}",
                        is_error=True,
                    )
                )
                continue

            if tool.is_concurrency_safe:
                concurrent_tools.append((tu, tool))
            else:
                serial_tools.append((tu, tool))

        for tu, tool in serial_tools:
            result = await self._run_single_tool(tu, tool, context)
            results.append(result)
            from nerdvana_cli.core.context_reminder import RecentToolResult
            self._reminder.record_tool(
                RecentToolResult(
                    name=tu["name"],
                    args_summary=str(tu.get("input", ""))[:100],
                    preview=(result.content or "")[:200],
                    ok=not result.is_error,
                )
            )

        if concurrent_tools:
            tasks = [self._run_single_tool(tu, tool, context) for tu, tool in concurrent_tools]
            concurrent_results = await asyncio.gather(*tasks)
            results.extend(concurrent_results)
            from nerdvana_cli.core.context_reminder import RecentToolResult
            for (tu, _), result in zip(concurrent_tools, concurrent_results, strict=False):
                self._reminder.record_tool(
                    RecentToolResult(
                        name=tu["name"],
                        args_summary=str(tu.get("input", ""))[:100],
                        preview=(result.content or "")[:200],
                        ok=not result.is_error,
                    )
                )

        return results

    async def _run_single_tool(
        self,
        tool_use: dict[str, Any],
        tool: Any,
        context: ToolContext,
    ) -> ToolResult:
        """Run a single tool with permission check."""
        tool_input = tool_use["input"]
        tool_id = tool_use["id"]

        try:
            parsed_args = tool.parse_args(tool_input)
        except (TypeError, ValueError) as e:
            return ToolResult(tool_use_id=tool_id, content=f"Invalid tool input: {e}", is_error=True)

        perm_result = tool.check_permissions(parsed_args, context)
        if perm_result.behavior == PermissionBehavior.DENY:
            return ToolResult(tool_use_id=tool_id, content=f"Permission denied: {perm_result.message}", is_error=True)

        if perm_result.behavior == PermissionBehavior.ASK:
            return ToolResult(
                tool_use_id=tool_id,
                content=f"Permission required (auto-denied in current mode): {perm_result.message}",
                is_error=True,
            )

        from nerdvana_cli.core.hooks import HookContext, HookEvent
        tool_name = tool_use["name"]
        hook_ctx = HookContext(event=HookEvent.BEFORE_TOOL, tool_name=tool_name, tool_input=tool_input, settings=self.settings)
        for hr in self.hooks.fire(hook_ctx):
            if not hr.allow:
                return ToolResult(tool_use_id=tool_id, content=f"Blocked by hook: {hr.message}", is_error=True)

        validation_error = tool.validate_input(parsed_args, context)
        if validation_error:
            return ToolResult(tool_use_id=tool_id, content=f"Validation error: {validation_error}", is_error=True)

        try:
            result: ToolResult = await tool.call(parsed_args, context, can_use_tool=None)
            result.tool_use_id = tool_id
            result.content = tool.truncate_result(result.content)
            return result
        except Exception as e:
            return ToolResult(tool_use_id=tool_id, content=f"Tool execution error: {e}", is_error=True)
