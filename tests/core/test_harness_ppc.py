"""Tests for the P-P-C run and the anonymous candidate selection (Task 2.3).

Author: 최진호
Date:   2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

from nerdvana_cli.core.harness.ledger import HarnessLedger
from nerdvana_cli.core.harness.ppc import (
    ANSWER_FREE,
    EVIDENCE_BOUNDARY,
    Candidate,
    ContextTreeBuilder,
    PPCTaskRunner,
    assign_anonymous_ids,
    system_instructions,
)
from nerdvana_cli.core.harness.surface import NERDVANA_SURFACE


def test_the_context_tree_carries_the_traces_the_deliverables_and_the_harness() -> None:
    candidates = [
        Candidate(label="inv0", kind="inv", deliverable="the invariant deliverable", rollout="the trace"),
    ]
    harness = {"NIRNA.md": "# Rules\n", "scripts/check.py": "print('x')\n"}

    tree = ContextTreeBuilder().build(candidates, harness, library_index="# api/migration\n")

    assert tree["context/rollouts/inv0.md"] == "the trace"
    assert tree["context/deliverables/inv0.md"] == "the invariant deliverable"
    assert tree["context/harness/NIRNA.md"] == "# Rules\n"
    assert tree["context/harness/scripts/check.py"] == "print('x')\n"
    assert tree["context/library/INDEX.md"] == "# api/migration\n"


def test_the_instructions_say_the_evidence_is_material_and_the_answer_is_their() -> None:
    instructions = system_instructions()

    assert EVIDENCE_BOUNDARY in instructions
    assert ANSWER_FREE in instructions
    assert "context/rollouts/" in instructions and "context/deliverables/" in instructions
    assert "not an order" in instructions
    assert "free-form" in instructions


def test_the_anonymous_ids_do_not_follow_the_order_the_labels_came_in() -> None:
    labels = ["dom0", "inv0", "agg0", "base0"]

    ids = assign_anonymous_ids(labels, task_id="task-1", k=1, mode="parallel")

    assert sorted(ids.values()) == ["A", "B", "C", "D"]
    assert [ids[label] for label in labels] != ["A", "B", "C", "D"]


def test_the_anonymous_ids_are_the_same_whatever_order_the_labels_came_in() -> None:
    first  = assign_anonymous_ids(["dom0", "inv0"], task_id="task-1", k=1, mode="parallel")
    second = assign_anonymous_ids(["inv0", "dom0"], task_id="task-1", k=1, mode="parallel")

    assert first == second


def test_the_anonymous_ids_follow_the_task_and_its_mode() -> None:
    labels = [f"c{i}" for i in range(26)]

    first = assign_anonymous_ids(labels, "task-1", 1, "parallel")

    assert first == assign_anonymous_ids(labels, "task-1", 1, "parallel")      # the same task, the same shuffle
    assert first != assign_anonymous_ids(labels, "task-2", 1, "parallel")      # another task, another shuffle
    assert first != assign_anonymous_ids(labels, "task-1", 1, "sequential")   # another mode, another shuffle


def test_the_anonymous_ids_go_past_the_alphabet() -> None:
    ids = assign_anonymous_ids([f"c{i}" for i in range(27)], task_id="task-1", k=7, mode="parallel")

    assert "AA" in ids.values()


def _runner(k: int = 1) -> tuple[HarnessLedger, PPCTaskRunner]:
    """A runner and the ledger that holds what it spends."""
    ledger = HarnessLedger(mode="parallel", task_id="task-1", k=k)
    return ledger, PPCTaskRunner(ledger, NERDVANA_SURFACE)


def test_a_full_run_spends_the_budget_it_declared() -> None:
    ledger, runner = _runner(k=1)

    runner.probe_base(0, lambda candidate: "the base trace")
    inv = runner.propose("inv", 0, lambda kind, index: "the invariant deliverable")
    dom = runner.propose("dom", 0, lambda kind, index: "the domain deliverable")
    runner.probe(inv, lambda candidate: "the invariant trace")
    runner.probe(dom, lambda candidate: "the domain trace")
    runner.compose(0, [inv, dom], lambda candidates: "the synthesis")

    ledger.assert_identity()
    assert len(ledger.rollouts) == 4
    assert len(ledger.sessions) == 3


def test_a_run_with_two_parameters_spends_twice_the_budget() -> None:
    ledger, runner = _runner(k=2)

    for index in range(2):
        runner.probe_base(index, lambda candidate: "trace")
        inv = runner.propose("inv", index, lambda kind, i: "deliverable")
        dom = runner.propose("dom", index, lambda kind, i: "deliverable")
        runner.probe(inv, lambda candidate: "trace")
        runner.probe(dom, lambda candidate: "trace")
        runner.compose(index, [inv, dom], lambda candidates: "synthesis")

    ledger.assert_identity()
    assert len(ledger.rollouts) == 8
    assert len(ledger.sessions) == 6


def test_the_synthesis_is_given_the_candidates_that_made_it() -> None:
    _ledger, runner = _runner(k=1)
    inv = runner.propose("inv", 0, lambda kind, index: "deliverable")
    dom = runner.propose("dom", 0, lambda kind, index: "deliverable")
    seen: list[Sequence[Candidate]] = []

    runner.compose(0, [inv, dom], lambda candidates: seen.append(candidates) or "synthesis")

    assert list(seen[0]) == [inv, dom]
