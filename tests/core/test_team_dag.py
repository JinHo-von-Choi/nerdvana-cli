"""Tests for the shared task graph of a team (Task 3.1).

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import pytest

from nerdvana_cli.core.delegation.task_state import TaskStatus
from nerdvana_cli.core.delegation.team_dag import (
    CasConflictError,
    CycleDetectedError,
    TeamTaskDAG,
    scopes_overlap,
)


def test_team_dag_enforces_acyclic_edges_cas_revision_and_write_scope_warnings() -> None:
    dag = TeamTaskDAG()
    t1  = dag.create_task(subject="Migrate schema", write_scopes=["nerdvana_cli/core/state"])
    t2  = dag.create_task(
        subject="Update session storage",
        blocked_by=[t1.task_id],
        write_scopes=["nerdvana_cli/core/state/session.py"],
    )

    views = {v.task_id: v for v in dag.views()}
    assert views[t1.task_id].ready is True
    assert views[t2.task_id].ready is False
    assert views[t1.task_id].write_scope_warnings
    assert views[t2.task_id].write_scope_warnings

    with pytest.raises(CasConflictError):
        dag.update_status(t1.task_id, expected_revision=99, status=TaskStatus.RUNNING, owner_id="agent-1")

    with pytest.raises(CycleDetectedError):
        dag.update_blockers(t1.task_id, expected_revision=t1.revision, blocked_by=[t2.task_id])

    dag.update_status(t1.task_id, expected_revision=t1.revision, status=TaskStatus.COMPLETED, owner_id="agent-1")
    views_after = {v.task_id: v for v in dag.views()}
    assert views_after[t2.task_id].ready is True


def test_a_change_moves_the_revision_of_the_task_it_changed() -> None:
    dag = TeamTaskDAG()
    task = dag.create_task(subject="Do the thing")

    updated = dag.update_status(task.task_id, expected_revision=task.revision, status=TaskStatus.RUNNING, owner_id="agent-1")

    assert updated.revision == task.revision + 1
    assert updated.status is TaskStatus.RUNNING
    assert updated.owner_id == "agent-1"
    assert dag.get(task.task_id).revision == task.revision + 1


def test_the_blockers_of_a_task_can_be_changed() -> None:
    dag = TeamTaskDAG()
    first  = dag.create_task(subject="First")
    second = dag.create_task(subject="Second")

    dag.update_blockers(second.task_id, expected_revision=second.revision, blocked_by=[first.task_id])

    assert dag.get(second.task_id).blocked_by == (first.task_id,)
    assert dag.ready_tasks() == [first]


def test_a_cycle_is_refused() -> None:
    dag = TeamTaskDAG()
    first  = dag.create_task(subject="First")
    second = dag.create_task(subject="Second", blocked_by=[first.task_id])

    with pytest.raises(CycleDetectedError):
        dag.update_blockers(first.task_id, expected_revision=first.revision, blocked_by=[second.task_id])


def test_a_blocker_that_does_not_exist_is_refused() -> None:
    dag = TeamTaskDAG()
    task = dag.create_task(subject="First")

    with pytest.raises(KeyError):
        dag.update_blockers(task.task_id, expected_revision=task.revision, blocked_by=["nope"])


def test_two_task_ids_are_refused() -> None:
    dag = TeamTaskDAG()
    dag.create_task(subject="First", task_id="a")

    with pytest.raises(ValueError):
        dag.create_task(subject="Again", task_id="a")


def test_the_tasks_run_in_waves() -> None:
    dag = TeamTaskDAG()
    first  = dag.create_task(subject="First")
    second = dag.create_task(subject="Second", blocked_by=[first.task_id])
    third  = dag.create_task(subject="Third", blocked_by=[second.task_id])

    waves = dag.waves()

    assert [task.task_id for task in waves[0]] == [first.task_id]
    assert [task.task_id for task in waves[1]] == [second.task_id]
    assert [task.task_id for task in waves[2]] == [third.task_id]


def test_a_finished_task_is_not_ready_anymore() -> None:
    dag = TeamTaskDAG()
    task = dag.create_task(subject="First")

    dag.update_status(task.task_id, expected_revision=task.revision, status=TaskStatus.COMPLETED)

    assert dag.ready_tasks() == []
    assert dag.waves() == []


def test_the_write_scopes_of_two_finished_tasks_do_not_warn() -> None:
    dag = TeamTaskDAG()
    first  = dag.create_task(subject="First", write_scopes=["src/core"])
    second = dag.create_task(subject="Second", write_scopes=["src/core/state.py"])

    dag.update_status(first.task_id, expected_revision=first.revision, status=TaskStatus.COMPLETED)

    views = {v.task_id: v for v in dag.views()}
    assert views[first.task_id].write_scope_warnings == ()
    assert views[second.task_id].write_scope_warnings == ()


def test_the_write_scopes_that_do_not_meet_do_not_warn() -> None:
    dag = TeamTaskDAG()
    first  = dag.create_task(subject="First", write_scopes=["src/core"])
    second = dag.create_task(subject="Second", write_scopes=["src/cli"])

    views = {v.task_id: v for v in dag.views()}

    assert views[first.task_id].write_scope_warnings == ()
    assert views[second.task_id].write_scope_warnings == ()


def test_two_scopes_overlap_when_one_lies_inside_the_other() -> None:
    assert scopes_overlap("src/core", "src/core/state.py") is True
    assert scopes_overlap("src/core/state.py", "src/core") is True
    assert scopes_overlap("src/core", "src/core") is True
    assert scopes_overlap("src/core", "src/cli") is False
    assert scopes_overlap("src/core", "src/core-extra") is False
