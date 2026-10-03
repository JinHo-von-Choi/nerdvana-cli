"""The memory inbox: proposals, diffs, approval, rejection, forgetting and the audit log.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.memories import MAX_CONTENT_BYTES, MemoriesManager, MemoryScope
from nerdvana_cli.core.memory_review import (
    MAX_PENDING,
    ChangeKind,
    MemoryInbox,
    ProposalAction,
    ProposalNotFoundError,
    forget_memory,
)

PK = MemoryScope.PROJECT_KNOWLEDGE


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def inbox(project: Path) -> MemoryInbox:
    return MemoryInbox(str(project))


@pytest.fixture
def mgr(project: Path) -> MemoriesManager:
    return MemoriesManager(str(project))


def _audit_records() -> list[dict[str, str]]:
    path = core_paths.memory_audit_log()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestProposing:
    def test_a_proposal_is_stored_in_the_inbox_not_in_the_live_store(
        self, inbox: MemoryInbox, mgr: MemoriesManager, project: Path,
    ) -> None:
        proposal = inbox.propose_write("build/commands", "pytest -q", PK)
        assert (project / ".nerdvana" / "memory-inbox" / f"{proposal.id}.json").is_file()
        assert mgr.list_memories() == []
        assert not (project / ".nerdvana" / "memories").exists()

    def test_proposal_is_not_counted_in_the_session_hint(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        inbox.propose_write("a", "x", PK)
        assert mgr.session_start_hint() == ""

    def test_proposal_is_not_readable(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        inbox.propose_write("a", "x", PK)
        with pytest.raises(FileNotFoundError):
            mgr.read("a")

    def test_queued_message_tells_the_agent_it_awaits_review(self, inbox: MemoryInbox) -> None:
        message = inbox.propose_write("a", "x", PK).queued_message()
        assert "awaits the user's review" in message
        assert "takes no effect until approved" in message

    def test_rule_proposal_does_not_touch_nirna_md(self, inbox: MemoryInbox, project: Path) -> None:
        inbox.propose_write("must-type", "Always add type hints", MemoryScope.PROJECT_RULE)
        assert not (project / "NIRNA.md").exists()

    def test_agent_experience_scope_is_refused(self, inbox: MemoryInbox) -> None:
        with pytest.raises(NotImplementedError):
            inbox.propose_write("a", "x", MemoryScope.AGENT_EXPERIENCE)

    @pytest.mark.parametrize("name", ["../escape", "/abs/path", "a/../../b", "%2e%2e/x", ".hidden"])
    def test_unsafe_names_are_refused_at_proposal_time(self, inbox: MemoryInbox, name: str, project: Path) -> None:
        with pytest.raises(ValueError):
            inbox.propose_write(name, "x", PK)
        assert inbox.pending() == []

    def test_oversized_content_is_refused(self, inbox: MemoryInbox) -> None:
        with pytest.raises(ValueError, match="too large"):
            inbox.propose_write("a", "x" * (MAX_CONTENT_BYTES + 1), PK)

    def test_inbox_is_capped(self, inbox: MemoryInbox) -> None:
        for index in range(MAX_PENDING):
            inbox.propose_write(f"m{index}", "x", PK)
        with pytest.raises(ValueError, match="proposals already"):
            inbox.propose_write("one-more", "x", PK)

    def test_delete_proposal_needs_an_existing_memory(self, inbox: MemoryInbox) -> None:
        with pytest.raises(FileNotFoundError):
            inbox.propose_delete("missing")

    def test_edit_proposal_holds_the_edited_text(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "alpha beta", PK)
        proposal = inbox.propose_edit("a", "beta", "gamma")
        assert proposal.content == "alpha gamma"
        assert proposal.action == ProposalAction.WRITE
        assert mgr.peek("a", PK) == "alpha beta"

    def test_rename_proposal_is_a_write_then_a_delete(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("old", "alpha", PK)
        proposals = inbox.propose_rename("old", "new")
        assert [(p.name, p.action) for p in proposals] == [
            ("new", ProposalAction.WRITE), ("old", ProposalAction.DELETE),
        ]

    def test_rename_to_the_same_name_is_refused(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "alpha", PK)
        with pytest.raises(ValueError, match="same"):
            inbox.propose_rename("a", "a")


class TestDiff:
    def test_new_entry(self, inbox: MemoryInbox) -> None:
        view = inbox.view(inbox.propose_write("a", "line one\nline two", PK))
        assert view.kind == ChangeKind.NEW
        assert "--- (none)" in view.diff
        assert "+line one" in view.diff and "+line two" in view.diff

    def test_changed_entry_shows_a_unified_diff(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "keep\nold line\nend", PK)
        view = inbox.view(inbox.propose_write("a", "keep\nnew line\nend", PK))
        assert view.kind == ChangeKind.CHANGED
        assert "--- current/project_knowledge/a" in view.diff
        assert "+++ proposed/project_knowledge/a" in view.diff
        assert "-old line" in view.diff
        assert "+new line" in view.diff
        assert " keep" in view.diff

    def test_removed_entry(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "doomed\ntext", PK)
        view = inbox.view(inbox.propose_delete("a"))
        assert view.kind == ChangeKind.REMOVED
        assert "+++ (removed)" in view.diff
        assert "-doomed" in view.diff and "-text" in view.diff

    def test_identical_content_is_unchanged(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "same", PK)
        view = inbox.view(inbox.propose_write("a", "same", PK))
        assert view.kind == ChangeKind.UNCHANGED
        assert view.diff == ""

    def test_diff_is_taken_against_the_entry_as_it_is_now(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "v1", PK)
        proposal = inbox.propose_write("a", "v2", PK)
        mgr.write("a", "v3", PK)
        assert "-v3" in inbox.view(proposal).diff

    def test_viewing_does_not_record_a_load(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "v1", PK)
        inbox.view(inbox.propose_write("a", "v2", PK))
        assert mgr.list_memories()[0].loaded_at is None


class TestApprove:
    def test_approving_a_new_entry_stores_it_with_agent_source(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        proposal = inbox.propose_write("build/commands", "pytest -q", PK)
        inbox.approve(proposal.id)
        assert mgr.read("build/commands") == "pytest -q"
        assert mgr.list_memories()[0].source == "agent"
        assert inbox.pending() == []

    def test_approving_a_change_overwrites_the_entry(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "v1", PK)
        inbox.approve(inbox.propose_write("a", "v2", PK).id)
        assert mgr.peek("a", PK) == "v2"

    def test_approving_a_removal_deletes_the_entry(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "v1", PK)
        inbox.approve(inbox.propose_delete("a").id)
        assert mgr.list_memories() == []

    def test_approving_a_rule_appends_it_to_nirna_md(self, inbox: MemoryInbox, project: Path) -> None:
        inbox.approve(inbox.propose_write("must-type", "Always add type hints", MemoryScope.PROJECT_RULE).id)
        text = (project / "NIRNA.md").read_text(encoding="utf-8")
        assert "## [Memory] must-type" in text and "Always add type hints" in text

    def test_global_scope_proposal_lands_in_the_global_store(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        inbox.approve(inbox.propose_write("pref", "tabs", MemoryScope.USER_GLOBAL).id)
        assert mgr.peek("pref", MemoryScope.USER_GLOBAL) == "tabs"

    def test_approved_rename_moves_the_entry(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("old", "alpha", PK)
        inbox.propose_rename("old", "new")
        outcomes = inbox.approve_all()
        assert all(o.ok for o in outcomes)
        assert [e.name for e in mgr.list_memories()] == ["new"]

    def test_unknown_id_raises(self, inbox: MemoryInbox) -> None:
        with pytest.raises(ProposalNotFoundError):
            inbox.approve("deadbeef")

    @pytest.mark.parametrize("bad_id", ["../../etc/passwd", "", "DEADBEEF", "dead", "deadbeef0", "a b"])
    def test_ids_that_are_not_eight_hex_digits_name_no_file(self, inbox: MemoryInbox, bad_id: str) -> None:
        with pytest.raises(ProposalNotFoundError):
            inbox.get(bad_id)

    def test_hand_edited_proposal_with_an_escaping_name_is_not_applied(
        self, inbox: MemoryInbox, project: Path, tmp_path: Path,
    ) -> None:
        proposal = inbox.propose_write("fine", "x", PK)
        path = project / ".nerdvana" / "memory-inbox" / f"{proposal.id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = "../../../escaped"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError):
            inbox.approve(proposal.id)
        assert not (tmp_path / "escaped.md").exists()
        assert not list(tmp_path.rglob("escaped.md"))
        assert [p.id for p in inbox.pending()] == [proposal.id]

    def test_failed_approval_leaves_the_proposal_pending(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "v1", PK)
        proposal = inbox.propose_delete("a")
        mgr.delete("a")
        with pytest.raises(FileNotFoundError):
            inbox.approve(proposal.id)
        assert [p.id for p in inbox.pending()] == [proposal.id]

    def test_approve_all_applies_in_order_and_reports_failures(
        self, inbox: MemoryInbox, mgr: MemoriesManager,
    ) -> None:
        mgr.write("gone", "v1", PK)
        inbox.propose_write("first", "1", PK)
        stale = inbox.propose_delete("gone")
        inbox.propose_write("second", "2", PK)
        mgr.delete("gone")
        outcomes = inbox.approve_all()
        assert [o.ok for o in outcomes] == [True, False, True]
        assert {e.name for e in mgr.list_memories()} == {"first", "second"}
        assert [p.id for p in inbox.pending()] == [stale.id]

    def test_approve_all_on_an_empty_inbox_does_nothing(self, inbox: MemoryInbox) -> None:
        assert inbox.approve_all() == []


class TestReject:
    def test_reject_drops_the_proposal_and_changes_nothing(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        proposal = inbox.propose_write("a", "x", PK)
        message = inbox.reject(proposal.id)
        assert proposal.id in message
        assert inbox.pending() == []
        assert mgr.list_memories() == []

    def test_reject_unknown_id_raises(self, inbox: MemoryInbox) -> None:
        with pytest.raises(ProposalNotFoundError):
            inbox.reject("deadbeef")


class TestAuditLog:
    def test_approval_is_logged(self, inbox: MemoryInbox, project: Path) -> None:
        proposal = inbox.propose_write("a", "x", PK)
        inbox.approve(proposal.id)
        (record,) = _audit_records()
        assert record["action"] == "approve"
        assert record["name"] == "a"
        assert record["scope"] == "project_knowledge"
        assert record["proposal"] == proposal.id
        assert record["change"] == "new"
        assert record["project"] == str(project)
        assert record["ts"]

    def test_rejection_is_logged_with_its_change_kind(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "x", PK)
        inbox.reject(inbox.propose_delete("a").id)
        (record,) = _audit_records()
        assert (record["action"], record["change"]) == ("reject", "removed")

    def test_forget_is_logged(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("a", "x", PK)
        forget_memory(str(project), "a")
        (record,) = _audit_records()
        assert (record["action"], record["name"], record["scope"]) == ("forget", "a", "project_knowledge")
        assert mgr.list_memories() == []

    def test_records_are_appended_in_order(self, inbox: MemoryInbox) -> None:
        inbox.approve(inbox.propose_write("a", "x", PK).id)
        inbox.reject(inbox.propose_write("b", "y", PK).id)
        assert [r["action"] for r in _audit_records()] == ["approve", "reject"]

    def test_audit_log_lives_under_the_data_root(self, inbox: MemoryInbox, tmp_path: Path) -> None:
        inbox.approve(inbox.propose_write("a", "x", PK).id)
        assert core_paths.memory_audit_log() == tmp_path / "data" / "logs" / "memory-audit.jsonl"
        assert core_paths.memory_audit_log().is_file()

    def test_a_failed_approval_logs_nothing(self, inbox: MemoryInbox, mgr: MemoriesManager) -> None:
        mgr.write("a", "x", PK)
        proposal = inbox.propose_delete("a")
        mgr.delete("a")
        with pytest.raises(FileNotFoundError):
            inbox.approve(proposal.id)
        assert _audit_records() == []


class TestForget:
    def test_forget_from_a_named_scope(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("same", "project copy", PK)
        mgr.write("same", "global copy", MemoryScope.USER_GLOBAL)
        forget_memory(str(project), "same", MemoryScope.USER_GLOBAL)
        assert mgr.peek("same", PK) == "project copy"
        assert mgr.peek("same", MemoryScope.USER_GLOBAL) is None

    def test_forget_unknown_name_raises_and_logs_nothing(self, project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            forget_memory(str(project), "missing")
        assert _audit_records() == []

    def test_unreadable_proposal_files_are_skipped(self, inbox: MemoryInbox, project: Path) -> None:
        good = inbox.propose_write("a", "x", PK)
        inbox_dir = project / ".nerdvana" / "memory-inbox"
        (inbox_dir / "deadbeef.json").write_text("{broken", encoding="utf-8")
        (inbox_dir / "notanid.json").write_text("{}", encoding="utf-8")
        assert [p.id for p in inbox.pending()] == [good.id]
