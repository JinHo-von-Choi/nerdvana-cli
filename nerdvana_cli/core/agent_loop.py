"""Core agent loop — the heart of NerdVana CLI.

Orchestrator only (Phase 0A, T-0A-06): delegates tool execution to
ToolExecutor, recovery hooks to LoopHookEngine, iteration state to LoopState.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from nerdvana_cli.core import signals
from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.core.analytics import AnalyticsWriter, CallOrigin, PricingTable
from nerdvana_cli.core.auto_verify import detect_test_command
from nerdvana_cli.core.budget import Budget
from nerdvana_cli.core.compact import FALLBACK_PROMPT, CompactionState, ai_compact
from nerdvana_cli.core.context_budget import ContextBudget, message_tokens
from nerdvana_cli.core.goal import MET, UNMET, Goal, load_goal, save_goal
from nerdvana_cli.core.images import prompt_content, transcript_text
from nerdvana_cli.core.loop_hooks import LoopHookEngine
from nerdvana_cli.core.loop_state import LoopState
from nerdvana_cli.core.observation_mask import mask_observations
from nerdvana_cli.core.policy import PermissionPolicy
from nerdvana_cli.core.provider_recovery import (
    COMPACT,
    FALLBACK,
    RESEND,
    RETRY,
    ProviderCallError,
    RecoveryPlanner,
    parse_fallback,
)
from nerdvana_cli.core.sandbox import SandboxPolicy
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.stream_guard import guarded_stream
from nerdvana_cli.core.todos import CONTINUE, STALLED, TodoGuard, describe, load_todos, open_items
from nerdvana_cli.core.tool import AskUserCallback, ConfirmCallback, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.core.tool_ids import collect_tool_use_ids, new_tool_use_id, repair_tool_ids
from nerdvana_cli.core.tool_index import ToolIndex
from nerdvana_cli.core.verify import run_verify
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.errors import OTHER, ProviderFailure, classify_exception
from nerdvana_cli.providers.factory import create_provider
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

# Longest background task output quoted in a completion notice.
_BACKGROUND_REPORT_CHARS = 4_000
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
    return message_tokens(msgs)


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


def _hook_injection_messages(executor: Any) -> list[Message]:
    """Turn the messages AFTER_TOOL hooks queued on *executor* into user messages.

    The caller appends them after the batch's tool results, never between a tool
    call and its result.
    """
    return [
        Message(role=Role.USER, content=str(msg["content"]))
        for msg in executor.drain_injections()
        if msg.get("content")
    ]


def _drop_orphan_tool_results(msgs: list[Any]) -> list[Any]:
    """Remove tool results whose originating tool_use is no longer in *msgs*.

    Truncation can cut an assistant message that requested tools while keeping
    the results it produced. Providers reject a tool_result that has no
    matching tool_use, so the widowed results are dropped here.
    """
    known_ids: set[str] = set()
    kept: list[Any] = []
    for msg in msgs:
        if msg.role == Role.ASSISTANT and msg.tool_uses:
            known_ids.update(str(tu.get("id", "")) for tu in msg.tool_uses)
        elif msg.role == Role.TOOL and str(msg.tool_use_id or "") not in known_ids:
            continue
        kept.append(msg)
    return kept


@dataclass
class _Flow:
    """Whether the run ends after the current step, and the context size measured for it."""

    finished:       bool = False
    context_tokens: int  = 0


@dataclass
class _Turn:
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


_VERIFY_FAILED = (
    "[Verification] `{command}` did not pass ({summary}, attempt {attempt} of {limit}). The end of its output:\n\n"
    "{tail}\n\n"
    "The objective is not met until that command exits with status 0. Find the cause and fix it. "
    "Do not change the verification command or weaken the checks it runs to make it pass."
)

_WRAP_UP = (
    "[Turn budget] {used} of {limit} turns are used. Stop exploring now and answer with what you have found; "
    "say plainly what you could not find."
)


class AgentLoop:
    """Orchestrates provider calls, tool execution, and session recording."""

    _queued_input:            list[str]
    _goal:                    Goal | None
    _goal_loaded:             bool
    _auto_goal:               Goal | None
    _auto_edit_mark:          int
    _budget:                  Budget | None
    _tool_index:              ToolIndex | None
    _escalated:               bool
    _escalated_to:            tuple[str, str, str, str] | None
    last_stop:                str
    turns_used:               int
    _cost_limit_warned:       bool
    _usage_input_total:       int
    _usage_output_total:      int
    _usage_cache_read_total:  int
    _usage_cache_write_total: int

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
    ) -> None:
        self._init_telemetry(origin)
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
        from nerdvana_cli.core.builtin_hooks import (
            DirectoryRuleInjector,
            context_limit_recovery,
            json_parse_recovery,
            ralph_loop_check,
            session_start_context_injection,
            session_start_memory_hint,
        )
        from nerdvana_cli.core.checkpoint import CheckpointManager
        from nerdvana_cli.core.command_hooks import load_command_hooks
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
        self._dir_rules = DirectoryRuleInjector()
        self.hooks.register(HookEvent.AFTER_TOOL, self._dir_rules.handle)
        self._user_hook_paths = load_user_hooks(self.hooks, settings)
        self._command_hooks   = load_command_hooks(self.hooks, settings)
        from nerdvana_cli.tools.skill_tool import ActivateSkillTool
        shared_skills = registry.get(ActivateSkillTool.name)
        if isinstance(shared_skills, ActivateSkillTool):
            self.skill_loader = shared_skills.loader
        else:
            self.skill_loader = SkillLoader.from_settings(settings)
            self.skill_loader.load_all()
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
        self._pricing_table     = pricing_table or PricingTable()
        self._analytics_writer  = analytics_writer or AnalyticsWriter(pricing_table=self._pricing_table)
        self._analytics_writer.start_session(
            session_id = self.session.session_id,
            mode       = self.settings.model.provider or None,
            context    = self.settings.cwd or None,
        )
        self._reset_run_counters()
        self.policy        = PermissionPolicy.from_settings(self.settings)
        self.tool_executor = ToolExecutor(
            registry            = self.registry,
            hooks               = self.hooks,
            settings            = self.settings,
            reminder            = self._reminder,
            checkpoint_manager  = self._checkpoint_manager,
            analytics_writer    = self._analytics_writer,
            policy              = self.policy,
        )
        self.loop_hook_engine = LoopHookEngine(hooks=self.hooks, settings=self.settings, registry=self.registry)
        from nerdvana_cli.core.activity_hooks import register_activity_hooks
        register_activity_hooks(self)

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
        self._usage_input_total       += current.input_tokens
        self._usage_output_total      += current.output_tokens
        self._usage_cache_read_total  += current.cache_read_tokens
        self._usage_cache_write_total += current.cache_creation_tokens
        origin = replace(self.origin, turn=self.turns_used, last_tool=self._last_tool)
        cost   = self._analytics_writer.record_api_call(self.settings.model.provider, self.settings.model.model, usage, origin)
        self._cost_total += cost
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
        limit = self.settings.session.max_cost_usd
        if self._budget is None or self._budget.limit != limit:
            self._budget = Budget(limit=limit)
        return self._budget

    @property
    def goal(self) -> Goal | None:
        """The goal this session is held to, loaded from its file the first time it is asked for."""
        if not self._goal_loaded:
            self._goal        = load_goal(self.session.session_id)
            self._goal_loaded = True
        return self._goal

    def set_goal(self, goal: Goal | None) -> None:
        """Hold the session to *goal* (None drops it) and save the change."""
        self._goal        = goal
        self._goal_loaded = True
        save_goal(self.session.session_id, goal)

    def verification_summary(self) -> dict[str, Any] | None:
        """How the goal stands, for the run result; None when the session has no goal."""
        goal = self.goal or self._auto_goal
        if goal is None:
            return None
        return {"command": goal.verify, "status": goal.status, "attempts": goal.attempts, "last_exit": goal.last_exit}

    def _sandbox_policy(self) -> SandboxPolicy:
        """The sandbox policy of this loop, from its settings."""
        sandbox = self.settings.sandbox
        return SandboxPolicy(sandbox.mode, sandbox.network, tuple(sandbox.write_paths), sandbox.project_writable, sandbox.scratch_writable)

    def _edit_count(self) -> int:
        """How many edits the edit tools have applied in this session so far."""
        return sum(self.tool_executor.edited.values())

    def _completion_goal(self) -> Goal | None:
        """The goal that must be met before the run may end: the session's own, else the detected-test check.

        Without a goal and with ``goal.auto_verify`` on, a run that has changed files since the last
        passing check is held to the project's test command; none detected means no check.
        """
        if self.goal is not None:
            return self.goal if self.goal.enforced else None
        if not self.settings.goal.auto_verify or self._edit_count() <= self._auto_edit_mark:
            return None
        if self._auto_goal is not None and self._auto_goal.enforced:
            return self._auto_goal
        command = detect_test_command(self.settings.cwd or ".")
        if not command:
            return None
        self._auto_goal = Goal("Keep the project's tests passing", command, max_attempts=self.settings.goal.max_attempts)
        return self._auto_goal

    async def _verify_goal(self, flow: _Flow, goal: Goal) -> AsyncGenerator[str, None]:
        """Run the verification command of *goal* now that the model says it is done.

        A pass ends the run; running out of attempts ends it as unmet; otherwise the failure is put in
        front of the model and the run goes on (``flow.finished`` stays False).
        """
        config = self.settings.goal
        yield f"\n[dim]Verifying: {escape(goal.verify)}[/dim]\n"
        result = await run_verify(
            goal.verify, self.settings.cwd or ".", timeout=config.verify_timeout, tail=config.output_tail_chars,
            policy=self._sandbox_policy(),
        )
        result = replace(result, tail=self.tool_executor.mask_text(result.tail))
        goal.record_attempt(result.passed, result.exit_code, result.tail)
        if goal is self.goal:
            save_goal(self.session.session_id, goal)
        if goal.status == MET:
            self._auto_edit_mark = self._edit_count()
            yield f"[green]Goal met: {escape(goal.verify)} passed ({result.summary()}).[/green]\n"
            flow.finished = True
            return
        self._signals[signals.VERIFY_FAILED] += 1
        if goal.status == UNMET:
            self.last_stop = "goal_unmet"
            flow.finished  = True
            yield f"[bold yellow]Goal not met after {goal.attempts} verification attempts ({result.summary()}). Stopping.[/bold yellow]\n"
            return
        yield f"[yellow]Verification failed ({result.summary()}); the agent continues.[/yellow]\n"
        self.state.messages.append(Message(role=Role.USER, content=_VERIFY_FAILED.format(
            command=goal.verify, summary=result.summary(), attempt=goal.attempts, limit=goal.max_attempts, tail=result.tail.strip(),
        )))

    def signal_summary(self) -> dict[str, int]:
        """How often each kind of trouble came up in this session (see ``core/signals.py``)."""
        return signals.merge(self._signals, self.tool_executor.signals)

    def usage_summary(self) -> dict[str, int]:
        """Token totals for every provider request made so far in this session."""
        return {
            "input_tokens":       self._usage_input_total,
            "output_tokens":      self._usage_output_total,
            "cache_read_tokens":  self._usage_cache_read_total,
            "cache_write_tokens": self._usage_cache_write_total,
        }

    def _over_cost_limit(self) -> str:
        """The stop notice when ``session.max_cost_usd`` is spent, else an empty string."""
        limit = self.settings.session.max_cost_usd
        if limit <= 0:
            return ""
        spent = self.total_cost_usd()
        if spent < limit:
            return ""
        return f"\n[bold yellow]Cost limit reached (${spent:.4f} of ${limit:.2f}). Stopping.[/bold yellow]"

    async def _plan_first(self, prompt: str) -> AsyncGenerator[str, None]:
        """When the planning gate asks for it, have a plan drafted and put in front of the model."""
        if self.settings.session.planning_gate and _needs_planning(prompt):
            plan = await self._run_plan_agent(prompt)
            if plan:
                yield f"\n[Plan]\n{plan}\n[/Plan]\n"
                self.state.messages.append(Message(role=Role.USER, content=f"[Auto-generated plan]\n{plan}"))

    def _checkpoint_depth(self) -> int:
        """How many file checkpoints this session has (0 where there is no git repository)."""
        with contextlib.suppress(Exception):
            return sum(1 for c in self._checkpoint_manager.list_checkpoints() if c.kind == "snapshot")
        return 0

    def rewind(self, prompts: int = 1) -> str:
        """Go back before the last *prompts* prompts: drop their messages and undo the edits they made.

        Files come back through the checkpoints taken before each edit, so only edits made by the edit
        tools are undone (not what a shell command changed). A compaction since a prompt ends how far back
        this can go. The session transcript records the rewind so a resumed session agrees.
        """
        if not self._turn_marks:
            return "Nothing to rewind: no earlier prompt is available (compaction or a reset ends how far back it goes)."
        prompts = min(max(prompts, 1), len(self._turn_marks))
        index, depth = self._turn_marks[-prompts]
        del self._turn_marks[-prompts:]
        undone = 0
        while self._checkpoint_depth() > depth and "Undone" in self._checkpoint_manager.undo():
            undone += 1
        removed = len(self.state.messages) - index
        del self.state.messages[index:]
        self.session.record_system("rewind", {"prompts": prompts})
        self._context_budget.reset()
        return f"Rewound {prompts} prompt(s): {removed} message(s) removed, {undone} edit(s) undone."

    def _maybe_escalate(self) -> str:
        """Switch to ``session.escalation_model`` once, when the run's signals reach their thresholds.

        Returns the notice to show, or an empty string when nothing changed. The thinking blocks kept on
        earlier assistant messages belong to the model that wrote them, so they are dropped on a switch.
        """
        session = self.settings.session
        if self._escalated or not session.escalation_model:
            return ""
        reason = signals.escalation_reason(self.signal_summary(), session.escalation_signals)
        if not reason:
            return ""
        self._escalated = True
        from nerdvana_cli.providers.factory import resolve_api_key

        provider, model = parse_fallback(session.escalation_model)
        if provider and provider != self.settings.model.provider and not resolve_api_key(ProviderName(provider)):
            logger.warning("escalation to %s skipped: no credential for provider %s", session.escalation_model, provider)
            return ""
        self._signals[signals.ESCALATED] += 1
        self._switch_model(provider, model)
        self._escalated_to = self._model_state()
        for message in self.state.messages:
            message.provider_blocks = []
        return f"\n[bold yellow][Escalating to {self.settings.model.provider}:{model}: {reason}][/bold yellow]\n"

    def _init_telemetry(self, origin: CallOrigin | None) -> None:
        """State for attributing requests and counting trouble: who this loop is and what it has seen."""
        self.origin                = origin or CallOrigin()
        self.usage_listener:       Callable[[dict[str, Any]], None] | None = None
        self._last_tool            = ""
        self._signals: Counter[str] = Counter()
        # Turn at which the model is told to stop exploring and answer; 0 = never. Sub-agents set it.
        self.wrap_up_at            = 0
        self._goal                 = None
        self._goal_loaded          = False
        self._auto_goal            = None
        self._auto_edit_mark       = 0
        self._budget               = None
        self._tool_index           = None
        self._escalated            = False
        self._escalated_to         = None
        self._turn_marks: list[tuple[int, int]] = []   # per prompt: (messages before it, checkpoints before it)

    def _over_token_limit(self) -> str:
        """The stop notice when ``session.max_total_tokens`` is used up, else an empty string."""
        limit = self.settings.session.max_total_tokens
        used  = self._usage_input_total + self._usage_output_total
        if limit <= 0 or used < limit:
            return ""
        return f"\n[bold yellow]Token limit reached ({used:,} of {limit:,}). Stopping.[/bold yellow]"

    def _cost_limit_unenforceable(self) -> bool:
        """True once per loop when a cost limit is set but the model has no known price."""
        if self._cost_limit_warned or self.settings.session.max_cost_usd <= 0:
            return False
        provider = self.settings.model.provider or ""
        model    = self.settings.model.model or ""
        if self._pricing_table.has_price(provider, model):
            return False
        self._cost_limit_warned = True
        logger.warning("cost limit set but %s/%s has no price; only the turn limit applies", provider, model)
        return True

    def session_cost_usd(self) -> float:
        """Estimated USD cost of every provider request this session made itself, each priced for the model that served it."""
        return self._cost_total

    def total_cost_usd(self) -> float:
        """What the session spent: its own requests plus what its finished sub-agents spent."""
        return self._cost_total + self.budget.spent

    def _record_session_totals(self) -> None:
        """Refresh the analytics session row with cumulative tokens and cost.

        Called at the end of every turn: the loop has no shutdown of its own,
        so the row is kept current rather than written once at exit.
        """
        self._analytics_writer.end_session(
            token_total        = self._usage_input_total + self._usage_output_total,
            cost_total         = self.session_cost_usd(),
            cache_read_tokens  = self._usage_cache_read_total,
            cache_write_tokens = self._usage_cache_write_total,
        )

    def _fire_before_api_call(self, tools: list[Any]) -> bool:
        """Run BEFORE_API_CALL handlers just before a provider request goes out.

        Returns True when a handler injected messages, in which case the caller
        rebuilds the provider payload so the injection reaches this same call.
        """
        from nerdvana_cli.core.hooks import HookContext, HookEvent
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
        pname = ProviderName(self.settings.model.provider) if self.settings.model.provider else None
        return create_provider(provider=pname, model=self.settings.model.model, api_key=self.settings.model.api_key,
            base_url=self.settings.model.base_url, max_tokens=self.settings.model.max_tokens, temperature=self.settings.model.temperature,
            prompt_caching=self.settings.model.prompt_caching,
            extended_thinking=self.settings.model.extended_thinking,
            thinking_budget=self.settings.model.thinking_budget, show_thinking=self.settings.model.show_thinking,
            reasoning_effort=self.settings.model.reasoning_effort)

    def _reset_run_counters(self) -> None:
        """Zero the stop status, the typed-ahead queue and the session's usage totals."""
        self._queued_input            = []
        self.last_stop                = "completed"
        self.turns_used               = 0
        self._cost_limit_warned       = False
        self._usage_input_total       = 0
        self._usage_output_total      = 0
        self._usage_cache_read_total  = 0
        self._usage_cache_write_total = 0
        self._cost_total              = 0.0

    def queue_input(self, text: str) -> None:
        """Hold text the user typed while the agent was working.

        It reaches the model at the start of the next step, after any tool results
        already in the history, or becomes the next prompt when the run ends first.
        """
        if text.strip():
            self._queued_input.append(text)

    def has_queued_input(self) -> bool:
        """True when typed-ahead text is waiting for the model."""
        return bool(self._queued_input)

    def take_queued_input(self) -> list[str]:
        """Return and clear the typed-ahead text."""
        taken, self._queued_input = self._queued_input, []
        return taken

    def _inject_queued_input(self) -> None:
        """Put typed-ahead text into the history as user messages, in the order typed."""
        for text in self.take_queued_input():
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
        from nerdvana_cli.core.hooks import HookContext, HookEvent
        self.hooks.fire(HookContext(
            event    = HookEvent.SESSION_END,
            settings = self.settings,
            messages = self.state.messages,
            extra    = {"reason": reason, "session_id": self.session.session_id},
        ))

    def reset_session(self) -> None:
        self._queued_input = []
        self.close_session("reset")
        self._session_started = False; self._sticky_session_context = ""; self.state.messages.clear()  # noqa: E702
        self._git_snapshot = None
        self._turn_marks.clear()
        self._dir_rules.reset()
        self._context_budget.reset()
        self.skill_loader.reset_activations()

    def _prepare_tools(self) -> list[Any]:
        """The tools this run may use, with MCP tools deferred behind ToolSearch when their declarations are large.

        What the model has loaded stays loaded across prompts of the session. Sub-agents never defer: they have
        no ToolSearch.
        """
        from nerdvana_cli.tools.tool_search import ToolSearchTool

        visible = [t for t in self.registry.all_tools() if self.policy.is_visible(t.name) and t.name != "ToolSearch"]
        session = self.settings.session
        index   = ToolIndex.build(visible, session.defer_tools, session.defer_tools_threshold) if self.origin.agent_type == "main" else ToolIndex()
        if self._tool_index is not None:
            index.loaded = self._tool_index.loaded & set(index.deferred)
        self._tool_index = index
        if index.deferred:
            search = ToolSearchTool(index)
            self.registry.register(search)
            visible.append(search)
        return visible

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
        out: list[dict[str, Any]] = []
        for msg in self.state.messages:
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

    async def run(self, prompt: str, images: list[dict[str, Any]] | None = None) -> AsyncGenerator[str, None]:
        """Submit a prompt (with image blocks, see core/images.py) and run the agent loop until completion."""
        self._turn_marks.append((len(self.state.messages), self._checkpoint_depth()))
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
            from nerdvana_cli.core.context_snapshot import collect_snapshot, format_snapshot
            from nerdvana_cli.core.hooks import HookContext, HookEvent
            _p: list[str] = []
            try:
                _s = format_snapshot(await collect_snapshot(self.settings.cwd or "."))
                if _s.strip():
                    _p.append(_s)
            except Exception as exc:  # noqa: BLE001
                logger.debug("context snapshot skipped: %s", exc)
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
            self._record_session_totals()

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
            output, _ = await run_subagent(cfg, asyncio.Event())
            return output
        except Exception as exc:  # noqa: BLE001
            return f"[plan agent error] {exc}"

    async def _maybe_compact_messages(self, cur_toks: int, thr: int) -> AsyncGenerator[str, None]:
        """Compress message history when the token threshold is exceeded.

        Yields ``COMPACT_STATUS_PREFIX`` status strings the UI consumes; mutates
        ``self.state.messages`` in place. Falls back to naive truncation when
        AI compaction returns None or the circuit breaker is open.
        """
        before = len(self.state.messages)
        self._turn_marks.clear()   # compaction rewrites the history the marks point into
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
                self._keep_todos_in_view()
                yield f"{COMPACT_STATUS_PREFIX}done"
                return
        self.state.messages = _drop_orphan_tool_results(compact_messages(self.state.messages, thr))
        self.session.record_compaction(tokens_before=cur_toks, messages_before=before, strategy="naive")
        self._context_budget.reset()
        self._keep_todos_in_view()

    def _report_background_tasks(self) -> None:
        """Tell the model about background tasks that finished since it last looked."""
        registry = self._task_registry
        if registry is None or not hasattr(registry, "drain_unreported"):
            return
        for task in registry.drain_unreported():
            body = task.output if task.output else (task.error or "")
            if len(body) > _BACKGROUND_REPORT_CHARS:
                body = body[:_BACKGROUND_REPORT_CHARS] + f"\n... [cut; TaskGet {task.id} returns the full output]"
            self.state.messages.append(Message(
                role    = Role.USER,
                content = f"[Background task {task.id} {task.status}] {task.description}\n{body}",
            ))

    def _keep_todos_in_view(self) -> None:
        """After compaction, restate the open todo items the summary may have lost."""
        pending = open_items(load_todos(self.session.session_id))
        if pending:
            self.state.messages.append(Message(
                role    = Role.USER,
                content = f"Open todo items (kept across compaction):\n{describe(pending)}",
            ))

    def _handle_max_tokens_stop(self) -> bool:
        """Run AFTER_API_CALL hooks with stop_reason='max_tokens'.

        Returns True if a hook injected recovery messages (caller should
        continue the loop); False if no recovery is available (caller should
        terminate).
        """
        from nerdvana_cli.core.hooks import HookContext, HookEvent
        ctx = HookContext(
            event       = HookEvent.AFTER_API_CALL,
            settings    = self.settings,
            tools       = self.registry.all_tools(),
            messages    = self.state.messages,
            stop_reason = "max_tokens",
            extra       = {"agent_loop": self},
        )
        recovered = False
        for hr in self.hooks.fire(ctx):
            for msg in hr.inject_messages:
                self.state.messages.append(Message(role=Role.USER, content=msg["content"]))
                recovered = True
        return recovered

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
        from nerdvana_cli.core.hooks import HookContext, HookEvent
        ctx = HookContext(
            event       = HookEvent.AFTER_API_CALL,
            settings    = self.settings,
            tools       = self.registry.all_tools(),
            messages    = self.state.messages,
            stop_reason = "end_turn",
            extra       = {"agent_loop": self, "asst_text": asst_text},
        )
        injected = False
        for hr in self.hooks.fire(ctx):
            for msg in hr.inject_messages:
                self.state.messages.append(Message(role=Role.USER, content=msg["content"]))
                injected = True
        return injected

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
        results = await self.tool_executor.run_batch(tool_uses, tool_ctx)
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
        self.state.messages.extend(_hook_injection_messages(self.tool_executor))

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
        context.state["tool_index"] = self._tool_index
        context.state["sandbox"]    = self._sandbox_policy()
        context.state["edit_scope"] = self.settings.sandbox.edit_scope
        context.state["goal_scope"] = self.goal.scope if self.goal is not None and self.goal.enforced and self.goal.scope else None
        return context

    async def _loop(self, system_prompt: str, tools: list[Any]) -> AsyncGenerator[str, None]:
        """Request, execute tools and repeat until the model is done or a limit stops it."""
        tool_ctx = self._new_tool_context()
        self._auto_goal      = None
        self._auto_edit_mark = self._edit_count()
        state    = LoopState(iteration=0, stop_reason="continue", continuation_hint=None, token_budget_used=0, session_id=self.session.session_id)
        saved    = self._model_state()
        self._context_budget.set_overhead(system_prompt, tools)
        recovery = RecoveryPlanner(
            fallbacks   = list(self.settings.model.fallback_models),
            max_retries = self.settings.model.max_retries,
        )
        try:
            while True:
                state = state.evolve(iteration=state.iteration + 1)
                flow  = _Flow()
                async for notice in self._check_run_limits(state.iteration, flow):
                    yield notice
                if flow.finished:
                    return

                async for status in self._prepare_context(state.iteration, flow):
                    yield status
                state = state.evolve(token_budget_used=flow.context_tokens)

                turn = self._build_turn(tools)
                try:
                    async for chunk in self._stream_response(system_prompt, tools, tool_ctx, turn, flow):
                        yield chunk
                except UnicodeDecodeError:
                    yield "\n[dim yellow]Encoding error, retrying without streaming...[/dim yellow]\n"
                    try:
                        async for c in self._fallback_to_send(system_prompt, turn.messages, tools, tool_ctx):
                            yield c
                    except Exception as fe:
                        yield f"\n[bold red]Fallback also failed: {fe}[/bold red]"
                    return
                except Exception as exc:
                    async for chunk in self._recover_from_failure(exc, recovery, turn, flow, system_prompt, tools, tool_ctx):
                        yield chunk
                if flow.finished:
                    return
        finally:
            self._restore_model(saved)

    async def _check_run_limits(self, iteration: int, flow: _Flow) -> AsyncGenerator[str, None]:
        """Stop the run when the turn or cost limit is reached, and warn about an unenforceable one."""
        if iteration > self.settings.session.max_turns:
            self.last_stop = "max_turns"
            flow.finished  = True
            yield f"\n[bold yellow]Max turns ({self.settings.session.max_turns}) reached.[/bold yellow]"
            return
        self.turns_used = iteration
        if self.wrap_up_at and iteration == self.wrap_up_at:
            self._signals[signals.WRAP_UP] += 1
            self.state.messages.append(Message(role=Role.USER, content=_WRAP_UP.format(used=iteration - 1, limit=self.settings.session.max_turns)))
        for stop, notice in (("max_cost", self._over_cost_limit()), ("max_total_tokens", self._over_token_limit())):
            if notice:
                self.last_stop = stop
                flow.finished  = True
                yield notice
                return
        escalated = self._maybe_escalate()
        if escalated:
            yield escalated
        if self._cost_limit_unenforceable():
            if self.settings.session.require_price:
                self.last_stop = "unpriced"
                flow.finished  = True
                yield (
                    f"\n[bold red]Cost limit ${self.settings.session.max_cost_usd:.2f} cannot be enforced: no price is known for "
                    f"{self.settings.model.provider}/{self.settings.model.model}. Refusing to run (session.require_price).[/bold red]"
                )
                return
            yield (
                f"\n[yellow]Cost limit ${self.settings.session.max_cost_usd:.2f} is not enforced: "
                f"no price is known for {self.settings.model.provider}/{self.settings.model.model}.[/yellow]\n"
            )

    async def _prepare_context(self, iteration: int, flow: _Flow) -> AsyncGenerator[str, None]:
        """Report finished background work, compact when the window is nearly full, and show usage."""
        self._inject_queued_input()
        self._report_background_tasks()
        self._mask_old_observations()
        max_ctx  = self.settings.session.max_context_tokens
        thr      = int(max_ctx * self.settings.session.compact_threshold)
        cur_toks = self._context_budget.current(self.state.messages)
        flow.context_tokens = cur_toks

        if cur_toks > thr:
            async for status in self._maybe_compact_messages(cur_toks, thr):
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

    def _build_turn(self, tools: list[Any]) -> _Turn:
        """Prepare the next request: a history with unique tool call ids, and its provider form."""
        repaired = repair_tool_ids(self.state.messages)
        if repaired:
            logger.warning("renumbered %d duplicate tool call id(s) in the history", repaired)
        used_ids = collect_tool_use_ids(self.state.messages)
        messages = self._to_provider_messages()
        if self._fire_before_api_call(tools):
            messages = self._to_provider_messages()
        return _Turn(messages=messages, used_ids=used_ids, sent_count=len(self.state.messages))

    async def _stream_response(
        self,
        system_prompt: str,
        tools:         list[Any],
        tool_ctx:      ToolContext,
        turn:          _Turn,
        flow:          _Flow,
    ) -> AsyncGenerator[str, None]:
        """Consume one provider response, acting on each event until it reports ``done``."""
        async for ev in guarded_stream(
            self.provider.stream(system_prompt, turn.messages, self._declared(tools)),
            idle  = self.settings.session.stream_idle_timeout,
            total = self.settings.session.stream_total_timeout,
        ):
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
        turn:     _Turn,
        flow:     _Flow,
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
            gate = self._completion_goal()
            if gate is not None:
                async for note in self._verify_goal(flow, gate):
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

    async def _recover_from_failure(
        self,
        exc:           Exception,
        recovery:      RecoveryPlanner,
        turn:          _Turn,
        flow:          _Flow,
        system_prompt: str,
        tools:         list[Any],
        tool_ctx:      ToolContext,
    ) -> AsyncGenerator[str, None]:
        """Retry, compact, fall back, resend without streaming, or end the run after a failed request."""
        from_event = isinstance(exc, ProviderCallError)
        failure    = exc.failure if isinstance(exc, ProviderCallError) else classify_exception(exc)
        action     = recovery.plan(
            failure,
            current          = self.settings.model.model,
            current_provider = self.settings.model.provider or "",
            streamed         = bool(turn.asst_text or turn.tool_uses),
        )
        if action.kind == RESEND:
            flow.finished = True
            yield "\n[dim yellow]Streaming error, retrying without streaming...[/dim yellow]\n"
            async for c in self._fallback_to_send(system_prompt, turn.messages, tools, tool_ctx):
                yield c
            return
        if action.kind == RETRY:
            self._signals[signals.PROVIDER_RETRY] += 1
            yield f"\n[dim yellow][Retrying in {action.delay:.1f}s: {failure.kind}][/dim yellow]\n"
            await asyncio.sleep(action.delay)
            return
        if action.kind == COMPACT:
            cur_toks = flow.context_tokens
            async for status in self._maybe_compact_messages(cur_toks, int(cur_toks * 0.6)):
                yield status
            return
        if action.kind == FALLBACK:
            self._signals[signals.PROVIDER_FALLBACK] += 1
            self._switch_model(action.provider, action.model)
            yield f"\n[dim yellow][Fallback: {self.settings.model.provider}:{action.model}][/dim yellow]\n"
            return
        self.last_stop = "provider_error"
        flow.finished  = True
        if from_event:
            yield f"\n[bold red]Provider error: {exc}[/bold red]"
            return
        yield f"\n[bold red]Error: {exc}[/bold red]"
        self.state.messages.append(Message(role=Role.ASSISTANT, content=f"Error occurred: {exc}"))

    def _restore_model(self, saved: tuple[str, str, str, str]) -> None:
        """After a prompt, go back to the model it started on; an escalation lasts for the session, a fallback only for the prompt it served."""
        (
            self.settings.model.provider,
            self.settings.model.model,
            self.settings.model.api_key,
            self.settings.model.base_url,
        ) = self._escalated_to or saved
        self._escalated_to = None
        self.provider      = self.create_provider_from_settings()

    def _model_state(self) -> tuple[str, str, str, str]:
        """The settings that name the model in use: provider, model, API key and base URL."""
        return (self.settings.model.provider, self.settings.model.model, self.settings.model.api_key, self.settings.model.base_url)

    def _switch_model(self, provider: str | None, model: str) -> None:
        """Point the loop at *model*, on *provider* when one is given."""
        if provider and provider != self.settings.model.provider:
            from nerdvana_cli.providers.factory import resolve_api_key
            self.settings.model.provider = provider
            self.settings.model.api_key  = resolve_api_key(ProviderName(provider))
            self.settings.model.base_url = ""
        self.settings.model.model = model
        self.provider             = self.create_provider_from_settings()

    async def _fallback_to_send(
        self, system_prompt: str, messages: list[dict[str, Any]], tools: list[Any], context: ToolContext,
    ) -> AsyncGenerator[str, None]:
        """Non-streaming fallback when provider streaming fails."""
        for _ in range(10):
            self._fire_before_api_call(tools)
            repair_tool_ids(self.state.messages)
            try:
                result = await self.provider.send(system_prompt, self._to_provider_messages(), self._declared(tools))
            except Exception as e:
                yield f"\n[bold red]Fallback error: {e}[/bold red]"
                return

            content   = result.get("content", "")
            tool_uses = result.get("tool_uses", [])
            usage     = result.get("usage", {})
            if content:
                yield content
            if usage:
                self._apply_usage(usage, None)
            if tool_uses:
                self.state.messages.append(Message(
                    role=Role.ASSISTANT, content=content if content else "[tool execution]", tool_uses=tool_uses,
                    provider_blocks=list(result.get("provider_blocks") or []),
                ))
                self.session.record_assistant_message(content, tool_uses, result.get("provider_blocks"))
                for tr in await self.tool_executor.run_batch(tool_uses, context):
                    self.state.messages.append(Message(role=Role.TOOL, content=tr.content, tool_use_id=tr.tool_use_id, is_error=tr.is_error))
                self.state.messages.extend(_hook_injection_messages(self.tool_executor))
                continue
            if content:
                self.state.messages.append(Message(role=Role.ASSISTANT, content=content, provider_blocks=list(result.get("provider_blocks") or [])))
                self.session.record_assistant_message(content, provider_blocks=result.get("provider_blocks"))
            return
