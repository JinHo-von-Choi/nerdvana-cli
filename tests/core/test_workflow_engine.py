"""The workflow engine runs steps in dependency order under a concurrency limit and one cost ceiling, stores units, and resumes.

The sub-agent runner is replaced by a fake, so nothing here starts a model; ``verify`` steps run real, trivial shell commands.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.workflow import (
    Workflow,
    WorkflowError,
    bundled_dir,
    load_workflow,
    parse_workflow,
    resolve_inputs,
)
from nerdvana_cli.core.delegation.workflow_engine import RunContext, RunReport, WorkflowRun, effective_scope, tally
from nerdvana_cli.core.delegation.workflow_store import RunStore
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.tool import ToolRegistry

Answer = Callable[[SubagentConfig], str]


class FakeAgents:
    """Stands in for ``run_subagent``: records every call, answers from a function, optionally charges a cost."""

    def __init__(self, answer: Answer | None = None, cost: float = 0.0, delay: float = 0.0) -> None:
        self.answer  = answer or (lambda config: f"answer to: {config.prompt}")
        self.cost    = cost
        self.delay   = delay
        self.calls:  list[SubagentConfig] = []
        self.active  = 0
        self.peak    = 0

    async def __call__(self, config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        self.calls.append(config)
        self.active += 1
        self.peak    = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            config.cost_usd = self.cost
            return self.answer(config), 10
        finally:
            self.active -= 1

    def prompts(self) -> list[str]:
        return [call.prompt for call in self.calls]


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAgents:
    agents = FakeAgents()
    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", agents)
    return agents


@pytest.fixture(autouse=True)
def _data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))


def _workflow(text: str) -> Workflow:
    return parse_workflow(yaml.safe_load(text))


def _context(tmp_path: Path, run_id: str = "run-1", **overrides: Any) -> RunContext:
    settings     = overrides.pop("settings", None) or NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return RunContext(
        settings=settings, registry_factory=lambda **kwargs: ToolRegistry(), cwd=str(tmp_path),
        store=RunStore(run_id, tmp_path / "runs"), **overrides,
    )


async def _run(workflow: Workflow, tmp_path: Path, inputs: dict[str, Any] | None = None, **overrides: Any) -> RunReport:
    return await WorkflowRun(workflow, resolve_inputs(workflow, inputs or {}), _context(tmp_path, **overrides)).run()


# ---------------------------------------------------------------------------
# Order, parallelism, fan-out, interpolation
# ---------------------------------------------------------------------------

DIAMOND = """
name: diamond
result: d
steps:
  - id: d
    prompt: join ${steps.b.output} and ${steps.c.output}
  - id: b
    prompt: left of ${steps.a.output}
  - id: c
    prompt: right of ${steps.a.output}
  - id: a
    prompt: root
"""


async def test_steps_run_in_dependency_order_and_independent_ones_overlap(fake: FakeAgents, tmp_path: Path) -> None:
    fake.delay = 0.02
    report     = await _run(_workflow(DIAMOND), tmp_path)
    assert report.status == "completed"
    prompts = fake.prompts()
    assert prompts[0] == "root"
    assert set(prompts[1:3]) == {"left of answer to: root", "right of answer to: root"}
    assert prompts[3].startswith("join ") and "left of" in prompts[3] and "right of" in prompts[3]
    assert fake.peak == 2                      # b and c ran at the same time
    assert report.output == f"answer to: {prompts[3]}"


FANOUT = """
name: fanout
steps:
  - id: items
    kind: verify
    command: printf 'a\\nb\\nc\\nd\\ne\\nf\\n'
  - id: each
    foreach: ${steps.items.items}
    prompt: handle ${item} (${item_index})
