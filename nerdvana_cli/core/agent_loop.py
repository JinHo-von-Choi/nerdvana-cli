"""Core agent loop: the heart of NerdVana CLI.

Orchestrator only: tool execution goes to ToolExecutor, recovery hooks to
LoopHookEngine, iteration state to LoopState, token and cost accounting to
RunLimits, model switching to ModelFailover, the goal check to GoalGate,
typed-ahead text to InputQueue and rewinding to Rewinder.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from nerdvana_cli.core import signals
from nerdvana_cli.core.activity_hooks import register_activity_hooks
from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.advisor import Advisor
from nerdvana_cli.core.budget import Budget
from nerdvana_cli.core.builtin_hooks import (
    DirectoryRuleInjector,
    context_limit_recovery,
    json_parse_recovery,
    ralph_loop_check,
    session_start_context_injection,
    session_start_memory_hint,
)
from nerdvana_cli.core.cancellation import race_abort, until_interrupted
from nerdvana_cli.core.checkpoint import CheckpointManager
from nerdvana_cli.core.compact import (
    FALLBACK_PROMPT,
    CompactionState,
    ai_compact,
    compact_messages,
    drop_orphan_tool_results,
)
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context_budget import ContextBudget
from nerdvana_cli.core.context_reminder import ContextReminder
from nerdvana_cli.core.goal import Goal
from nerdvana_cli.core.goal_gate import GoalGate
from nerdvana_cli.core.hooks.command_hooks import load_command_hooks
from nerdvana_cli.core.hooks.hooks import HookContext, HookEngine, HookEvent
from nerdvana_cli.core.hooks.user_hooks import load_user_hooks
from nerdvana_cli.core.images import prompt_content, transcript_text
from nerdvana_cli.core.input_queue import InputQueue, interrupted_results
from nerdvana_cli.core.loop_context import (
    COMPACT_STATUS_PREFIX,
    background_reports,
    new_provider,
    open_todos_note,
    prepare_tools,
    provider_messages,
    session_start_context,
)
from nerdvana_cli.core.loop_hooks import LoopHookEngine, hook_injection_messages
from nerdvana_cli.core.loop_state import LoopFlow, LoopState, LoopTurn
from nerdvana_cli.core.loop_support import classifier_feed, with_compaction_hooks
from nerdvana_cli.core.model_failover import ModelFailover
from nerdvana_cli.core.observation_mask import mask_observations
from nerdvana_cli.core.phase_effort import PhaseEffort
from nerdvana_cli.core.plan_gate import plan_for
from nerdvana_cli.core.policy import PermissionPolicy
from nerdvana_cli.core.provider_recovery import ProviderCallError, RecoveryPlanner
from nerdvana_cli.core.rewind import Rewinder
from nerdvana_cli.core.run_limits import RunLimits
from nerdvana_cli.core.sandbox import SandboxPolicy
from nerdvana_cli.core.server_compaction import ServerCompaction
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.skills import SkillLoader
from nerdvana_cli.core.stream_guard import guarded_stream
from nerdvana_cli.core.subagent_config import LoopFactories
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, CallOrigin, PricingTable
from nerdvana_cli.core.todos import CONTINUE, STALLED, TodoGuard
from nerdvana_cli.core.tool import AskUserCallback, ConfirmCallback, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.core.tool_ids import collect_tool_use_ids, repair_tool_ids
from nerdvana_cli.core.tool_index import ToolIndex
from nerdvana_cli.providers.errors import OTHER, ProviderFailure
from nerdvana_cli.types import Message, Role, SessionState

if TYPE_CHECKING:
    # Provider classes are optional-extras imports. They are only needed for
    # type hints and the factory; runtime instantiation goes through
    # `create_provider`, which performs lazy imports inside its body. Keeping
    # these imports under TYPE_CHECKING lets the CLI boot in a minimal
    # install where no provider SDK is present.
    from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
    from nerdvana_cli.providers.gemini_provider import GeminiProvider
    from nerdvana_cli.providers.openai_provider import OpenAIProvider

console = Console()
logger  = logging.getLogger(__name__)

TOOL_STATUS_PREFIX    = "\x00TOOL:"
TOOL_DONE_PREFIX      = "\x00TOOL_DONE:"
CONTEXT_USAGE_PREFIX  = "\x00CTX_USAGE:"

# End-of-turn hooks (unfinished-marker checks and the like) may keep the loop
# going at most this many times per user prompt.
_MAX_END_TURN_NUDGES = 3

_ULTRAWORK_PATTERN = re.compile(r"\b(ultrawork|ulw)\b", re.IGNORECASE)


def _is_ultrawork(prompt: str) -> bool:
    return bool(_ULTRAWORK_PATTERN.search(prompt))


_WRAP_UP = (
    "[Turn budget] {used} of {limit} turns are used. Stop exploring now and answer with what you have found; "
    "say plainly what you could not find."
)


class AgentLoop:
    """Orchestrates provider calls, tool execution, and session recording."""

    _tool_index: ToolIndex | None
    last_stop:   str
    turns_used:  int

    def __init__(
        self,
        settings:            NerdvanaSettings,
        registry:            ToolRegistry,
        session:             SessionStorage | None = None,
        task_registry:       Any = None,
        on_activity_change:  Callable[[ActivityState], None] | None = None,
        on_thinking_chunk:   Callable[[str], None] | None = None,
        analytics_writer:    AnalyticsWriter | None = None,
        pricing_table:       PricingTable | None = None,
        role_prompt:         str = "",
        on_ask_user:         AskUserCallback | None = None,
        on_confirm:          ConfirmCallback | None = None,
        origin:              CallOrigin | None = None,
        factories:           LoopFactories | None = None,
    ) -> None:
        self._init_telemetry(origin)
        self._factories           = factories or LoopFactories()
        self.settings             = settings
        self.registry             = registry
        self.session              = session or SessionStorage()
        self.state                = SessionState()
        self._task_registry       = task_registry
        self.console              = Console()
        self.activity_state       = ActivityState()
        self._on_activity_change  = on_activity_change
        self._on_thinking_chunk   = on_thinking_chunk
        self._on_ask_user         = on_ask_user
        self._on_confirm          = on_confirm
        self.last_thinking:  str  = ""
        self._init_hooks(settings)
        self._init_history(settings, registry, role_prompt)
        self.provider             = self.create_provider_from_settings()
        self._init_execution(settings, pricing_table, analytics_writer)
        self.input_queue          = InputQueue(settings.session.steer_mode)
        self.last_stop            = "completed"
        self.turns_used           = 0
        self.goal_gate            = GoalGate(self)
        self.advisor              = Advisor(self)
        self.phase_effort         = PhaseEffort(self)
        self.server_compaction    = ServerCompaction(self)
        self.failover             = ModelFailover(self)
        self.rewinder             = Rewinder(self)
        self.loop_hook_engine     = LoopHookEngine(hooks=self.hooks, settings=self.settings, registry=self.registry)
        register_activity_hooks(self)

    def _init_telemetry(self, origin: CallOrigin | None) -> None:
        """State for attributing requests and counting trouble: who this loop is and what it has seen."""
        self.origin                = origin or CallOrigin()
        self.usage_listener:       Callable[[dict[str, Any]], None] | None = None
        self._last_tool            = ""
        self._signals: Counter[str] = Counter()
        # Turn at which the model is told to stop exploring and answer; 0 = never. Sub-agents set it.
        self.wrap_up_at            = 0
        self._tool_index           = None

    def _init_hooks(self, settings: NerdvanaSettings) -> None:
        """The hook bus with the built-in recovery hooks, then the user's and the project's own."""
        self.hooks = HookEngine()
        self.hooks.register(HookEvent.SESSION_START, session_start_context_injection)
        self.hooks.register(HookEvent.SESSION_START, session_start_memory_hint)
        self.hooks.register(HookEvent.AFTER_API_CALL, context_limit_recovery)
        self.hooks.register(HookEvent.AFTER_API_CALL, ralph_loop_check)
        self.hooks.register(HookEvent.AFTER_TOOL, json_parse_recovery)
        self._dir_rules = DirectoryRuleInjector()
        self.hooks.register(HookEvent.AFTER_TOOL, self._dir_rules.handle)
        self._user_hook_paths = load_user_hooks(self.hooks, settings)
        self._command_hooks   = load_command_hooks(self.hooks, settings)

    def _init_history(self, settings: NerdvanaSettings, registry: ToolRegistry, role_prompt: str) -> None:
        """Skills, reminders, compaction and the per-session context the history is built with."""
        self.skill_loader = self._skill_loader_for(registry, settings)
        self._active_skill: str | None = None
        self._role_prompt = role_prompt
        self._reminder    = ContextReminder(cwd=settings.cwd or ".", max_recent=5)
        self._turn        = 0
        _cs = self.skill_loader.get_by_name("compress-context")
        self._compact_prompt   = _cs.body if _cs else FALLBACK_PROMPT
        self._compaction_state = CompactionState(max_failures=settings.session.compact_max_failures)
        self._context_budget   = ContextBudget()
        self._todo_guard       = TodoGuard()
        self._end_turn_nudges  = 0
        self._session_started = False; self._sticky_session_context = ""  # noqa: E702
        self._git_snapshot: dict[str, str] | None = None
        self._session_ended   = False

    def _init_execution(self, settings: NerdvanaSettings, pricing_table: PricingTable | None, analytics_writer: AnalyticsWriter | None) -> None:
        """Checkpoints, accounting, the permission policy and the tool executor."""
        _cp_cfg = getattr(settings, "checkpoint", None)
        self._checkpoint_manager = CheckpointManager(
            cwd             = settings.cwd or ".",
            session_id      = getattr(self.session, "session_id", "default"),
            per_session_max = _cp_cfg.per_session_max if _cp_cfg is not None else 50,
            enabled         = _cp_cfg.enabled if _cp_cfg is not None else True,
        )
        self.limits            = RunLimits(settings, self._signals, pricing_table, analytics_writer)
        self._analytics_writer = self.limits.analytics_writer
        self._analytics_writer.start_session(
            session_id = self.session.session_id,
            mode       = settings.model.provider or None,
            context    = settings.cwd or None,
        )
        self.policy        = PermissionPolicy.from_settings(settings)
        self.tool_executor = ToolExecutor(
            registry            = self.registry,
            hooks               = self.hooks,
            settings            = settings,
            reminder            = self._reminder,
            checkpoint_manager  = self._checkpoint_manager,
            analytics_writer    = self._analytics_writer,
            policy              = self.policy,
        )

    def _set_activity(self, **kwargs: Any) -> None:
        """Mutate self.activity_state and notify subscribers."""
        for key, value in kwargs.items():
            setattr(self.activity_state, key, value)
        if self._on_activity_change is not None:
            try:
                self._on_activity_change(self.activity_state)
            except Exception:  # noqa: BLE001
                # A broken indicator must never abort a turn, but a failure that
                # leaves no trace hides itself for as long as nobody looks.
                logger.warning("activity change callback failed", exc_info=True)

    def _apply_usage(self, usage: dict[str, int], messages_sent: int | None) -> None:
        """Fold one request's reported *usage* into the session's counters.

        ``input_tokens`` is the whole prompt; ``cache_read_tokens`` and
        ``cache_write_tokens`` say how much of it was served from or written to the
        provider's prompt cache. With *messages_sent* the figure also anchors the
        context-window estimate.
        """
        current = self.state.usage
        current.input_tokens          = usage.get("input_tokens", 0)
        current.output_tokens         = usage.get("output_tokens", 0)
        current.cache_read_tokens     = usage.get("cache_read_tokens", 0)
        current.cache_creation_tokens = usage.get("cache_write_tokens", 0)
        origin = replace(self.origin, turn=self.turns_used, last_tool=self._last_tool)
        cost   = self.limits.record(usage, origin, self.signal_summary)
        if self.usage_listener is not None:
            self.usage_listener({
                **usage, "provider": self.settings.model.provider, "model": self.settings.model.model,
                "agent_type": origin.agent_type, "turn": origin.turn, "last_tool": origin.last_tool, "cost_usd": cost,
            })
        if messages_sent is not None:
            self._context_budget.record_usage(current.input_tokens, messages_sent)

    @property
    def budget(self) -> Budget:
        """The session's cost limit as shared with its sub-agents (rebuilt when the limit changes)."""
        return self.limits.budget

    @property
    def goal(self) -> Goal | None:
        """The goal this session is held to, loaded from its file the first time it is asked for."""
        return self.goal_gate.goal

    def set_goal(self, goal: Goal | None) -> None:
        """Hold the session to *goal* (None drops it) and save the change."""
        self.goal_gate.set_goal(goal)

    def verification_summary(self) -> dict[str, Any] | None:
        """How the goal stands, for the run result; None when the session has no goal."""
        return self.goal_gate.summary()

    def _sandbox_policy(self) -> SandboxPolicy:
        """The sandbox policy of this loop, from its settings."""
        return SandboxPolicy.from_config(self.settings.sandbox, self.settings.secrets.proxy_credentials)

    def signal_summary(self) -> dict[str, int]:
        """How often each kind of trouble came up in this session (see ``core/signals.py``)."""
        return signals.merge(self._signals, self.tool_executor.signals)

    def usage_summary(self) -> dict[str, int]:
        """Token totals for every provider request made so far in this session."""
        return self.limits.usage_summary()

    def session_cost_usd(self) -> float:
        """Estimated USD cost of every provider request this session made itself, each priced for the model that served it."""
        return self.limits.cost_usd

    def total_cost_usd(self) -> float:
        """What the session spent: its own requests plus what its finished sub-agents spent."""
        return self.limits.total_cost_usd()

    def absorb_subagent(self, usage: dict[str, int], signal_counts: dict[str, int]) -> None:
        """Add a finished sub-agent's token totals and signal counts to this session's own."""
        self.limits.absorb_subagent(usage, signal_counts)

    async def _plan_first(self, prompt: str) -> AsyncGenerator[str, None]:
        """When the planning gate asks for it, have a plan drafted and put in front of the model."""
        plan = await plan_for(prompt, self.settings, self._factories)
        if plan:
            yield f"\n[Plan]\n{plan}\n[/Plan]\n"
            self.state.messages.append(Message(role=Role.USER, content=f"[Auto-generated plan]\n{plan}"))

    def rewind(self, prompts: int = 1) -> str:
        """Go back before the last *prompts* prompts: drop their messages and undo the edits they made."""
        return self.rewinder.rewind(prompts)

    def _fire_before_api_call(self, tools: list[Any]) -> bool:
        """Run BEFORE_API_CALL handlers just before a provider request goes out.

        Returns True when a handler injected messages, in which case the caller
        rebuilds the provider payload so the injection reaches this same call.
        """
        ctx = HookContext(
            event    = HookEvent.BEFORE_API_CALL,
            settings = self.settings,
            tools    = tools,
            messages = self.state.messages,
            extra    = {"agent_loop": self},
        )
        injected = False
        for hr in self.hooks.fire(ctx):
            for msg in hr.inject_messages:
                content = msg.get("content")
                if not content:
                    continue
                self.state.messages.append(Message(role=Role.USER, content=str(content)))
                injected = True
        return injected

    def create_provider_from_settings(self) -> AnthropicProvider | OpenAIProvider | GeminiProvider:
        return new_provider(self.settings.model)

    def queue_input(self, text: str, interrupt: bool | None = None) -> bool:
        """Hold text typed while the agent works for its next step; True when it also interrupts the step in progress."""
        return self.input_queue.put(text, interrupt)

    def has_queued_input(self) -> bool:
        """True when typed-ahead text is waiting for the model."""
        return self.input_queue.pending()

    def take_queued_input(self) -> list[str]:
        """Return and clear the typed-ahead text."""
        return self.input_queue.take()

    def _inject_queued_input(self) -> None:
        """Put typed-ahead text into the history as user messages, in the order typed."""
        for text in self.input_queue.take():
            self.state.messages.append(Message(role=Role.USER, content=text))
            self.session.record_user_message(text)

    def restore_history(self) -> int:
        """Load this session's recorded conversation into the live history.

        Returns the number of messages restored.
        """
        restored = self.session.load_messages()
        self.state.messages.extend(restored)
        return len(restored)

    def close_session(self, reason: str = "exit") -> None:
        """Fire SESSION_END once for a session that has started."""
        if not self._session_started or self._session_ended:
            return
        self._session_ended = True
        self.hooks.fire(HookContext(
            event    = HookEvent.SESSION_END,
            settings = self.settings,
            messages = self.state.messages,
            extra    = {"reason": reason, "session_id": self.session.session_id},
        ))

    def reset_session(self) -> None:
        self.input_queue.take()
        self.close_session("reset")
        self._session_started = False; self._sticky_session_context = ""; self.state.messages.clear()  # noqa: E702
        self._git_snapshot = None
        self.rewinder.marks.clear()
        self._dir_rules.reset()
        self._context_budget.reset()
        self.skill_loader.reset_activations()

    def _prepare_tools(self) -> list[Any]:
        """The tools this run may use, with MCP tools deferred behind ToolSearch when their declarations are large."""
        return prepare_tools(self)

    def _declared(self, tools: list[Any]) -> list[Any]:
        """The tools to declare in the next request."""
        return self._tool_index.declared(tools) if self._tool_index else tools

    def build_system_prompt(self) -> str:
        from nerdvana_cli.core.prompts import build_system_prompt as _b
        from nerdvana_cli.core.prompts import git_snapshot
        if self._git_snapshot is None:
            self._git_snapshot = git_snapshot(self.settings.cwd)
        return _b(tools=[t for t in self.registry.all_tools() if self.policy.is_visible(t.name)], parism_active=self.registry.get("Parism") is not None,
                  model=self.settings.model.model, provider=self.settings.model.provider, cwd=self.settings.cwd,
                  active_tool_mode=bool(self.settings.model.extended_thinking),
                  project_doc_max_tokens=self.settings.session.project_doc_max_tokens,
                  deferred_tools=self._tool_index.index_lines() if self._tool_index else None, git_info=self._git_snapshot)

    def activate_skill(self, skill_body: str) -> None: self._active_skill = skill_body  # noqa: E704
    def deactivate_skill(self) -> None: self._active_skill = None  # noqa: E704

    def _to_provider_messages(self) -> list[dict[str, Any]]:
        """The history in the form the provider takes."""
        return provider_messages(self.state.messages)

    async def run(self, prompt: str, images: list[dict[str, Any]] | None = None) -> AsyncGenerator[str, None]:
        """Submit a prompt (with image blocks, see core/images.py) and run the agent loop until completion."""
        self.rewinder.mark()
        async for note in self._plan_first(prompt):
            yield note

        original_et = self.settings.model.extended_thinking
        if _is_ultrawork(prompt):
            self.settings.model.extended_thinking = True
            self.provider = self.create_provider_from_settings()
            yield "[dim cyan][Ultrawork mode: autonomous tool-use guidance ON][/dim cyan]\n"

        self._turn += 1
        self.last_stop  = "completed"
        self.turns_used = 0
        self._todo_guard.reset()
        self._end_turn_nudges = 0
        reminder = self._reminder.build(turn=self._turn)
        if reminder:
            self.state.messages.append(Message(role=Role.USER, content=reminder))
        self.state.messages.append(Message(role=Role.USER, content=prompt_content(prompt, images)))
        self.session.record_user_message(transcript_text(prompt, images))
        tools         = self._prepare_tools()
        system_prompt = self.build_system_prompt()
        if not self._session_started:
            self._session_started = True
            self._session_ended   = False
            self._sticky_session_context = await session_start_context(self.settings, self.hooks, tools)
        if self._sticky_session_context:
            system_prompt += f"\n\n{self._sticky_session_context}"
        if self._role_prompt:
            system_prompt += f"\n\n# Agent Role\n{self._role_prompt}"
        if self._active_skill:
            system_prompt += f"\n\n# Active Skill\n{self._active_skill}"
        try:
            async for event in self._loop(system_prompt, tools):
                yield event
        finally:
            if self.settings.model.extended_thinking != original_et:
                self.settings.model.extended_thinking = original_et
                self.provider = self.create_provider_from_settings()
            self.limits.record_session_totals()

    @with_compaction_hooks
    async def _maybe_compact_messages(self, cur_toks: int, thr: int) -> AsyncGenerator[str, None]:
        """Compress message history when the token threshold is exceeded.

        Yields ``COMPACT_STATUS_PREFIX`` status strings for the UI and mutates ``self.state.messages`` in place; falls back to naive truncation when AI compaction fails or the circuit is open.
        """
        before = len(self.state.messages)
        self.rewinder.marks.clear()   # compaction rewrites the history the marks point into
        self._signals[signals.COMPACTION] += 1
        if not self._compaction_state.is_circuit_open:
            yield f"{COMPACT_STATUS_PREFIX}compressing ({cur_toks} tokens)..."
            summary = await ai_compact(
                self.state.messages, self.provider, self._compaction_state, prompt=self._compact_prompt
            )
            if summary is not None:
                recent = self.state.messages[-4:]
                while recent and recent[0].role == Role.TOOL:
                    recent = recent[1:]
                self.state.messages = [summary] + recent
                self.session.record_compaction(tokens_before=cur_toks, messages_before=before, strategy="ai")
                self._context_budget.reset()
                self.state.messages.extend(open_todos_note(self.session.session_id))
                yield f"{COMPACT_STATUS_PREFIX}done"
                return
        self.state.messages = drop_orphan_tool_results(compact_messages(self.state.messages, thr))
        self.session.record_compaction(tokens_before=cur_toks, messages_before=before, strategy="naive")
        self._context_budget.reset()
        self.state.messages.extend(open_todos_note(self.session.session_id))

    def _fire_after_api_call(self, stop_reason: str, extra: dict[str, Any]) -> bool:
        """Run AFTER_API_CALL hooks for *stop_reason*; True when one of them injected messages."""
        ctx = HookContext(
            event       = HookEvent.AFTER_API_CALL,
            settings    = self.settings,
            tools       = self.registry.all_tools(),
            messages    = self.state.messages,
            stop_reason = stop_reason,
            extra       = {"agent_loop": self, **extra},
        )
        injected = False
        for hr in self.hooks.fire(ctx):
            for msg in hr.inject_messages:
                self.state.messages.append(Message(role=Role.USER, content=msg["content"]))
                injected = True
        return injected

    def _handle_max_tokens_stop(self) -> bool:
        """Run AFTER_API_CALL hooks with stop_reason='max_tokens'.

        Returns True if a hook injected recovery messages (caller should
        continue the loop); False if no recovery is available (caller should
        terminate).
        """
        return self._fire_after_api_call("max_tokens", {})

    def _handle_end_turn_stop(self, asst_text: str, thinking_buffer: str, provider_blocks: list[dict[str, Any]] | None = None) -> bool:
        """Finalize the assistant turn and run AFTER_API_CALL hooks.

        Persists the assistant message, fires hooks. Returns True if a hook
        injected continuation messages (caller should keep looping); False if
        the turn is complete and the caller should stop.
        """
        self.last_thinking = thinking_buffer
        if asst_text:
            self.state.messages.append(Message(role=Role.ASSISTANT, content=asst_text, provider_blocks=list(provider_blocks or [])))
            self.session.record_assistant_message(asst_text, provider_blocks=provider_blocks)
        return self._fire_after_api_call("end_turn", {"asst_text": asst_text})

    async def _handle_tool_use_stop(
        self,
        asst_text:  str,
        tool_uses:  list[dict[str, Any]],
        tool_ctx:   ToolContext,
        provider_blocks: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Execute the requested tool calls and append results to history.

        Yields ``TOOL_STATUS_PREFIX`` / ``TOOL_DONE_PREFIX`` markers that the UI
        consumes; mutates ``self.state.messages`` and records each tool result
        in the session log.
        """
        self.session.record_assistant_message(asst_text, tool_uses, provider_blocks)
        for tu in tool_uses:
            yield f"{TOOL_STATUS_PREFIX}{tu['name']} {json.dumps(tu['input'], ensure_ascii=False)[:80]}"
        names_by_id = {tu["id"]: tu["name"] for tu in tool_uses}
        raced   = await race_abort(self.tool_executor.run_batch(tool_uses, tool_ctx), self.input_queue.interrupt, patience=self.input_queue.patience)
        results = interrupted_results(tool_uses) if raced.aborted else raced.value or []
        self._last_tool = tool_uses[-1]["name"] if tool_uses else self._last_tool
        for tr in results:
            tname = names_by_id.get(tr.tool_use_id, "unknown")
            yield f"{TOOL_DONE_PREFIX}{tname} [{'error' if tr.is_error else 'done'}]"
        self.state.messages.append(Message(
            role      = Role.ASSISTANT,
            content   = asst_text if asst_text else "[tool execution]",
            tool_uses = tool_uses,
            provider_blocks = list(provider_blocks or []),
        ))
        for tr in results:
            self.state.messages.append(Message(
                role        = Role.TOOL,
                content     = tr.content,
                tool_use_id = tr.tool_use_id,
                is_error    = tr.is_error,
            ))
            self.session.record_tool_result(
                tool_name   = names_by_id.get(tr.tool_use_id, "unknown"),
                tool_use_id = tr.tool_use_id,
                content     = tr.content,
                is_error    = tr.is_error,
            )
        self.state.messages.extend(hook_injection_messages(self.tool_executor))

    def _new_tool_context(self) -> ToolContext:
        """The context every tool call of this run receives."""
        context = ToolContext(
            cwd           = self.settings.cwd,
            task_registry = self._task_registry,
            ask_user      = self._on_ask_user,
            confirm       = self._on_confirm,
        )
        context.state["session_id"] = self.session.session_id
        context.state["budget"]     = (self.budget, self.session_cost_usd)
        context.state["absorb"]     = self.absorb_subagent
        context.state["loop_factories"]      = self._factories
        context.state["report_bash_changes"] = self.settings.session.report_bash_changes
        context.state["tool_index"] = self._tool_index
        context.state["sandbox"]    = self._sandbox_policy()
        context.state["edit_scope"] = self.settings.sandbox.edit_scope
        context.state["goal_scope"] = self.goal_gate.scope()
        context.state["classifier_feed"] = classifier_feed(self)
        context.state["advisor"]    = self.advisor
        return context

    async def _loop(self, system_prompt: str, tools: list[Any]) -> AsyncGenerator[str, None]:
        """Request, execute tools and repeat until the model is done or a limit stops it."""
        tool_ctx = self._new_tool_context()
        self.goal_gate.start_run()
        state    = LoopState(iteration=0, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id=self.session.session_id)
        saved    = self.failover.begin_run()
        self._context_budget.set_overhead(system_prompt, tools)
        recovery = RecoveryPlanner(
            fallbacks   = list(self.settings.model.fallback_models),
            max_retries = self.settings.model.max_retries,
        )
        try:
            while True:
                state = state.evolve(iteration=state.iteration + 1)
                flow  = LoopFlow()
                async for notice in self._check_run_limits(state.iteration, flow):
                    yield notice
                if flow.finished:
                    return

                async for status in self._prepare_context(state.iteration, flow, system_prompt, tools):
                    yield status
                state = state.evolve(token_budget_used=flow.context_tokens)

                turn = self._build_turn(tools)
                try:
                    async for chunk in self._stream_response(system_prompt, tools, tool_ctx, turn, flow):
                        yield chunk
                except UnicodeDecodeError:
                    yield "\n[dim yellow]Encoding error, retrying without streaming...[/dim yellow]\n"
                    try:
                        async for c in self.failover.send_without_streaming(system_prompt, tools, tool_ctx):
                            yield c
                    except Exception as fe:
                        yield f"\n[bold red]Fallback also failed: {fe}[/bold red]"
                    return
                except Exception as exc:
                    async for chunk in self.failover.recover(exc, recovery, turn, flow, system_prompt, tools, tool_ctx):
                        yield chunk
                if flow.finished:
                    return
        finally:
            self.failover.restore_model(saved)

    async def _check_run_limits(self, iteration: int, flow: LoopFlow) -> AsyncGenerator[str, None]:
        """Stop the run when the turn, cost or token limit is reached, and warn about an unenforceable one."""
        if iteration > self.settings.session.max_turns:
            self.last_stop = "max_turns"
            flow.finished  = True
            yield f"\n[bold yellow]Max turns ({self.settings.session.max_turns}) reached.[/bold yellow]"
            return
        self.turns_used = iteration
        if self.wrap_up_at and iteration == self.wrap_up_at:
            self._signals[signals.WRAP_UP] += 1
            self.state.messages.append(Message(role=Role.USER, content=_WRAP_UP.format(used=iteration - 1, limit=self.settings.session.max_turns)))
        stop, notice = self.limits.exhausted()
        if notice:
            self.last_stop = stop
            flow.finished  = True
            yield notice
            return
        escalated = await self.failover.maybe_escalate()
        if escalated:
            yield escalated
        stop, notice = self.limits.unpriced()
        if stop:
            self.last_stop = stop
            flow.finished  = True
        if notice:
            yield notice

    async def _prepare_context(self, iteration: int, flow: LoopFlow, system_prompt: str, tools: list[Any]) -> AsyncGenerator[str, None]:
        """Report finished background work, compact when the window is nearly full, and show usage."""
        self._inject_queued_input()
        self.state.messages.extend(background_reports(self._task_registry))
        self._mask_old_observations()
        max_ctx  = self.settings.session.max_context_tokens
        thr      = int(max_ctx * self.settings.session.compact_threshold)
        cur_toks = self._context_budget.current(self.state.messages)
        flow.context_tokens = cur_toks

        if cur_toks > thr:
            async for status in self.server_compaction.run(cur_toks, thr, system_prompt, tools):
                yield status

        yield f"{CONTEXT_USAGE_PREFIX}{min(100, int(cur_toks / max_ctx * 100)) if max_ctx > 0 else 0}"
        if self.settings.verbose:
            self.console.print(f"[dim]Turn {iteration} — {len(self.state.messages)} messages[/dim]")

    def _mask_old_observations(self) -> None:
        """Clear old read-type tool output once enough of it has piled up (``session.observation_masking``)."""
        session = self.settings.session
        if not session.observation_masking:
            return
        result = mask_observations(self.state.messages, keep_last=session.mask_keep_last, trigger_tokens=session.mask_trigger_tokens)
        if result.masked:
            self._signals[signals.OBSERVATIONS_MASKED] += result.masked
            self.session.record_system("observation_masking", {"masked": result.masked, "tokens_saved": result.tokens_saved})
            self._context_budget.reset()

    def _build_turn(self, tools: list[Any]) -> LoopTurn:
        """Prepare the next request: a history with unique tool call ids, and its provider form."""
        repaired = repair_tool_ids(self.state.messages)
        if repaired:
            logger.warning("renumbered %d duplicate tool call id(s) in the history", repaired)
        used_ids = collect_tool_use_ids(self.state.messages)
        messages = self._to_provider_messages()
        if self._fire_before_api_call(tools):
            messages = self._to_provider_messages()
        return LoopTurn(messages=messages, used_ids=used_ids, sent_count=len(self.state.messages))

    async def _stream_response(
        self,
        system_prompt: str,
        tools:         list[Any],
        tool_ctx:      ToolContext,
        turn:          LoopTurn,
        flow:          LoopFlow,
    ) -> AsyncGenerator[str, None]:
        """Consume one provider response, acting on each event until it reports ``done``."""
        events = guarded_stream(
            self.provider.stream(system_prompt, turn.messages, self._declared(tools)),
            idle  = self.settings.session.stream_idle_timeout,
            total = self.settings.session.stream_total_timeout,
        )
        async for ev in until_interrupted(events, self.input_queue.interrupt):
            if ev.type == "content_delta":
                self._set_activity(
                    phase="streaming",
                    label=f"Streaming from {self.settings.model.model}",
                )
                turn.asst_text += ev.content
                yield ev.content
            elif ev.type == "thinking_delta":
                turn.thinking_buffer += ev.thinking
                self._set_activity(phase="thinking", label="Thinking...")
                if self._on_thinking_chunk is not None:
                    with contextlib.suppress(Exception):
                        self._on_thinking_chunk(turn.thinking_buffer)
            elif ev.type == "provider_block" and ev.block:
                turn.provider_blocks.append(ev.block)
            elif ev.type == "tool_use_complete":
                turn.add_call(ev.tool_use_id, ev.tool_name, ev.tool_input_complete, ev.tool_signature)
            elif ev.type == "usage" and ev.usage:
                self._apply_usage(ev.usage, turn.sent_count)
            elif ev.type == "done":
                # ``done`` ends the response. Reading on would act twice on a provider
                # that repeats its final chunk.
                async for status in self._finish_response(ev.stop_reason, tool_ctx, turn, flow):
                    yield status
                return
            elif ev.type == "error":
                raise ProviderCallError(
                    ev.error or "Unknown error",
                    ProviderFailure(ev.error_kind or OTHER, ev.status_code, ev.retry_after),
                )

    async def _finish_response(
        self,
        stop:     str,
        tool_ctx: ToolContext,
        turn:     LoopTurn,
        flow:     LoopFlow,
    ) -> AsyncGenerator[str, None]:
        """Act on why the response ended: continue the run (flow stays open) or end it."""
        if stop == "max_tokens":
            if not self._handle_max_tokens_stop():
                self.last_stop = "max_tokens"
                flow.finished  = True
                yield "\n\n[bold red]Max tokens reached.[/bold red]"
            return
        if stop == "end_turn":
            if self.has_queued_input():
                self._handle_end_turn_stop(turn.asst_text, turn.thinking_buffer, turn.provider_blocks)
                return  # the user typed ahead; answer it in the next step
            if (
                self._handle_end_turn_stop(turn.asst_text, turn.thinking_buffer, turn.provider_blocks)
                and self._end_turn_nudges < _MAX_END_TURN_NUDGES
            ):
                self._end_turn_nudges += 1
                return
            decision = self._todo_guard.check(self.session.session_id)
            if decision.kind == CONTINUE:
                self._signals[signals.TODO_NUDGE] += 1
                self.state.messages.append(Message(role=Role.USER, content=decision.message))
                return
            if decision.kind == STALLED:
                yield f"\n[yellow]{escape(decision.message)}[/yellow]\n"
            gate = self.goal_gate.completion_goal()
            if gate is not None:
                async for note in self.goal_gate.verify(flow, gate):
                    yield note
                if not flow.finished:
                    return  # the check failed: the model has been told and takes another step
            self._set_activity(phase="idle", label="Ready")
            flow.finished = True
            return
        if stop == "tool_use" and turn.tool_uses:
            async for status in self._handle_tool_use_stop(turn.asst_text, turn.tool_uses, tool_ctx, turn.provider_blocks):
                yield status
            return
        logger.warning("Unhandled stop_reason: %s", stop)
        if turn.asst_text:
            self.state.messages.append(Message(role=Role.ASSISTANT, content=turn.asst_text))
            self.session.record_assistant_message(turn.asst_text)
        flow.finished = True

    @staticmethod
    def _skill_loader_for(registry: ToolRegistry, settings: NerdvanaSettings) -> SkillLoader:
        """The loader the ActivateSkill tool uses, so ``/clear`` resets its activations; a fresh one without the tool."""
        shared = getattr(registry.get("ActivateSkill"), "loader", None)
        if isinstance(shared, SkillLoader):
            return shared
        loader = SkillLoader.from_settings(settings)
        loader.load_all()
        return loader
