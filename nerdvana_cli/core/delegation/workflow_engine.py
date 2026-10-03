"""Running a workflow: the steps in dependency order, agents in parallel, one cost ceiling, resumable.

Author: 최진호
Date:   2026-10-03

A step becomes ready when every step it needs has finished; ready steps run side by side, and all the
agents of a run share ``workflow.max_parallel`` slots (never more than ``session.max_parallel_agents``).
The work of a step is a list of units: one agent run for a plain step, one per element for ``foreach``,
one per reviewer for ``cross_check``, one command for ``verify``. A unit is stored the moment it
finishes (``core/delegation/workflow_store.py``), so ``resume`` runs only the units without a result under the same key.

Money: the run has one ceiling (``RunContext.ceiling``, USD, 0 = none) kept in a ``Budget``. Each agent is
handed an envelope of ``1 / max_parallel`` of what is left, stops at it, and its actual spend is charged
when it finishes. Once nothing is left no further agent starts and the run ends as ``stopped``.

Writing: every agent runs under ``write_scope: none`` unless its step names another scope, which a
workflow can do only with ``allow_write: true``. An agent type that is read-only stays read-only.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.agents.registry import AgentTypeRegistry
from nerdvana_cli.core.config.model_routing import apply_model_spec, select_model
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.subagent import label_confirm, run_subagent
from nerdvana_cli.core.delegation.workflow import Step, Workflow
from nerdvana_cli.core.delegation.workflow_store import OK, RunStore, digest
from nerdvana_cli.core.delegation.workflow_text import (
    Scope,
    WorkflowError,
    as_text,
    extract_json,
    items_of,
    render,
    resolve,
    validate_schema,
    whole_reference,
)
from nerdvana_cli.core.loop.subagent_config import LoopFactories, SubagentConfig, SubagentRegistryFactory
from nerdvana_cli.core.loop.verify import run_verify
from nerdvana_cli.core.safety.agent_scope import apply_write_scope
from nerdvana_cli.core.safety.sandbox import SandboxPolicy
from nerdvana_cli.core.state.budget import MIN_ENVELOPE, Budget
from nerdvana_cli.core.tool import BaseTool, ConfirmCallback

MAX_RETRIES = 3          # times an agent is asked again to fix an answer that fails its schema
VERIFY_TAIL = 20_000     # characters of a verify step's output that are kept (the end)
ERROR       = "error"
BUDGET      = "budget"

RUNNING     = "running"
COMPLETED   = "completed"
FAILED      = "failed"
STOPPED     = "stopped"
INTERRUPTED = "interrupted"

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "required": ["claim", "confirmed"],
        "properties": {"claim": {"type": "integer"}, "confirmed": {"type": "boolean"}, "reason": {"type": "string"}},
    }}},
}


@dataclass
class RunContext:
    """What a run needs from the session that starts it."""

    settings:          NerdvanaSettings
    registry_factory:  SubagentRegistryFactory         # builds an agent's tool registry (tools.subagent_registry)
    cwd:               str
    store:             RunStore
    ceiling:           float                                = 0.0      # USD for the whole run; 0 = none
    confirm:           ConfirmCallback | None               = None
    absorb:            Callable[[dict[str, int], dict[str, int]], None] | None = None
    factories:         LoopFactories | None                 = None
    parent_session_id: str                                  = ""
    parent_tools:      list[BaseTool[Any]] | None           = None
    progress:          Callable[[str], None] | None         = None
    agent_types:       AgentTypeRegistry | None             = None


@dataclass
class RunReport:
    """How a run ended."""

    run_id:       str
    status:       str                  # completed, failed or stopped
    output:       str   = ""           # the result step's output, when completed
    error:        str   = ""
    cost_usd:     float = 0.0
    ran_units:    int   = 0
    reused_units: int   = 0


@dataclass(frozen=True)
class Call:
    """One agent run: who, with what prompt, under which limits."""

    agent:       str
    prompt:      str
    max_turns:   int
    category:    str
    model:       str
    write_scope: str | tuple[str, ...]
    schema:      Mapping[str, Any] | None                           = None
    check:       Callable[[Any], list[str]] | None                  = None

    def fingerprint(self) -> dict[str, Any]:
        return {
            "agent": self.agent, "prompt": self.prompt, "max_turns": self.max_turns, "category": self.category,
            "model": self.model, "write_scope": self.write_scope, "schema": self.schema,
        }


@dataclass
class Job:
    """A unit that has to be run."""

    index:   int
    item:    Any         = None
    call:    Call | None = None
    command: str         = ""
    timeout: int         = 0


@dataclass
class Unit:
    """The result of one job."""

    index:    int
    key:      str
    status:   str
    output:   str   = ""
    value:    Any   = None
    cost_usd: float = 0.0
    tokens:   int   = 0
    error:    str   = ""
    item:     Any   = None

    def record(self) -> dict[str, Any]:
        return {
            "index": self.index, "key": self.key, "status": self.status, "output": self.output, "value": self.value,
            "cost_usd": self.cost_usd, "tokens": self.tokens, "error": self.error, "item": self.item,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Unit:
        return cls(
            int(record.get("index", 0)), str(record["key"]), str(record.get("status", OK)), str(record.get("output", "")),
            record.get("value"), float(record.get("cost_usd", 0.0)), int(record.get("tokens", 0)), str(record.get("error", "")),
            record.get("item"),
        )


@dataclass
class StepResult:
    """What a finished step hands to the steps that need it."""

    output: str
    items:  list[Any] = field(default_factory=list)


def load_agent_types(cwd: str) -> AgentTypeRegistry:
    """The built-in agent types plus the ones defined under ``.nerdvana/agents`` of *cwd*."""
    registry = AgentTypeRegistry()
    for definition in BUILTIN_AGENTS:
        registry.register(definition)
    registry.load_from_dir(str(Path(cwd) / ".nerdvana" / "agents"))
    return registry


def _pretty(value: Any) -> str:
    """JSON as the text steps after it read it."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def effective_scope(agent_scope: str | list[str], step_scope: str | tuple[str, ...]) -> str | list[str]:
    """A read-only agent or step stays read-only; a step's list of paths narrows the agent; ``project`` defers to the agent."""
    if "none" in (agent_scope, step_scope):
        return "none"
    return list(step_scope) if isinstance(step_scope, tuple) else agent_scope