"""


@pytest.mark.parametrize(("workflow_limit", "session_limit", "expected_peak"), [(2, 5, 2), (10, 3, 3), (4, 5, 4)])
async def test_agents_never_exceed_the_workflow_limit_or_the_session_limit(
    fake: FakeAgents, tmp_path: Path, workflow_limit: int, session_limit: int, expected_peak: int,
) -> None:
    settings = NerdvanaSettings()
    settings.workflow.max_parallel        = workflow_limit
    settings.session.max_parallel_agents  = session_limit
    fake.delay = 0.02
    report = await _run(_workflow(FANOUT), tmp_path, settings=settings)
    assert report.status == "completed" and report.ran_units == 7
    assert fake.peak == expected_peak


async def test_foreach_starts_one_agent_per_element_with_the_element_in_its_prompt(fake: FakeAgents, tmp_path: Path) -> None:
    report = await _run(_workflow(FANOUT), tmp_path)
    assert sorted(fake.prompts()) == [f"handle {letter} ({index})" for index, letter in enumerate("abcdef")]
    assert report.output.count("answer to: handle") == 6


async def test_a_foreach_over_an_empty_list_runs_nothing_and_the_workflow_still_completes(fake: FakeAgents, tmp_path: Path) -> None:
    text = FANOUT.replace("printf 'a\\nb\\nc\\nd\\ne\\nf\\n'", '":"')
    report = await _run(_workflow(text), tmp_path)
    assert report.error == ""
    assert report.status == "completed" and fake.calls == [] and report.output == ""


async def test_foreach_beyond_max_agents_stops_the_run_before_any_agent_starts(fake: FakeAgents, tmp_path: Path) -> None:
    settings = NerdvanaSettings()
    settings.workflow.max_agents = 3
    report = await _run(_workflow(FANOUT), tmp_path, settings=settings)
    assert report.status == "failed" and "max_agents" in report.error and fake.calls == []


async def test_inputs_and_json_step_results_are_interpolated_and_shell_values_are_quoted(fake: FakeAgents, tmp_path: Path) -> None:
    fake.answer = lambda config: '{"items": ["x", "y"]}' if "list" in config.prompt else f"saw {config.prompt}"
    text = """
name: interp
inputs:
  word: hello world; echo injected
steps:
  - id: shown
    kind: verify
    command: printf '%s' ${inputs.word}
  - id: listing
    prompt: list things about ${steps.shown.output}
    output: json
    schema: {type: object, required: [items]}
  - id: use
    prompt: "${inputs.word} | ${steps.listing.items} | ${steps.listing.output}"
"""
    report = await _run(_workflow(text), tmp_path)
    assert report.status == "completed"
    assert fake.prompts()[0] == "list things about hello world; echo injected"      # the shell printed the value, it ran nothing
    assert fake.prompts()[1].startswith('hello world; echo injected | ["x", "y"] | {')
    assert '"items": [' in fake.prompts()[1]


async def test_a_failing_check_stops_the_run_with_its_output(fake: FakeAgents, tmp_path: Path) -> None:
    text = "name: c\nsteps:\n  - id: gate\n    kind: verify\n    command: echo broken >&2; exit 3\n  - id: after\n    needs: [gate]\n    prompt: never\n"
    report = await _run(_workflow(text), tmp_path)
    assert report.status == "failed" and "broken" in report.error and "exit 3" in report.error
    assert fake.calls == []


async def test_an_unknown_agent_type_is_refused_before_anything_runs(fake: FakeAgents, tmp_path: Path) -> None:
    with pytest.raises(WorkflowError, match="unknown agent type 'nobody'"):
        await _run(_workflow("name: x\nsteps:\n  - id: a\n    agent: nobody\n    prompt: p\n"), tmp_path)
    assert fake.calls == []


async def test_the_agent_gets_its_type_turn_limit_scope_and_the_callbacks_of_the_session(fake: FakeAgents, tmp_path: Path) -> None:
    seen: list[Any] = []
    report = await _run(
        _workflow("name: x\nsteps:\n  - id: a\n    agent: Explore\n    prompt: p\n    max_turns: 3\n"), tmp_path,
        absorb=lambda usage, signals: seen.append((usage, signals)), parent_session_id="parent-1",
    )
    config = fake.calls[0]
    assert report.status == "completed"
    assert config.name == "Explore" and config.max_turns == 3 and config.parent_session_id == "parent-1"
    assert config.absorb is not None and "exploration agent" in config.system_prompt


# ---------------------------------------------------------------------------
# Read-only by default
# ---------------------------------------------------------------------------


def _is_read_only(config: SubagentConfig) -> bool:
    sandbox = config.settings.sandbox
    return not sandbox.project_writable and not sandbox.scratch_writable and sandbox.write_paths == [] and sandbox.mode != "off"


async def test_an_agent_is_read_only_unless_its_step_names_a_scope_and_the_file_allows_writing(fake: FakeAgents, tmp_path: Path) -> None:
    await _run(_workflow("name: x\nsteps:\n  - id: a\n    agent: general-purpose\n    prompt: p\n"), tmp_path)
    assert _is_read_only(fake.calls[0])
    assert fake.calls[0].settings.sandbox.edit_scope == []


async def test_a_step_with_a_scope_in_a_workflow_that_allows_writing_keeps_that_scope(fake: FakeAgents, tmp_path: Path) -> None:
    text = "name: x\nallow_write: true\nsteps:\n  - id: a\n    prompt: p\n    write_scope: [docs]\n  - id: b\n    prompt: q\n    write_scope: project\n"
    await _run(_workflow(text), tmp_path)
    scoped, project = fake.calls
    assert scoped.settings.sandbox.edit_scope == ["docs"] and not scoped.settings.sandbox.project_writable
    assert project.settings.sandbox.project_writable          # "project" defers to the session's policy


async def test_a_read_only_agent_type_stays_read_only_whatever_the_step_asks(fake: FakeAgents, tmp_path: Path) -> None:
    text = "name: x\nallow_write: true\nsteps:\n  - id: a\n    agent: code-reviewer\n    prompt: p\n    write_scope: project\n"
    await _run(_workflow(text), tmp_path)
    assert _is_read_only(fake.calls[0])


def test_the_effective_scope_never_widens_a_none() -> None:
    assert effective_scope("none", "project") == "none"
    assert effective_scope("", "none") == "none"
    assert effective_scope("", ("docs",)) == ["docs"]
    assert effective_scope(["src"], "project") == ["src"]


# ---------------------------------------------------------------------------
# Cross-check
# ---------------------------------------------------------------------------

CROSS = """
name: cross
steps:
  - id: found
    kind: verify
    command: printf 'c0\\nc1\\nc2\\n'
  - id: confirm
    kind: cross_check
    claims: ${steps.found.items}
    reviewers: 3
    prompt: Look carefully.
  - id: report
    prompt: ${steps.confirm.output}
