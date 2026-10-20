"""Tests for the budget identity and the domain library (Task 2.2).

Author: 최진호
Date:   2026-10-20
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.harness.ledger import (
    BudgetIdentityError,
    HarnessLedger,
    expected_rollouts,
    expected_sessions,
)
from nerdvana_cli.core.harness.library import DomainLibrary, ExperienceNote
from nerdvana_cli.core.harness.surface import NERDVANA_SURFACE


def _ledger(k: int = 1) -> HarnessLedger:
    """A ledger that spent the budget a run with parameter *k* must spend."""
    ledger = HarnessLedger(mode="parallel", task_id="task-1", k=k)
    for index in range(k):
        ledger.record_rollout(f"base{index}", usd=0.05)
        ledger.record_rollout(f"inv{index}", usd=0.05)
        ledger.record_rollout(f"dom{index}", usd=0.05)
        ledger.record_rollout(f"agg{index}", usd=0.05)
    for index in range(k):
        ledger.record_session(f"prop_inv{index}", usd=0.02)
        ledger.record_session(f"prop_dom{index}", usd=0.02)
        ledger.record_session(f"comp_agg{index}", usd=0.02)
    return ledger


def test_ledger_enforces_4k_rollouts_and_3k_sessions_without_duplicates() -> None:
    ledger = HarnessLedger(mode="parallel", task_id="task-1", k=1)
    for r_lbl in ("base1", "inv1", "dom1", "agg1"):
        ledger.record_rollout(r_lbl, usd=0.05)
    for s_lbl in ("prop_inv1", "prop_dom1", "comp_agg1"):
        ledger.record_session(s_lbl, usd=0.02)

    ledger.assert_identity()
    assert ledger.total_usd == pytest.approx(0.26)
    assert ledger.rollout_usd == pytest.approx(0.20)
    assert ledger.session_usd == pytest.approx(0.06)

    dup_ledger = HarnessLedger(mode="parallel", task_id="task-1", k=1)
    for r_lbl in ("base1", "inv1", "inv1", "agg1"):
        dup_ledger.record_rollout(r_lbl, usd=0.05)
    for s_lbl in ("prop_inv1", "prop_dom1", "comp_agg1"):
        dup_ledger.record_session(s_lbl, usd=0.02)

    with pytest.raises(BudgetIdentityError, match="DUPLICATE"):
        dup_ledger.assert_identity()


def test_the_identity_holds_for_both_modes_and_both_parameters() -> None:
    for mode in ("parallel", "sequential"):
        for k in (1, 2):
            ledger = _ledger(k)
            ledger.mode = mode

            ledger.assert_identity()

            assert expected_rollouts(k) == 4 * k
            assert expected_sessions(k) == 3 * k


def test_a_run_that_spent_the_wrong_number_is_refused() -> None:
    ledger = _ledger(k=1)
    ledger.record_rollout("base2", usd=0.05)

    with pytest.raises(BudgetIdentityError, match="ROLLOUT COUNT"):
        ledger.assert_identity()


def test_a_run_that_spent_the_wrong_composition_is_refused() -> None:
    ledger = HarnessLedger(mode="parallel", task_id="task-1", k=1)
    for r_lbl in ("base1", "base2", "dom1", "agg1"):
        ledger.record_rollout(r_lbl, usd=0.05)
    for s_lbl in ("prop_inv1", "prop_dom1", "comp_agg1"):
        ledger.record_session(s_lbl, usd=0.02)

    with pytest.raises(BudgetIdentityError, match="ROLLOUT COMPOSITION"):
        ledger.assert_identity()


def test_a_run_that_held_the_wrong_number_of_sessions_is_refused() -> None:
    ledger = _ledger(k=1)
    ledger.record_session("comp_agg2", usd=0.02)

    with pytest.raises(BudgetIdentityError, match="SESSION COUNT"):
        ledger.assert_identity()


def test_domain_library_stores_whole_tree_and_blocks_grader_leakage(tmp_path: Path) -> None:
    lib  = DomainLibrary(root=tmp_path, domain="api/migration", surface=NERDVANA_SURFACE)
    tree = {"NIRNA.md": "# Clean rules\n", "scripts/check_ast.py": "print('ast')\n"}
    note = ExperienceNote(
        cue="When migrating async client methods across multiple modules",
        lesson="Run scripts/check_ast.py via PostToolUse hook to catch unawaited coroutines",
        evidence="Candidate dom1 fixed 4 unawaited calls missed by base1 trajectory",
    )

    entry_path = lib.add(task_id="mig-01", harness=tree, experience=note)

    assert entry_path is not None
    assert lib.index_path.exists()
    assert lib.load_tree("mig-01") == tree

    leaked_tree = {"NIRNA.md": "The benchmark rubric expects score 100 from the grader."}
    assert lib.add(task_id="mig-02", harness=leaked_tree, experience=note) is None


def test_the_library_index_holds_the_stored_experiences(tmp_path: Path) -> None:
    lib  = DomainLibrary(root=tmp_path, domain="api/migration", surface=NERDVANA_SURFACE)
    note = ExperienceNote(cue="cue", lesson="lesson", evidence="evidence")
    lib.add(task_id="mig-01", harness={"NIRNA.md": "# Rules\n"}, experience=note)

    index = lib.index_path.read_text(encoding="utf-8")

    assert "mig-01" in index
    assert "cue" in index and "lesson" in index and "evidence" in index


def test_a_forgotten_experience_is_kept_out_of_the_kept_ones(tmp_path: Path) -> None:
    lib  = DomainLibrary(root=tmp_path, domain="api/migration", surface=NERDVANA_SURFACE)
    note = ExperienceNote(cue="cue", lesson="lesson", evidence="evidence")
    lib.add(task_id="mig-01", harness={"NIRNA.md": "# Rules\n"}, experience=note)

    assert lib.forget("mig-01") is True
    assert "kept: no" in lib.index_path.read_text(encoding="utf-8")
    assert lib.retain("mig-01") is True
    assert "kept: yes" in lib.index_path.read_text(encoding="utf-8")


def test_a_note_that_leaks_the_grader_is_refused(tmp_path: Path) -> None:
    lib  = DomainLibrary(root=tmp_path, domain="api/migration", surface=NERDVANA_SURFACE)
    note = ExperienceNote(cue="cue", lesson="the rubric told us the score", evidence="evidence")

    assert lib.add(task_id="mig-03", harness={"NIRNA.md": "# Rules\n"}, experience=note) is None
    assert not lib.index_path.exists()


# ---------------------------------------------------------------------------
# What the next task of a domain starts from
# ---------------------------------------------------------------------------


def _note() -> ExperienceNote:
    return ExperienceNote(cue="cue", lesson="lesson", evidence="evidence")


def test_the_next_task_of_a_domain_starts_from_what_the_domain_learned(tmp_path: Path) -> None:
    lib = DomainLibrary(root=tmp_path, domain="python", surface=NERDVANA_SURFACE)
    lib.add(task_id="t1", harness={"scripts/a.py": "one\n"}, experience=_note())

    assert lib.inherited_tree() == {"scripts/a.py": "one\n"}
    assert lib.retained_ids() == ["t1"]


def test_a_forgotten_experience_is_not_inherited(tmp_path: Path) -> None:
    lib = DomainLibrary(root=tmp_path, domain="python", surface=NERDVANA_SURFACE)
    lib.add(task_id="t1", harness={"scripts/a.py": "one\n"}, experience=_note())

    lib.forget("t1")

    assert lib.inherited_tree() == {}
    assert lib.notes() == []
    assert lib.retained_ids() == []


def test_a_later_experience_may_correct_an_earlier_one(tmp_path: Path) -> None:
    lib = DomainLibrary(root=tmp_path, domain="python", surface=NERDVANA_SURFACE)
    lib.add(task_id="t1", harness={"scripts/a.py": "one\n"}, experience=_note())
    lib.add(task_id="t2", harness={"scripts/a.py": "two\n"}, experience=_note())

    assert lib.inherited_tree() == {"scripts/a.py": "two\n"}


def test_the_notes_are_the_kept_experiences_with_the_task_they_came_from(tmp_path: Path) -> None:
    lib = DomainLibrary(root=tmp_path, domain="python", surface=NERDVANA_SURFACE)
    lib.add(task_id="t1", harness={"scripts/a.py": "one\n"}, experience=ExperienceNote(cue="c1", lesson="l1", evidence="e1"))

    assert [(task_id, note.cue) for task_id, note in lib.notes()] == [("t1", "c1")]


def test_a_library_that_holds_nothing_teaches_nothing(tmp_path: Path) -> None:
    lib = DomainLibrary(root=tmp_path, domain="python", surface=NERDVANA_SURFACE)

    assert lib.inherited_tree() == {}
    assert lib.retained_ids() == []
    assert lib.domain == "python"