def reviewer_prompt(claims: list[Any], extra: str) -> str:
    """What each independent reviewer of a ``cross_check`` step is asked."""
    numbered = "\n".join(f"{index}: {as_text(claim)}" for index, claim in enumerate(claims))
    return (
        "You are an independent reviewer. Each numbered claim below was made by someone else about this project. "
        "Try to refute every claim by checking the actual code and files; confirm a claim only when you verified it yourself.\n"
        + (f"{extra}\n" if extra else "")
        + 'Answer with JSON only, in this shape: {"verdicts": [{"claim": 0, "confirmed": true, "reason": "one sentence"}]}. '
        + "Give exactly one verdict for every claim number.\n\nClaims:\n" + numbered
    )


def verdict_check(count: int) -> Callable[[Any], list[str]]:
    """A check that a reviewer judged every one of *count* claims exactly once."""

    def _check(value: Any) -> list[str]:
        seen    = [verdict["claim"] for verdict in value["verdicts"]]
        missing = [number for number in range(count) if number not in seen]
        extra   = sorted({number for number in seen if number not in range(count) or seen.count(number) > 1})
        return ([f"no verdict for claim(s) {missing}"] if missing else []) + ([f"claim(s) {extra} judged wrongly or twice"] if extra else [])

    return _check


def tally(claims: list[Any], units: list[Unit]) -> dict[str, Any]:
    """Keep the claims a strict majority of the reviewers confirmed; list the others with their votes as unverified."""
    confirmed:  list[Any] = []
    unverified: list[dict[str, Any]] = []
    for number, claim in enumerate(claims):
        votes   = [verdict for unit in units for verdict in unit.value["verdicts"] if verdict["claim"] == number]
        yes     = sum(1 for verdict in votes if verdict["confirmed"])
        if yes * 2 > len(units):
            confirmed.append(claim)
        else:
            reasons = [str(verdict["reason"]) for verdict in votes if verdict.get("reason")]
            unverified.append({"claim": claim, "votes": yes, "reviewers": len(units), "reasons": reasons})
    return {"confirmed": confirmed, "unverified": unverified}