"""


def _votes(table: dict[int, list[bool]]) -> Answer:
    """Reviewer N (read off the agent id) confirms claim C when table[C][N] is true."""

    def _answer(config: SubagentConfig) -> str:
        if "independent reviewer" not in config.prompt:
            return "summary"
        reviewer = int(config.agent_id.split("-")[-2])
        return json.dumps({"verdicts": [{"claim": claim, "confirmed": votes[reviewer], "reason": f"r{reviewer}"} for claim, votes in table.items()]})

    return _answer


async def test_only_claims_confirmed_by_a_majority_of_independent_reviewers_survive(fake: FakeAgents, tmp_path: Path) -> None:
    fake.answer = _votes({0: [True, True, True], 1: [True, True, False], 2: [True, False, False]})
    report = await _run(_workflow(CROSS), tmp_path)
    assert report.status == "completed"
    reviewers = [call for call in fake.calls if "independent reviewer" in call.prompt]
    assert len(reviewers) == 3 and len({call.agent_id for call in reviewers}) == 3
    assert all("0: c0\n1: c1\n2: c2" in call.prompt and "Look carefully." in call.prompt for call in reviewers)
    outcome = json.loads(fake.prompts()[-1])                    # the report step was handed the cross-check's output
    assert outcome["confirmed"] == ["c0", "c1"]
    assert outcome["unverified"] == [{"claim": "c2", "votes": 1, "reviewers": 3, "reasons": ["r0", "r1", "r2"]}]


def test_a_claim_needs_a_strict_majority() -> None:
    from nerdvana_cli.core.delegation.workflow_engine import Unit

    def unit(confirmed: bool) -> Unit:
        return Unit(0, "k", "ok", value={"verdicts": [{"claim": 0, "confirmed": confirmed}]})

    assert tally(["x"], [unit(True), unit(False)])["unverified"][0]["votes"] == 1        # 1 of 2 is not a majority
    assert tally(["x"], [unit(True), unit(True), unit(False)])["confirmed"] == ["x"]
    assert tally(["x"], [unit(True)])["confirmed"] == ["x"]


async def test_a_reviewer_that_skips_a_claim_is_asked_again(fake: FakeAgents, tmp_path: Path) -> None:
    replies = iter([json.dumps({"verdicts": [{"claim": 0, "confirmed": True}]})] + [json.dumps({"verdicts": [
        {"claim": 0, "confirmed": True}, {"claim": 1, "confirmed": True}, {"claim": 2, "confirmed": True}]})] * 2 + ["done"] * 5)
    fake.answer = lambda config: next(replies)
    report = await _run(_workflow(CROSS.replace("reviewers: 3", "reviewers: 1")), tmp_path)
    assert report.status == "completed"
    assert "no verdict for claim(s) [1, 2]" in fake.prompts()[1]


async def test_a_cross_check_without_claims_runs_no_reviewer(fake: FakeAgents, tmp_path: Path) -> None:
    text = CROSS.replace("printf 'c0\\nc1\\nc2\\n'", '":"')
    report = await _run(_workflow(text), tmp_path)
    assert report.status == "completed"
    assert not any("independent reviewer" in prompt for prompt in fake.prompts())
    assert json.loads(fake.prompts()[0]) == {"confirmed": [], "unverified": []}


# ---------------------------------------------------------------------------
# Schema retries
# ---------------------------------------------------------------------------

SCHEMA = """
name: schema
steps:
  - id: list
    prompt: give a list
    output: json
    schema: {type: object, required: [items], properties: {items: {type: array}}}
