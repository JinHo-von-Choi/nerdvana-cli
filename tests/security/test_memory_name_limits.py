"""Memory names stay inside their store: encoded traversal, odd segments, symlinks and size caps.

A memory name and its content arrive through the memory tools, and proposals wait in the
inbox before anyone looks at them, so every check holds on the proposal path as well.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.context.memories import (
    MAX_CONTENT_BYTES,
    MAX_NAME_LENGTH,
    MemoriesManager,
    MemoryScope,
)
from nerdvana_cli.core.context.memory_review import MemoryInbox

pytestmark = pytest.mark.security

PK = MemoryScope.PROJECT_KNOWLEDGE

ENCODED_TRAVERSAL = [
    "%2e%2e/escape",
    "%2E%2E%2Fescape",
    "..%2fescape",
    "a/%2e%2e/%2e%2e/escape",
    "%252e%252e/escape",
    "%5c..%5cescape",
    "a%00b",
]

ODD_SEGMENTS = [
    "a//b",
    "a/",
    "./a",
    "a/./b",
    ".hidden",
    "dir/.hidden",
    ".index",
    "a\\b",
    "a\x00b",
    "a\nb",
    "a b",
    "．．/escape",
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def mgr(project: Path) -> MemoriesManager:
    return MemoriesManager(str(project))


def _files_outside_stores(tmp_path: Path) -> list[Path]:
    """Markdown files anywhere under tmp_path that are not in a memory store."""
    return [
        p for p in tmp_path.rglob("*.md")
        if ".nerdvana/memories" not in p.as_posix() and "memories/global" not in p.as_posix()
    ]


@pytest.mark.parametrize("name", ENCODED_TRAVERSAL)
def test_encoded_traversal_is_rejected_by_every_entry_point(
    name: str, mgr: MemoriesManager, project: Path, tmp_path: Path,
) -> None:
    mgr.write("existing", "x", PK)
    with pytest.raises(ValueError):
        mgr.write(name, "x", PK)
    with pytest.raises(ValueError):
        mgr.read(name)
    with pytest.raises(ValueError):
        mgr.delete(name)
    with pytest.raises(ValueError):
        mgr.rename("existing", name)
    with pytest.raises(ValueError):
        MemoryInbox(str(project)).propose_write(name, "x", PK)
    assert _files_outside_stores(tmp_path) == []
    assert [e.name for e in mgr.list_memories()] == ["existing"]


def test_percent_encoded_names_are_named_in_the_error(mgr: MemoriesManager) -> None:
    with pytest.raises(ValueError, match="percent-encoded"):
        mgr.write("%2e%2e/x", "x", PK)


@pytest.mark.parametrize("name", ODD_SEGMENTS)
def test_odd_segments_are_rejected(name: str, mgr: MemoriesManager, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        mgr.write(name, "x", PK)
    assert mgr.list_memories() == []
    assert _files_outside_stores(tmp_path) == []


def test_empty_name_is_rejected(mgr: MemoriesManager) -> None:
    with pytest.raises(ValueError, match="empty"):
        mgr.write("", "x", PK)


def test_name_longer_than_the_cap_is_rejected(mgr: MemoriesManager) -> None:
    with pytest.raises(ValueError, match="too long"):
        mgr.write("a" * (MAX_NAME_LENGTH + 1), "x", PK)


def test_name_at_the_cap_is_accepted(mgr: MemoriesManager) -> None:
    name = "a" * 100 + "/" + "b" * (MAX_NAME_LENGTH - 101)
    assert len(name) == MAX_NAME_LENGTH
    mgr.write(name, "x", PK)
    assert mgr.read(name) == "x"


def test_content_larger_than_the_cap_is_rejected_and_nothing_is_written(mgr: MemoriesManager) -> None:
    with pytest.raises(ValueError, match="too large"):
        mgr.write("big", "x" * (MAX_CONTENT_BYTES + 1), PK)
    assert mgr.list_memories() == []


def test_content_cap_counts_bytes_not_characters(mgr: MemoriesManager) -> None:
    with pytest.raises(ValueError, match="too large"):
        mgr.write("wide", "é" * (MAX_CONTENT_BYTES // 2 + 1), PK)


def test_content_at_the_cap_is_accepted(mgr: MemoriesManager) -> None:
    mgr.write("exact", "x" * MAX_CONTENT_BYTES, PK)
    assert len(mgr.read("exact")) == MAX_CONTENT_BYTES


def test_edit_that_grows_an_entry_past_the_cap_is_rejected(mgr: MemoriesManager) -> None:
    mgr.write("a", "x", PK)
    with pytest.raises(ValueError, match="too large"):
        mgr.edit("a", "x", "y" * (MAX_CONTENT_BYTES + 1))
    assert mgr.read("a") == "x"


def test_rule_name_with_a_line_break_is_rejected(mgr: MemoriesManager, project: Path) -> None:
    with pytest.raises(ValueError, match="single line"):
        mgr.write("rule\n## injected heading", "text", MemoryScope.PROJECT_RULE)
    assert not (project / "NIRNA.md").exists()


def test_rule_name_may_be_a_plain_sentence(mgr: MemoriesManager, project: Path) -> None:
    mgr.write("Use type hints", "text", MemoryScope.PROJECT_RULE)
    assert "## [Memory] Use type hints" in (project / "NIRNA.md").read_text(encoding="utf-8")


def test_symlinked_scope_subdirectory_cannot_be_written_through_from_a_proposal(
    mgr: MemoriesManager, project: Path, tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    store = project / ".nerdvana" / "memories"
    store.mkdir(parents=True)
    (store / "link").symlink_to(outside, target_is_directory=True)
    inbox = MemoryInbox(str(project))
    with pytest.raises(ValueError):
        inbox.propose_write("link/planted", "x", PK)
    assert list(outside.iterdir()) == []


def test_symlink_swapped_in_after_the_proposal_is_not_followed_on_approval(
    mgr: MemoriesManager, project: Path, tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    inbox    = MemoryInbox(str(project))
    proposal = inbox.propose_write("link/planted", "x", PK)
    store    = project / ".nerdvana" / "memories"
    store.mkdir(parents=True)
    (store / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        inbox.approve(proposal.id)
    assert list(outside.iterdir()) == []


def test_symlinked_memory_file_pointing_outside_is_not_read(mgr: MemoriesManager, project: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text("private", encoding="utf-8")
    store = project / ".nerdvana" / "memories"
    store.mkdir(parents=True)
    (store / "leak.md").symlink_to(secret)
    with pytest.raises(ValueError):
        mgr.read("leak")