def _retry_prompt(prompt: str, previous: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {problem}" for problem in problems[:10])
    return f"{prompt}\n\nYour previous answer was rejected:\n{listed}\n\nThat answer was:\n{previous[-3000:]}\n\nAnswer again with only the corrected JSON."


class WorkflowRun:
    """One execution of a workflow with its inputs."""

    def __init__(self, workflow: Workflow, inputs: Mapping[str, Any], context: RunContext) -> None:
        settings          = context.settings
        self.workflow     = workflow
        self.inputs       = dict(inputs)
        self.context      = context
        self.parallel     = max(1, min(settings.workflow.max_parallel, settings.session.max_parallel_agents))
        self.slots        = asyncio.Semaphore(self.parallel)
        self.budget       = Budget(context.ceiling) if context.ceiling > 0 else None
        self.types        = context.agent_types or load_agent_types(context.cwd)
        self.results:     dict[str, StepResult] = {}
        self.digests:     dict[str, str]        = {}
        self.halt         = ""
        self.error        = ""
        self.spent        = 0.0
        self.ran          = 0
        self.reused       = 0

    # -- the run ---------------------------------------------------------------------------------

    async def run(self) -> RunReport:
        """Run every step that can run; the report says how it ended."""
        self._check_agents()
        started = self._write_meta(RUNNING)
        try:
            await self._schedule()
        except (asyncio.CancelledError, KeyboardInterrupt):
            self._write_meta(INTERRUPTED, started)
            raise
        status = self.halt or COMPLETED
        self._write_meta(status, started)
        output = self.results[self.workflow.result].output if status == COMPLETED else ""
        return RunReport(self.context.store.run_id, status, output, self.error, self.spent, self.ran, self.reused)

    def _write_meta(self, status: str, started: str = "") -> str:
        store   = self.context.store
        started = started or (store.read_meta().get("started_at", "") if store.exists() else "") or datetime.now().isoformat(timespec="seconds")
        store.write_meta({
            "run_id": store.run_id, "workflow": self.workflow.name, "inputs": self.inputs, "status": status,
            "started_at": started, "updated_at": datetime.now().isoformat(timespec="seconds"),
            "cost_usd": round(self.spent, 6), "error": self.error,
        })
        return started

    def _check_agents(self) -> None:
        for step in self.workflow.steps:
            if step.kind != "verify" and self.types.get(step.agent) is None:
                known = ", ".join(sorted(definition.agent_type for definition in self.types.all()))
                raise WorkflowError(f"step {step.id}: unknown agent type {step.agent!r}; available: {known}")

    def _note(self, message: str) -> None:
        if self.context.progress is not None:
            self.context.progress(message)

    def _stop(self, status: str, error: str) -> None:
        if not self.halt:
            self.halt, self.error = status, error

    # -- scheduling ------------------------------------------------------------------------------

    async def _schedule(self) -> None:
        pending = {step.id: step for step in self.workflow.steps}
        running: dict[asyncio.Task[StepResult | None], Step] = {}
        try:
            while pending or running:
                if not self.halt:
                    for step in [step for step in pending.values() if all(need in self.results for need in step.needs)]:
                        del pending[step.id]
                        running[asyncio.create_task(self._execute(step))] = step
                if not running:
                    break
                finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    self._collect(running.pop(task), task.result())
        finally:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)

    def _collect(self, step: Step, result: StepResult | None) -> None:
        if result is None:
            return
        self.results[step.id] = result
        self.digests[step.id] = digest(result.output)
        self._note(f"step {step.id} done")

    async def _execute(self, step: Step) -> StepResult | None:
        """Run the units of *step*; None when it did not finish (the run is halted then)."""
        try:
            jobs  = self._plan(step)
            units = await self._run_jobs(step, jobs)
        except WorkflowError as exc:
            self._stop(FAILED, f"step {step.id}: {exc}")
            return None
        bad = [unit for unit in units if unit.status != OK]
        if bad:
            self._stop(STOPPED if any(unit.status == BUDGET for unit in bad) else FAILED, f"step {step.id}: {bad[0].error}")
            return None
        return self._combine(step, units)

    # -- planning --------------------------------------------------------------------------------

    def _scope(self, item: Any = None, index: int = 0, in_foreach: bool = False) -> Scope:
        steps = {step_id: {"output": result.output, "items": result.items} for step_id, result in self.results.items()}
        return Scope(self.inputs, steps, item, index, in_foreach)

    def _list(self, template: str, step: Step, what: str) -> list[Any]:
        value = resolve(whole_reference(template), self._scope())
        if not isinstance(value, list):
            raise WorkflowError(f"{what} of step {step.id} must be a list, got {type(value).__name__}")
        return value

    def _call(self, step: Step, prompt: str, schema: Mapping[str, Any] | None = None, check: Any = None) -> Call:
        scope = step.write_scope if step.kind == "agent" else "none"
        return Call(step.agent, prompt, step.max_turns, step.category, step.model, scope, schema, check)

    def _plan(self, step: Step) -> list[Job]:
        """The jobs of *step*, with every template rendered."""
        if step.kind == "verify":
            return [Job(0, command=render(step.command, self._scope(), shell=True), timeout=step.timeout)]
        if step.kind == "cross_check":
            claims = self._list(step.claims, step, "claims")
            if not claims:
                return []
            prompt = reviewer_prompt(claims, render(step.prompt, self._scope()))
            call   = self._call(step, prompt, VERDICT_SCHEMA, verdict_check(len(claims)))
            return [Job(index, call=call) for index in range(step.reviewers)]
        if not step.foreach:
            return [Job(0, call=self._call(step, render(step.prompt, self._scope()), step.schema))]
        items = self._list(step.foreach, step, "foreach")
        limit = self.context.settings.workflow.max_agents
        if len(items) > limit:
            raise WorkflowError(f"foreach has {len(items)} elements; workflow.max_agents is {limit}")
        return [Job(i, item, self._call(step, render(step.prompt, self._scope(item, i, True)), step.schema)) for i, item in enumerate(items)]

    def _key(self, step: Step, job: Job) -> str:
        """Everything a unit depends on: its definition and the results of the steps it needs."""
        what = job.call.fingerprint() if job.call else {"command": job.command}
        return digest({"kind": step.kind, "index": job.index, "what": what, "needs": {need: self.digests[need] for need in step.needs}})

    # -- running the units -----------------------------------------------------------------------

    async def _run_jobs(self, step: Step, jobs: list[Job]) -> list[Unit]:
        """Reuse stored units whose key is unchanged and run the others; ``verify`` units always run."""
        stored = {} if step.kind == "verify" else self.context.store.load_units(step.id)
        done: dict[int, Unit] = {}
        todo: list[tuple[Job, str]] = []
        for job in jobs:
            key = self._key(step, job)
            if key in stored:
                done[job.index] = Unit.from_record(stored[key])
                self.reused += 1
            else:
                todo.append((job, key))
        self._note(f"step {step.id}: {len(todo)} to run, {len(done)} reused")

        async def _one(job: Job, key: str) -> None:
            unit = await self._run_job(step, job, key)
            done[job.index] = replace(unit, item=job.item)
            self.ran += 1
            self.context.store.save_units(step.id, [done[i].record() for i in sorted(done)])

        await asyncio.gather(*(_one(job, key) for job, key in todo))
        return [done[index] for index in sorted(done)]

    async def _run_job(self, step: Step, job: Job, key: str) -> Unit:
        try:
            return await self._verify(job, key) if job.call is None else await self._ask(step, job, key)
        except Exception as exc:  # noqa: BLE001 - one failed unit must not hide the results of the others
            return Unit(job.index, key, ERROR, error=f"{type(exc).__name__}: {exc}")

    async def _verify(self, job: Job, key: str) -> Unit:
        settings = self.context.settings
        result   = await run_verify(
            job.command, self.context.cwd, timeout=job.timeout or settings.goal.verify_timeout, tail=VERIFY_TAIL,
            policy=SandboxPolicy.from_config(settings.sandbox, settings.secrets.proxy_credentials),
        )
        if result.passed:
            return Unit(job.index, key, OK, result.tail)
        return Unit(job.index, key, ERROR, result.tail, error=f"the command failed ({result.summary()}): {result.tail.strip()[-300:]}")

    def _exhausted(self) -> bool:
        return self.budget is not None and self.budget.remaining(0.0) <= MIN_ENVELOPE

    async def _ask(self, step: Step, job: Job, key: str) -> Unit:
        """Run the agent; with a schema, ask again (up to MAX_RETRIES times) until its answer is valid."""
        call = job.call
        assert call is not None
        prompt, cost, tokens = call.prompt, 0.0, 0
        problems: list[str] = []
        for attempt in range(MAX_RETRIES + 1):
            async with self.slots:
                if self._exhausted():
                    return Unit(job.index, key, BUDGET, cost_usd=cost, tokens=tokens, error="the cost ceiling was reached before this agent could start")
                text, spent, used, stopped = await self._invoke(f"{step.id}-{job.index}-{attempt}", call, prompt)
            cost, tokens = cost + spent, tokens + used
            if stopped == "max_cost":
                return Unit(job.index, key, BUDGET, text, cost_usd=cost, tokens=tokens, error="an agent used up its share of the cost ceiling")
            if call.schema is None:
                return Unit(job.index, key, OK, text, cost_usd=cost, tokens=tokens)
            value, problems = self._parse(text, call)
            if not problems:
                return Unit(job.index, key, OK, text, value, cost, tokens)
            prompt = _retry_prompt(call.prompt, text, problems)
        return Unit(job.index, key, ERROR, text, cost_usd=cost, tokens=tokens, error=f"the answer still failed its schema after {MAX_RETRIES} retries: {'; '.join(problems[:3])}")

    @staticmethod
    def _parse(text: str, call: Call) -> tuple[Any, list[str]]:
        try:
            value = extract_json(text)
        except ValueError as exc:
            return None, [str(exc)]
        problems = validate_schema(value, call.schema or {})
        if not problems and call.check is not None:
            problems = call.check(value)
        return value, problems

    async def _invoke(self, label: str, call: Call, prompt: str) -> tuple[str, float, int, str]:
        """Start one sub-agent: (its answer, what it spent, tokens, why it stopped)."""
        context    = self.context
        definition = self.types.get(call.agent)
        assert definition is not None
        child = copy.deepcopy(context.settings)
        apply_write_scope(child, replace(definition, write_scope=effective_scope(definition.write_scope, call.write_scope)), context.cwd)
        apply_model_spec(child, select_model(call.model, call.category, definition.model, definition.category, child.agents.categories))
        child.session.max_turns = call.max_turns or definition.max_turns
        settle   = self._reserve(child)
        agent_id = f"wf-{context.store.run_id}-{label}"
        config   = SubagentConfig(
            agent_id=agent_id, name=call.agent, prompt=prompt, settings=child,
            registry=context.registry_factory(settings=child, allowed_tools=definition.allowed_tools, parent_tools=context.parent_tools),
            max_turns=child.session.max_turns, system_prompt=definition.system_prompt, confirm=label_confirm(context.confirm, agent_id),
            category=call.category or definition.category, parent_session_id=context.parent_session_id, absorb=context.absorb,
            factories=context.factories,
        )
        try:
            text, tokens = await run_subagent(config, asyncio.Event())
        finally:
            self.spent += config.cost_usd
            if settle is not None:
                settle(config.cost_usd)
        return text, config.cost_usd, tokens, config.stopped_for

    def _reserve(self, child: NerdvanaSettings) -> Callable[[float], None] | None:
        """Give an agent its share of what is left of the ceiling; the call that settles it afterwards."""
        if self.budget is None:
            return None
        budget   = self.budget
        envelope = budget.reserve(1 / self.parallel, 0.0)
        child.session.max_cost_usd = envelope.amount
        return lambda actual: budget.settle(envelope, actual)

    # -- results ---------------------------------------------------------------------------------

    def _combine(self, step: Step, units: list[Unit]) -> StepResult:
        """The output and the item list a finished step offers to the steps after it."""
        if step.kind == "cross_check":
            claims = self._list(step.claims, step, "claims")
            value  = tally(claims, units) if claims else {"confirmed": [], "unverified": []}
            return StepResult(_pretty(value), list(value["confirmed"]))
        if step.kind == "agent" and step.output == "json":
            values = [unit.value for unit in units]
            if not step.foreach:
                return StepResult(_pretty(values[0]), items_of(values[0]))
            return StepResult(_pretty(values), [item for value in values for item in items_of(value)])
        text = "\n\n".join(unit.output for unit in units)
        return StepResult(text, _lines(text))


async def run_workflow(workflow: Workflow, inputs: Mapping[str, Any], context: RunContext) -> RunReport:
    """Run *workflow* with *inputs* and return how it ended; WorkflowError for an unusable definition."""
    return await WorkflowRun(workflow, inputs, context).run()


__all__ = ["RunContext", "RunReport", "WorkflowRun", "run_workflow", "load_agent_types", "COMPLETED", "FAILED", "STOPPED", "INTERRUPTED"]