"""


async def test_an_answer_that_fails_its_schema_is_sent_back_with_the_problems_and_a_fixed_one_is_accepted(fake: FakeAgents, tmp_path: Path) -> None:
    answers = iter(['{"things": []}', "no json at all", '```json\n{"items": [1]}\n```'])
    fake.answer = lambda config: next(answers)
    report = await _run(_workflow(SCHEMA), tmp_path)
    assert report.status == "completed" and len(fake.calls) == 3
    assert "rejected" in fake.prompts()[1] and "$.items is required" in fake.prompts()[1] and '{"things": []}' in fake.prompts()[1]
    assert "no JSON found" in fake.prompts()[2]
    assert json.loads(report.output) == {"items": [1]}


async def test_an_answer_that_never_validates_fails_the_step_after_three_retries(fake: FakeAgents, tmp_path: Path) -> None:
    fake.answer = lambda config: '{"things": []}'
    report = await _run(_workflow(SCHEMA), tmp_path)
    assert report.status == "failed" and len(fake.calls) == 4
    assert "after 3 retries" in report.error and "$.items is required" in report.error


# ---------------------------------------------------------------------------
# Cost ceiling
# ---------------------------------------------------------------------------

CHAIN = "name: chain\nsteps:\n" + "".join(
    f"  - id: s{n}\n    prompt: step {n}{'' if n == 1 else f' after ${{steps.s{n - 1}.output}}'}\n" for n in range(1, 6)
)


async def test_the_run_stops_when_the_ceiling_is_spent_and_names_the_step_it_stopped_before(fake: FakeAgents, tmp_path: Path) -> None:
    fake.cost = 0.6
    report = await _run(_workflow(CHAIN), tmp_path, ceiling=1.0)
    assert report.status == "stopped" and len(fake.calls) == 2
    assert "step s3" in report.error and "cost ceiling" in report.error
    assert report.cost_usd == pytest.approx(1.2)


async def test_each_agent_is_handed_a_share_of_what_is_left_and_stops_at_it(fake: FakeAgents, tmp_path: Path) -> None:
    await _run(_workflow(FANOUT), tmp_path, ceiling=8.0)
    shares = [call.settings.session.max_cost_usd for call in fake.calls]
    assert shares[0] == pytest.approx(2.0)                   # 1/4 of 8.0 with four agents at a time
    assert all(share > 0 for share in shares)


async def test_an_agent_that_used_up_its_share_stops_the_run_as_budget_limited(fake: FakeAgents, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def capped(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        config.cost_usd, config.stopped_for = 0.5, "max_cost"
        return "partial [Stopped: ...]", 1

    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", capped)
    report = await _run(_workflow(CHAIN), tmp_path, ceiling=2.0)
    assert report.status == "stopped" and "share of the cost ceiling" in report.error


async def test_without_a_ceiling_no_share_is_set_and_the_settings_limit_stays(fake: FakeAgents, tmp_path: Path) -> None:
    settings = NerdvanaSettings()
    settings.session.max_cost_usd = 7.0
    await _run(_workflow("name: x\nsteps:\n  - id: a\n    prompt: p\n"), tmp_path, settings=settings)
    assert fake.calls[0].settings.session.max_cost_usd == 7.0


async def test_a_child_that_never_starts_settles_its_reservation_at_zero(fake: FakeAgents, tmp_path: Path) -> None:
    def no_registry(**kwargs: Any) -> ToolRegistry:
        raise RuntimeError("registry unavailable")

    settings          = NerdvanaSettings()
    settings.cwd      = str(tmp_path)
    context           = RunContext(
        settings=settings, registry_factory=no_registry, cwd=str(tmp_path),
        store=RunStore("run-1", tmp_path / "runs"), ceiling=4.0,
    )
    run               = WorkflowRun(_workflow("name: x\nsteps:\n  - id: a\n    prompt: p\n"), {}, context)
    report            = await run.run()
    assert report.status == "failed" and "registry unavailable" in report.error and fake.calls == []
    assert run.budget is not None
    assert run.budget.promised == 0.0 and run.budget.spent == 0.0          # the envelope came straight back
    assert run.spent == 0.0 and report.cost_usd == 0.0


async def test_a_child_cancelled_while_running_is_settled_with_what_it_spent(fake: FakeAgents, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancelled(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        config.cost_usd = 0.25
        raise asyncio.CancelledError

    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", cancelled)
    run = WorkflowRun(_workflow("name: x\nsteps:\n  - id: a\n    prompt: p\n"), {}, _context(tmp_path, ceiling=4.0))
    with pytest.raises(asyncio.CancelledError):
        await run.run()
    assert run.spent == pytest.approx(0.25)
    assert run.budget is not None
    assert run.budget.spent == pytest.approx(0.25) and run.budget.promised == 0.0


# ---------------------------------------------------------------------------
# Stored units and resume
# ---------------------------------------------------------------------------

THREE = """
name: three
inputs:
  topic: cats
