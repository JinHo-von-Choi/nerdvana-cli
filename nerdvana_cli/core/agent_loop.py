"""Core agent loop — the heart of NerdVana CLI.

Orchestrator only (Phase 0A, T-0A-06): delegates tool execution to
ToolExecutor, recovery hooks to LoopHookEngine, iteration state to LoopState.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import AsyncGenerator, Callable
from typing import Any

from rich.console import Console

from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.compact import FALLBACK_PROMPT, CompactionState, ai_compact
from nerdvana_cli.core.loop_hooks import LoopHookEngine
from nerdvana_cli.core.loop_state import LoopState
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.types import Message, Role, SessionState

console = Console()
logger  = logging.getLogger(__name__)

TOOL_STATUS_PREFIX    = "\x00TOOL:"
TOOL_DONE_PREFIX      = "\x00TOOL_DONE:"
CONTEXT_USAGE_PREFIX  = "\x00CTX_USAGE:"
COMPACT_STATUS_PREFIX = "\x00COMPACT:"

_COMPLEXITY_SIGNALS: list[str] = [
    r"리팩터링|refactor", r"새로운\s+(기능|모듈|서비스|시스템)|new\s+(feature|module|service|system)",
    r"마이그레이션|migration", r"\d+개\s+(파일|클래스|모듈)|\d+\s+(files?|classes?|modules?)",
    r"아키텍처|architecture|전면\s+개편", r"처음부터|from\s+scratch",
]
_ULTRAWORK_PATTERN = re.compile(r"\b(ultrawork|ulw)\b", re.IGNORECASE)


def _needs_planning(prompt: str) -> bool:
    return sum(1 for p in _COMPLEXITY_SIGNALS if re.search(p, prompt, re.IGNORECASE)) >= 2


def _is_ultrawork(prompt: str) -> bool:
    return bool(_ULTRAWORK_PATTERN.search(prompt))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def estimate_messages_tokens(msgs: list[Any]) -> int:
    total = 0
    for m in msgs:
        total += estimate_tokens(m.content if isinstance(m.content, str) else json.dumps(m.content))
        if m.tool_uses:
            total += estimate_tokens(json.dumps(m.tool_uses))
    return total


def compact_messages(msgs: list[Any], max_tokens: int) -> list[Any]:
    if not msgs or estimate_messages_tokens(msgs) <= max_tokens:
        return msgs
    keep = min(10, len(msgs))
    recent = msgs[-keep:]
    budget = max_tokens - estimate_messages_tokens(recent)
    if budget <= 0:
        return msgs[-4:]
    early: list[Any] = []
    for m in msgs[:-keep]:
        cost = estimate_tokens(m.content if isinstance(m.content, str) else json.dumps(m.content))
        if budget - cost < 0:
            break
        early.append(m)
        budget -= cost
    dropped = len(msgs) - len(early) - len(recent)
    if dropped > 0:
        return early + [Message(role=Role.USER, content=f"[context compacted: {dropped} earlier messages removed to fit context window]")] + recent
    return early + recent


class AgentLoop:
    """Orchestrates provider calls, tool execution, and session recording."""

    def __init__(
        self,
        settings:            NerdvanaSettings,
        registry:            ToolRegistry,
        session:             SessionStorage | None = None,
        task_registry:       Any = None,
        team_registry:       Any = None,
        on_activity_change:  Callable[[ActivityState], None] | None = None,
        on_thinking_chunk:   Callable[[str], None] | None = None,
    ) -> None:
        self.settings             = settings
        self.registry             = registry
        self.session              = session or SessionStorage()
        self.state                = SessionState()
        self._task_registry       = task_registry
        self._team_registry       = team_registry
        self.console              = Console()
        self.activity_state       = ActivityState()
        self._on_activity_change  = on_activity_change
        self._on_thinking_chunk   = on_thinking_chunk
        self.last_thinking:  str  = ""
        from nerdvana_cli.core.builtin_hooks import (
            context_limit_recovery,
            json_parse_recovery,
            ralph_loop_check,
            session_start_context_injection,
            session_start_memory_hint,
        )
        from nerdvana_cli.core.checkpoint import CheckpointManager
        from nerdvana_cli.core.context_reminder import ContextReminder
        from nerdvana_cli.core.hooks import HookEngine, HookEvent
        from nerdvana_cli.core.skills import SkillLoader
        from nerdvana_cli.core.user_hooks import load_user_hooks
        self.hooks = HookEngine()
        self.hooks.register(HookEvent.SESSION_START, session_start_context_injection)
        self.hooks.register(HookEvent.SESSION_START, session_start_memory_hint)
        self.hooks.register(HookEvent.AFTER_API_CALL, context_limit_recovery)
        self.hooks.register(HookEvent.AFTER_API_CALL, ralph_loop_check)
        self.hooks.register(HookEvent.AFTER_TOOL, json_parse_recovery)
        self._user_hook_paths = load_user_hooks(self.hooks, settings)
        self.skill_loader = SkillLoader(project_dir=settings.cwd)
        self.skill_loader.load_all()
        self._active_skill: str | None = None
        self._reminder    = ContextReminder(cwd=settings.cwd or ".", max_recent=5)
        self._turn        = 0
        _cs = self.skill_loader.get_by_name("compress-context")
        self._compact_prompt   = _cs.body if _cs else FALLBACK_PROMPT
        self._compaction_state = CompactionState(max_failures=settings.session.compact_max_failures)
        self._session_started = False; self._sticky_session_context = ""  # noqa: E702
        self.provider         = self.create_provider_from_settings()
        _cp_cfg = getattr(settings, "checkpoint", None)
        _cp_enabled = _cp_cfg.enabled if _cp_cfg is not None else True
        _cp_max     = _cp_cfg.per_session_max if _cp_cfg is not None else 50
        self._checkpoint_manager = CheckpointManager(
            cwd             = settings.cwd or ".",
            session_id      = getattr(self.session, "session_id", "default"),
            per_session_max = _cp_max,
            enabled         = _cp_enabled,
        )
        self.tool_executor = ToolExecutor(
            registry            = self.registry,
            hooks               = self.hooks,
            settings            = self.settings,
            reminder            = self._reminder,
            checkpoint_manager  = self._checkpoint_manager,
        )
        self.loop_hook_engine = LoopHookEngine(hooks=self.hooks, settings=self.settings, registry=self.registry)
        from nerdvana_cli.core.activity_hooks import register_activity_hooks
        register_activity_hooks(self)

    def _set_activity(self, **kwargs: Any) -> None:
        """Mutate self.activity_state and notify subscribers."""
        for key, value in kwargs.items():
            setattr(self.activity_state, key, value)
        if self._on_activity_change is not None:
            import contextlib
            with contextlib.suppress(Exception):
                self._on_activity_change(self.activity_state)

    def create_provider_from_settings(self) -> AnthropicProvider | OpenAIProvider | GeminiProvider:
        pname = ProviderName(self.settings.model.provider) if self.settings.model.provider else None
        return create_provider(provider=pname, model=self.settings.model.model, api_key=self.settings.model.api_key,
            base_url=self.settings.model.base_url, max_tokens=self.settings.model.max_tokens, temperature=self.settings.model.temperature)

    def reset_session(self) -> None:
        self._session_started = False; self._sticky_session_context = ""; self.state.messages.clear()  # noqa: E702

    def build_system_prompt(self) -> str:
        from nerdvana_cli.core.prompts import build_system_prompt as _b
        return _b(tools=self.registry.all_tools(), parism_active=self.registry.get("Parism") is not None,
                  model=self.settings.model.model, provider=self.settings.model.provider, cwd=self.settings.cwd)

    def activate_skill(self, skill_body: str) -> None: self._active_skill = skill_body  # noqa: E704
    def deactivate_skill(self) -> None: self._active_skill = None  # noqa: E704

    def _next_fallback_model(self) -> str | None:
        fb = self.settings.model.fallback_models
        if not fb:
            return None
        try:
            idx = fb.index(self.settings.model.model) + 1
        except ValueError:
            idx = 0
        return fb[idx] if idx < len(fb) else None

    def _to_provider_messages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in self.state.messages:
            if msg.role == Role.USER:
                out.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                d = {"role": "assistant", "content": msg.content, **({"tool_uses": msg.tool_uses} if msg.tool_uses else {})}
                out.append(d)
            elif msg.role == Role.TOOL:
                out.append({"role": "tool", "content": msg.content, "tool_use_id": msg.tool_use_id or "", "is_error": msg.is_error})
        return out

    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """Submit a prompt and run the agent loop until completion."""
        if self.settings.session.planning_gate and _needs_planning(prompt):
            plan = await self._run_plan_agent(prompt)
            if plan:
                yield f"\n[Plan]\n{plan}\n[/Plan]\n"
                self.state.messages.append(Message(role=Role.USER, content=f"[Auto-generated plan]\n{plan}"))

        original_et = self.settings.model.extended_thinking
        if _is_ultrawork(prompt):
            self.settings.model.extended_thinking = True
            yield "[dim cyan][Ultrawork mode: extended thinking ON][/dim cyan]\n"

        self._turn += 1
        reminder = self._reminder.build(turn=self._turn)
        if reminder:
            self.state.messages.append(Message(role=Role.USER, content=reminder))
        self.state.messages.append(Message(role=Role.USER, content=prompt))
        self.session.record_user_message(prompt)
        system_prompt = self.build_system_prompt()
        tools = self.registry.all_tools()
        if not self._session_started:
            self._session_started = True
            from nerdvana_cli.core.context_snapshot import collect_snapshot, format_snapshot
            from nerdvana_cli.core.hooks import HookContext, HookEvent
            _p: list[str] = []
            try:
                _s = format_snapshot(await collect_snapshot(self.settings.cwd or "."))
                if _s.strip():
                    _p.append(_s)
            except Exception:  # noqa: BLE001
                pass
            for _hr in self.hooks.fire(HookContext(event=HookEvent.SESSION_START, settings=self.settings, tools=tools)):
                if _hr.system_prompt_append:
                    _p.append(_hr.system_prompt_append)
                for _m in _hr.inject_messages:
                    if _m.get("content"):
                        _p.append(str(_m["content"]))
            if _p:
                self._sticky_session_context = "\n\n".join(_p)
        if self._sticky_session_context:
            system_prompt += f"\n\n{self._sticky_session_context}"
        if self._active_skill:
            system_prompt += f"\n\n# Active Skill\n{self._active_skill}"
        try:
            async for event in self._loop(system_prompt, tools):
                yield event
        finally:
            self.settings.model.extended_thinking = original_et

    async def _run_plan_agent(self, prompt: str) -> str:
        from nerdvana_cli.core.subagent import SubagentConfig, run_subagent
        from nerdvana_cli.tools.registry import create_subagent_registry
        child = self.settings.model_copy(deep=True)
        child.session.planning_gate = False
        reg = create_subagent_registry(settings=child, allowed_tools=["Glob", "Grep", "FileRead", "Bash"])
        cfg = SubagentConfig(agent_id="plan_agent", name="Plan", max_turns=20,
                             prompt=f"Create an implementation plan for the following task:\n\n{prompt}",
                             settings=child, registry=reg)
        try:
            return await run_subagent(cfg, asyncio.Event())
        except Exception as exc:  # noqa: BLE001
            return f"[plan agent error] {exc}"

    async def _loop(self, system_prompt: str, tools: list[Any]) -> AsyncGenerator[str, None]:
        tool_ctx   = ToolContext(cwd=self.settings.cwd, task_registry=self._task_registry, team_registry=self._team_registry)
        state      = LoopState(iteration=0, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id=self.session.session_id)
        orig_model = self.settings.model.model
        try:
            while True:
                state = state.evolve(iteration=state.iteration + 1)
                if state.iteration > self.settings.session.max_turns:
                    yield f"\n[bold yellow]Max turns ({self.settings.session.max_turns}) reached.[/bold yellow]"
                    return

                max_ctx  = self.settings.session.max_context_tokens
                thr      = int(max_ctx * self.settings.session.compact_threshold)
                cur_toks = estimate_messages_tokens(self.state.messages)
                state    = state.evolve(token_budget_used=cur_toks)

                if cur_toks > thr:
                    before = len(self.state.messages)
                    if not self._compaction_state.is_circuit_open:
                        yield f"{COMPACT_STATUS_PREFIX}compressing ({cur_toks} tokens)..."
                        summary = await ai_compact(self.state.messages, self.provider, self._compaction_state, prompt=self._compact_prompt)
                        if summary is not None:
                            recent = self.state.messages[-4:]
                            while recent and recent[0].role == Role.TOOL:
                                recent = recent[1:]
                            self.state.messages = [summary] + recent
                            self.session.record_compaction(tokens_before=cur_toks, messages_before=before, strategy="ai")
                            yield f"{COMPACT_STATUS_PREFIX}done"
                        else:
                            self.state.messages = compact_messages(self.state.messages, thr)
                            self.session.record_compaction(tokens_before=cur_toks, messages_before=before, strategy="naive")
                    else:
                        self.state.messages = compact_messages(self.state.messages, thr)
                        self.session.record_compaction(tokens_before=cur_toks, messages_before=before, strategy="naive")

                yield f"{CONTEXT_USAGE_PREFIX}{min(100, int(cur_toks / max_ctx * 100)) if max_ctx > 0 else 0}"
                if self.settings.verbose:
                    self.console.print(f"[dim]Turn {state.iteration} — {len(self.state.messages)} messages[/dim]")

                messages = self._to_provider_messages()
                try:
                    asst_text       = ""
                    thinking_buffer = ""
                    tool_uses: list[dict[str, Any]] = []

                    async for ev in self.provider.stream(system_prompt, messages, tools):
                        if ev.type == "content_delta":
                            self._set_activity(
                                phase="streaming",
                                label=f"Streaming from {self.settings.model.model}",
                            )
                            asst_text += ev.content
                            yield ev.content
                        elif ev.type == "thinking_delta":
                            thinking_buffer += ev.thinking
                            self._set_activity(phase="thinking", label="Thinking...")
                            if self._on_thinking_chunk is not None:
                                import contextlib as _cl
                                with _cl.suppress(Exception):
                                    self._on_thinking_chunk(thinking_buffer)
                        elif ev.type == "tool_use_complete":
                            tool_uses.append({"id": ev.tool_use_id or f"call_{len(tool_uses)}",
                                              "name": ev.tool_name, "input": ev.tool_input_complete or {}})
                        elif ev.type == "usage" and ev.usage:
                            self.state.usage.input_tokens  = ev.usage.get("input_tokens", 0)
                            self.state.usage.output_tokens = ev.usage.get("output_tokens", 0)
                        elif ev.type == "done":
                            stop = ev.stop_reason
                            if stop == "max_tokens":
                                from nerdvana_cli.core.hooks import HookContext, HookEvent
                                ctx = HookContext(event=HookEvent.AFTER_API_CALL, settings=self.settings,
                                                  tools=self.registry.all_tools(), messages=self.state.messages,
                                                  stop_reason="max_tokens", extra={"agent_loop": self})
                                recovered = False
                                for hr in self.hooks.fire(ctx):
                                    for msg in hr.inject_messages:
                                        self.state.messages.append(Message(role=Role.USER, content=msg["content"]))
                                        recovered = True
                                if not recovered:
                                    yield "\n\n[bold red]Max tokens reached.[/bold red]"
                                    return
                                break
                            elif stop == "end_turn":
                                self.last_thinking = thinking_buffer
                                if asst_text:
                                    self.state.messages.append(Message(role=Role.ASSISTANT, content=asst_text))
                                    self.session.record_assistant_message(asst_text)
                                from nerdvana_cli.core.hooks import HookContext, HookEvent
                                _et_ctx = HookContext(
                                    event       = HookEvent.AFTER_API_CALL,
                                    settings    = self.settings,
                                    tools       = self.registry.all_tools(),
                                    messages    = self.state.messages,
                                    stop_reason = "end_turn",
                                    extra       = {"agent_loop": self, "asst_text": asst_text},
                                )
                                _et_injected = False
                                for _et_hr in self.hooks.fire(_et_ctx):
                                    for _et_msg in _et_hr.inject_messages:
                                        self.state.messages.append(Message(role=Role.USER, content=_et_msg["content"]))
                                        _et_injected = True
                                if not _et_injected:
                                    self._set_activity(phase="idle", label="Ready")
                                    return
                                break
                            elif stop == "tool_use" and tool_uses:
                                if asst_text:
                                    self.session.record_assistant_message(asst_text, tool_uses)
                                for tu in tool_uses:
                                    yield f"{TOOL_STATUS_PREFIX}{tu['name']} {json.dumps(tu['input'], ensure_ascii=False)[:80]}"
                                results = await self.tool_executor.run_batch(tool_uses, tool_ctx)
                                for i, tr in enumerate(results):
                                    yield f"{TOOL_DONE_PREFIX}{tool_uses[i]['name'] if i < len(tool_uses) else 'unknown'} [{'error' if tr.is_error else 'done'}]"
                                self.state.messages.append(Message(role=Role.ASSISTANT,
                                    content=asst_text if asst_text else "[tool execution]", tool_uses=tool_uses))
                                for tr in results:
                                    self.state.messages.append(Message(role=Role.TOOL, content=tr.content, tool_use_id=tr.tool_use_id, is_error=tr.is_error))
                                    self.session.record_tool_result(
                                        tool_name=tr.tool_use_id.split(":")[0] if ":" in tr.tool_use_id else "unknown",
                                        tool_use_id=tr.tool_use_id, content=tr.content, is_error=tr.is_error)
                            else:
                                logger.warning("Unhandled stop_reason: %s", stop)
                                if asst_text:
                                    self.state.messages.append(Message(role=Role.ASSISTANT, content=asst_text))
                                    self.session.record_assistant_message(asst_text)
                                return
                        elif ev.type == "error":
                            err = ev.error or "Unknown error"
                            if any(k in err.lower() for k in ("utf-8", "decode", "encoding")):
                                yield "\n[dim yellow]Streaming error, retrying without streaming...[/dim yellow]\n"
                                async for c in self._fallback_to_send(system_prompt, messages, tools, tool_ctx):
                                    yield c
                                return
                            yield f"\n[bold red]Provider error: {err}[/bold red]"
                            return

                except UnicodeDecodeError:
                    yield "\n[dim yellow]Encoding error, retrying without streaming...[/dim yellow]\n"
                    try:
                        async for c in self._fallback_to_send(system_prompt, messages, tools, tool_ctx):
                            yield c
                    except Exception as fe:
                        yield f"\n[bold red]Fallback also failed: {fe}[/bold red]"
                    return
                except Exception as e:
                    if self.loop_hook_engine._is_retryable_error(e):
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
            self.settings.model.model = orig_model
            self.provider             = self.create_provider_from_settings()

    async def _fallback_to_send(
        self, system_prompt: str, messages: list[dict[str, Any]], tools: list[Any], context: ToolContext,
    ) -> AsyncGenerator[str, None]:
        """Non-streaming fallback when provider streaming fails."""
        for _ in range(10):
            try:
                result = await self.provider.send(system_prompt, self._to_provider_messages(), tools)
            except Exception as e:
                yield f"\n[bold red]Fallback error: {e}[/bold red]"
                return

            content   = result.get("content", "")
            tool_uses = result.get("tool_uses", [])
            usage     = result.get("usage", {})
            if content:
                yield content
            if usage:
                self.state.usage.input_tokens  = usage.get("input_tokens", 0)
                self.state.usage.output_tokens = usage.get("output_tokens", 0)
            if tool_uses:
                self.state.messages.append(Message(role=Role.ASSISTANT, content=content if content else "[tool execution]", tool_uses=tool_uses))
                if content:
                    self.session.record_assistant_message(content, tool_uses)
                for tr in await self.tool_executor.run_batch(tool_uses, context):
                    self.state.messages.append(Message(role=Role.TOOL, content=tr.content, tool_use_id=tr.tool_use_id, is_error=tr.is_error))
                continue
            if content:
                self.state.messages.append(Message(role=Role.ASSISTANT, content=content))
                self.session.record_assistant_message(content)
            return
