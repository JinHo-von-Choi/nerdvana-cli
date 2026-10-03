"""``nerdvana memory`` review commands and the ``/memory`` slash command.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.config import paths as core_paths
from nerdvana_cli.core.context.memories import MemoriesManager, MemoryScope
from nerdvana_cli.core.context.memory_index import INDEX_NAME, MemoryIndex
from nerdvana_cli.core.context.memory_review import MemoryInbox
from nerdvana_cli.main import app
from nerdvana_cli.ui.slash import memory_commands as mc

PK = MemoryScope.PROJECT_KNOWLEDGE


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "project"
    root.mkdir()
    return root


def _cli(project: Path, *args: str, answer: str | None = None) -> Any:
    runner = CliRunner()
    with patch("nerdvana_cli.cli.commands.memory_command._cwd", return_value=str(project)):
        return runner.invoke(app, ["memory", *args], input=answer, catch_exceptions=False)


def _audit() -> list[dict[str, str]]:
    path = core_paths.memory_audit_log()
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _age(project: Path, name: str, days: int) -> None:
    base    = project / ".nerdvana" / "memories"
    records = json.loads((base / INDEX_NAME).read_text(encoding="utf-8"))
    past    = time.time() - days * 86400
    records[name].update(created=past, modified=past)
    (base / INDEX_NAME).write_text(json.dumps(records), encoding="utf-8")
    os.utime(base / f"{name}.md", (past, past))


class TestInbox:
    def test_empty(self, project: Path) -> None:
        result = _cli(project, "inbox")
        assert result.exit_code == 0
        assert "inbox is empty" in result.output

    def test_lists_each_kind_with_its_diff(self, project: Path) -> None:
        mgr   = MemoriesManager(str(project))
        inbox = MemoryInbox(str(project))
        mgr.write("changed", "keep\nold", PK)
        mgr.write("removed", "bye", PK)
        new      = inbox.propose_write("fresh", "hello", PK)
        changed  = inbox.propose_write("changed", "keep\nnew", PK)
        removed  = inbox.propose_delete("removed")
        output   = _cli(project, "inbox").output
        assert "3 pending" in output
        for proposal, kind in ((new, "new"), (changed, "changed"), (removed, "removed")):
            line = next(x for x in output.splitlines() if x.startswith(proposal.id))
            assert kind in line and proposal.name in line
        assert "+hello" in output
        assert "-old" in output and "+new" in output
        assert "-bye" in output
        assert "approve <id>" in output

    def test_content_that_looks_like_markup_is_printed_literally(self, project: Path) -> None:
        MemoryInbox(str(project)).propose_write("a", "[bold red]not markup[/bold red]", PK)
        assert "[bold red]not markup[/bold red]" in _cli(project, "inbox").output

    def test_unchanged_proposal_says_so(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "same", PK)
        MemoryInbox(str(project)).propose_write("a", "same", PK)
        assert "same as the current entry" in _cli(project, "inbox").output


class TestApproveAndReject:
    def test_approve_one(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        result   = _cli(project, "approve", proposal.id)
        assert result.exit_code == 0
        assert f"Approved {proposal.id}" in result.output
        assert MemoriesManager(str(project)).peek("a", PK) == "x"
        assert [r["action"] for r in _audit()] == ["approve"]

    def test_approve_all(self, project: Path) -> None:
        inbox = MemoryInbox(str(project))
        inbox.propose_write("a", "1", PK)
        inbox.propose_write("b", "2", PK)
        result = _cli(project, "approve", "--all")
        assert result.exit_code == 0
        assert "2 approved, 0 failed" in result.output
        assert {e.name for e in MemoriesManager(str(project)).list_memories()} == {"a", "b"}
        assert len(_audit()) == 2

    def test_approve_all_reports_a_failure_and_keeps_going(self, project: Path) -> None:
        mgr   = MemoriesManager(str(project))
        inbox = MemoryInbox(str(project))
        mgr.write("gone", "v", PK)
        inbox.propose_delete("gone")
        inbox.propose_write("ok", "1", PK)
        mgr.delete("gone")
        result = _cli(project, "approve", "--all")
        assert "1 approved, 1 failed" in result.output
        assert "Failed" in result.output

    def test_approve_all_on_empty_inbox(self, project: Path) -> None:
        result = _cli(project, "approve", "--all")
        assert result.exit_code == 0
        assert "inbox is empty" in result.output

    def test_approve_unknown_id_exits_nonzero(self, project: Path) -> None:
        result = _cli(project, "approve", "deadbeef")
        assert result.exit_code == 1
        assert "No pending memory proposal" in result.output

    def test_approve_needs_an_id_or_all(self, project: Path) -> None:
        result = _cli(project, "approve")
        assert result.exit_code == 1
        assert "either a proposal id or --all" in result.output

    def test_approve_rejects_id_together_with_all(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        result   = _cli(project, "approve", proposal.id, "--all")
        assert result.exit_code == 1
        assert MemoriesManager(str(project)).list_memories() == []

    def test_reject(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        result   = _cli(project, "reject", proposal.id)
        assert result.exit_code == 0
        assert "Rejected" in result.output
        assert MemoriesManager(str(project)).list_memories() == []
        assert MemoryInbox(str(project)).pending() == []
        assert [r["action"] for r in _audit()] == ["reject"]

    def test_reject_unknown_id_exits_nonzero(self, project: Path) -> None:
        assert _cli(project, "reject", "deadbeef").exit_code == 1

    def test_traversal_shaped_id_is_just_unknown(self, project: Path) -> None:
        result = _cli(project, "approve", "../../x")
        assert result.exit_code == 1


class TestForget:
    def test_confirmed_forget_deletes_and_logs(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "x", PK)
        result = _cli(project, "forget", "a", answer="y\n")
        assert result.exit_code == 0
        assert MemoriesManager(str(project)).list_memories() == []
        assert [(r["action"], r["name"]) for r in _audit()] == [("forget", "a")]

    def test_declined_forget_keeps_the_memory(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "x", PK)
        result = _cli(project, "forget", "a", answer="n\n")
        assert result.exit_code != 0
        assert [e.name for e in MemoriesManager(str(project)).list_memories()] == ["a"]
        assert _audit() == []

    def test_yes_flag_skips_the_question(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "x", PK)
        assert _cli(project, "forget", "a", "--yes").exit_code == 0
        assert MemoriesManager(str(project)).list_memories() == []

    def test_unknown_name_exits_nonzero(self, project: Path) -> None:
        result = _cli(project, "forget", "missing", "--yes")
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_prompt_names_the_entry_history(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "x", PK)
        result = _cli(project, "forget", "a", answer="n\n")
        assert "created" in result.output and "source user" in result.output


class TestStale:
    def _seed(self, project: Path) -> None:
        mgr = MemoriesManager(str(project))
        mgr.write("old-unread", "a", PK)
        mgr.write("old-read", "b", PK)
        mgr.write("fresh", "c", PK)
        mgr.read("old-read")
        _age(project, "old-unread", 60)
        _age(project, "old-read", 60)

    def test_lists_old_entries_that_were_never_read(self, project: Path) -> None:
        self._seed(project)
        output = _cli(project, "stale", "--days", "30").output
        assert "old-unread" in output
        assert "old-read" not in output.replace("old-unread", "")
        assert "fresh" not in output
        assert "never read): 1" in output

    def test_default_is_thirty_days(self, project: Path) -> None:
        self._seed(project)
        assert "not modified for 30+ days" in _cli(project, "stale").output

    def test_nothing_is_removed_without_the_remove_flag(self, project: Path) -> None:
        self._seed(project)
        _cli(project, "stale", "--days", "30")
        assert {e.name for e in MemoriesManager(str(project)).list_memories()} == {"old-unread", "old-read", "fresh"}
        assert _audit() == []

    def test_remove_asks_first(self, project: Path) -> None:
        self._seed(project)
        result = _cli(project, "stale", "--days", "30", "--remove", answer="n\n")
        assert result.exit_code != 0
        assert "old-unread" in {e.name for e in MemoriesManager(str(project)).list_memories()}

    def test_confirmed_remove_deletes_only_the_listed_and_logs_each(self, project: Path) -> None:
        self._seed(project)
        result = _cli(project, "stale", "--days", "30", "--remove", answer="y\n")
        assert result.exit_code == 0
        assert {e.name for e in MemoriesManager(str(project)).list_memories()} == {"old-read", "fresh"}
        assert [(r["action"], r["name"]) for r in _audit()] == [("forget", "old-unread")]

    def test_remove_with_yes_skips_the_question(self, project: Path) -> None:
        self._seed(project)
        assert _cli(project, "stale", "--days", "30", "--remove", "--yes").exit_code == 0
        assert "old-unread" not in {e.name for e in MemoriesManager(str(project)).list_memories()}

    def test_empty_report(self, project: Path) -> None:
        output = _cli(project, "stale").output
        assert "(none)" in output


class TestAddSourceAndList:
    def test_add_records_the_source(self, project: Path) -> None:
        assert _cli(project, "add", "text", "--name", "n", "--source", "import").exit_code == 0
        assert MemoriesManager(str(project)).list_memories()[0].source == "import"

    def test_add_defaults_to_user(self, project: Path) -> None:
        _cli(project, "add", "text", "--name", "n")
        assert MemoriesManager(str(project)).list_memories()[0].source == "user"

    def test_add_rejects_an_unknown_source(self, project: Path) -> None:
        result = _cli(project, "add", "text", "--name", "n", "--source", "agent")
        assert result.exit_code != 0
        assert MemoriesManager(str(project)).list_memories() == []

    def test_list_shows_timestamps_and_source(self, project: Path) -> None:
        MemoriesManager(str(project)).write("n", "x", PK)
        output = _cli(project, "list", "--scope", "project").output
        assert "created" in output and "modified" in output and "user" in output

    def test_memory_commands_are_registered(self) -> None:
        names = {c.name or c.callback.__name__.replace("memory_", "") for c in _memory_commands()}
        assert {"inbox", "approve", "reject", "forget", "stale"} <= names


def _memory_commands() -> list[Any]:
    from nerdvana_cli.cli.commands.memory_command import memory_app
    return list(memory_app.registered_commands)


class _Settings:
    def __init__(self, cwd: str) -> None:
        self.cwd = cwd


class _App:
    def __init__(self, cwd: str) -> None:
        self.settings            = _Settings(cwd)
        self.messages: list[str] = []

    def _add_chat_message(self, message: str, **kwargs: Any) -> None:
        self.messages.append(message)

    @property
    def last(self) -> str:
        return self.messages[-1]


class TestSlashCommand:
    async def test_usage(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, "")
        assert "Usage: /memory" in chat.last
        assert "approve" in chat.last

    async def test_inbox_lists_the_diff(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "old", PK)
        proposal = MemoryInbox(str(project)).propose_write("a", "new", PK)
        chat     = _App(str(project))
        await mc.handle_memory(chat, "inbox")
        assert proposal.id in chat.last and "changed" in chat.last
        assert "[red]-old[/red]" in chat.last and "[green]+new[/green]" in chat.last

    async def test_approve_by_id(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        chat     = _App(str(project))
        await mc.handle_memory(chat, f"approve {proposal.id}")
        assert f"Approved {proposal.id}" in chat.last
        assert MemoriesManager(str(project)).peek("a", PK) == "x"

    async def test_approve_all(self, project: Path) -> None:
        inbox = MemoryInbox(str(project))
        inbox.propose_write("a", "1", PK)
        inbox.propose_write("b", "2", PK)
        chat = _App(str(project))
        await mc.handle_memory(chat, "approve --all")
        assert "2 approved, 0 failed" in chat.last

    async def test_approve_without_target_is_an_error_message(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, "approve")
        assert chat.last.startswith("[red]")

    async def test_approve_id_with_all_is_refused(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        chat     = _App(str(project))
        await mc.handle_memory(chat, f"approve --all {proposal.id}")
        assert chat.last.startswith("[red]")
        assert MemoriesManager(str(project)).list_memories() == []

    async def test_reject(self, project: Path) -> None:
        proposal = MemoryInbox(str(project)).propose_write("a", "x", PK)
        chat     = _App(str(project))
        await mc.handle_memory(chat, f"reject {proposal.id}")
        assert "Rejected" in chat.last
        assert MemoryInbox(str(project)).pending() == []

    async def test_reject_unknown_id(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, "reject deadbeef")
        assert chat.last.startswith("[red]")

    async def test_forget_without_yes_only_describes(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "x", PK)
        chat = _App(str(project))
        await mc.handle_memory(chat, "forget a")
        assert "Would delete" in chat.last and "--yes" in chat.last
        assert [e.name for e in MemoriesManager(str(project)).list_memories()] == ["a"]
        assert _audit() == []

    async def test_forget_with_yes_deletes_and_logs(self, project: Path) -> None:
        MemoriesManager(str(project)).write("a", "x", PK)
        chat = _App(str(project))
        await mc.handle_memory(chat, "forget a --yes")
        assert MemoriesManager(str(project)).list_memories() == []
        assert [r["action"] for r in _audit()] == ["forget"]

    async def test_forget_unknown_name(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, "forget missing --yes")
        assert chat.last.startswith("[red]")

    async def test_stale_lists_and_remove_needs_yes(self, project: Path) -> None:
        mgr = MemoriesManager(str(project))
        mgr.write("old", "x", PK)
        _age(project, "old", 60)
        chat = _App(str(project))
        await mc.handle_memory(chat, "stale --days 30")
        assert "old" in chat.last and "Remove them with" in chat.last
        await mc.handle_memory(chat, "stale --days 30 --remove")
        assert "Nothing removed" in chat.last
        assert [e.name for e in mgr.list_memories()] == ["old"]
        await mc.handle_memory(chat, "stale --days 30 --remove --yes")
        assert mgr.list_memories() == []
        assert [r["action"] for r in _audit()] == ["forget"]

    async def test_stale_days_must_be_a_number(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, "stale --days soon")
        assert chat.last.startswith("[red]")

    async def test_unknown_subcommand(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, "frobnicate")
        assert chat.last.startswith("[red]") and "frobnicate" in chat.last

    async def test_unbalanced_quote_is_an_error_message(self, project: Path) -> None:
        chat = _App(str(project))
        await mc.handle_memory(chat, 'forget "a')
        assert chat.last.startswith("[red]")

    def test_command_is_routed_and_listed_in_the_menu(self) -> None:
        from nerdvana_cli.ui.command_dispatcher import _build_handler_map
        from nerdvana_cli.ui.widgets.command_menu import SLASH_COMMANDS

        assert _build_handler_map()["/memory"] is mc.handle_memory
        assert "/memory" in {name for name, _ in SLASH_COMMANDS}


def test_index_is_untouched_by_inbox_operations(project: Path) -> None:
    mgr = MemoriesManager(str(project))
    mgr.write("a", "x", PK)
    before = MemoryIndex(project / ".nerdvana" / "memories").records()
    inbox = MemoryInbox(str(project))
    inbox.view(inbox.propose_write("a", "y", PK))
    inbox.reject(inbox.pending()[0].id)
    assert MemoryIndex(project / ".nerdvana" / "memories").records() == before