steps:
  - id: a
    prompt: study ${inputs.topic}
  - id: b
    prompt: refine ${steps.a.output}
  - id: c
    prompt: conclude ${steps.b.output}
"""


async def test_a_failed_run_resumes_without_running_finished_units_again(fake: FakeAgents, tmp_path: Path) -> None:
    def flaky(config: SubagentConfig) -> str:
        if config.prompt.startswith("refine"):
            raise RuntimeError("provider unavailable")
        return f"answer to: {config.prompt}"

    fake.answer = flaky
    first = await _run(_workflow(THREE), tmp_path)
    assert first.status == "failed" and "provider unavailable" in first.error and len(fake.calls) == 2
    meta = RunStore("run-1", tmp_path / "runs").read_meta()
    assert meta["status"] == "failed" and meta["workflow"] == "three" and meta["inputs"] == {"topic": "cats"}
    stored = json.loads((tmp_path / "runs" / "run-1" / "step-a.json").read_text(encoding="utf-8"))
    assert stored["units"][0]["output"] == "answer to: study cats"

    fake.answer = lambda config: f"answer to: {config.prompt}"
    fake.calls.clear()
    second = await _run(_workflow(THREE), tmp_path)
    assert second.status == "completed" and second.reused_units == 1
    assert fake.prompts() == ["refine answer to: study cats", "conclude answer to: refine answer to: study cats"]
    assert RunStore("run-1", tmp_path / "runs").read_meta()["status"] == "completed"


async def test_a_changed_input_runs_the_affected_units_again_and_an_unchanged_one_does_not(fake: FakeAgents, tmp_path: Path) -> None:
    await _run(_workflow(THREE), tmp_path)
    fake.calls.clear()
    same = await _run(_workflow(THREE), tmp_path)
    assert same.reused_units == 3 and fake.calls == []
    changed = await _run(_workflow(THREE), tmp_path, inputs={"topic": "dogs"})
    assert changed.reused_units == 0 and len(fake.calls) == 3


async def test_a_change_upstream_re_runs_downstream_units_even_when_their_prompt_text_is_the_same(fake: FakeAgents, tmp_path: Path) -> None:
    text = "name: d\nsteps:\n  - id: a\n    prompt: first ${inputs.v}\n  - id: b\n    needs: [a]\n    prompt: constant text\n"
    wf   = _workflow(text.replace("name: d\n", "name: d\ninputs:\n  v: one\n"))
    await _run(wf, tmp_path)
    fake.calls.clear()
    report = await _run(wf, tmp_path, inputs={"v": "two"})
    assert report.reused_units == 0 and fake.prompts() == ["first two", "constant text"]


async def test_foreach_units_are_kept_one_by_one_so_a_resume_runs_only_the_missing_ones(fake: FakeAgents, tmp_path: Path) -> None:
    def fail_on_d(config: SubagentConfig) -> str:
        if "handle d" in config.prompt:
            raise RuntimeError("boom")
        return "fine"

    fake.answer = fail_on_d
    first = await _run(_workflow(FANOUT), tmp_path)
    assert first.status == "failed" and first.ran_units == 7          # the other five and the check ran, d failed
    fake.answer = lambda config: "fine"
    fake.calls.clear()
    second = await _run(_workflow(FANOUT), tmp_path)
    assert second.status == "completed" and second.reused_units == 5
    assert fake.prompts() == ["handle d (3)"]


async def test_check_steps_always_run_again_so_a_resume_sees_the_current_state(fake: FakeAgents, tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    text   = f"name: v\nsteps:\n  - id: look\n    kind: verify\n    command: cat {marker} || true\n  - id: use\n    prompt: saw ${{steps.look.output}}\n"
    marker.write_text("one", encoding="utf-8")
    await _run(_workflow(text), tmp_path)
    marker.write_text("two", encoding="utf-8")
    report = await _run(_workflow(text), tmp_path)
    assert fake.prompts() == ["saw one", "saw two"] and report.reused_units == 0


async def test_every_stored_file_is_whole_and_no_temporary_file_is_left(fake: FakeAgents, tmp_path: Path) -> None:
    await _run(_workflow(FANOUT), tmp_path)
    directory = tmp_path / "runs" / "run-1"
    names     = sorted(path.name for path in directory.iterdir())
    assert names == ["run.json", "step-each.json", "step-items.json"]
    for path in directory.iterdir():
        json.loads(path.read_text(encoding="utf-8"))


def test_a_run_id_cannot_leave_the_runs_directory(tmp_path: Path) -> None:
    for bad in ("../x", "a/b", "", ".hidden", "x" * 200):
        with pytest.raises(WorkflowError, match="not a run id"):
            RunStore(bad, tmp_path)


# ---------------------------------------------------------------------------
# The bundled workflow end to end
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


async def test_review_fanout_reviews_each_changed_file_cross_checks_the_findings_and_summarizes(
    fake: FakeAgents, tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "b.py").write_text("x = 3\n", encoding="utf-8")

    def answer(config: SubagentConfig) -> str:
        if "independent reviewer" in config.prompt:
            count = sum(1 for line in config.prompt.splitlines() if re.match(r"^\d+: ", line))
            return json.dumps({"verdicts": [{"claim": number, "confirmed": number == 0, "reason": "checked"} for number in range(count)]})
        if config.name == "code-reviewer":
            path = config.prompt.split("file ")[1].split(" ")[0]
            return json.dumps({"items": [{"file": path, "line": 1, "claim": f"{path} is wrong"}]})
        return "SUMMARY"

    fake.answer = answer
    workflow    = load_workflow(bundled_dir() / "review-fanout.yml")
    report      = await WorkflowRun(workflow, {"base": "HEAD"}, _context(repo)).run()
    assert report.status == "completed" and report.output == "SUMMARY"
    reviewed = sorted(call.prompt.split("file ")[1].split(" ")[0] for call in fake.calls if "Read the file" in call.prompt)
    assert reviewed == ["a.py", "b.py"]                                         # the unchanged file is not reviewed
    assert sum(1 for call in fake.calls if "independent reviewer" in call.prompt) == 3
    assert all(call.name == "code-reviewer" for call in fake.calls[:-1]) and fake.calls[-1].name == "Explore"
    assert all(_is_read_only(call) for call in fake.calls)
    summary_prompt = fake.calls[-1].prompt
    assert '"confirmed": [' in summary_prompt and "is wrong" in summary_prompt and '"unverified": [' in summary_prompt
