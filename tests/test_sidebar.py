"""Unit tests for the Sidebar widget and its sections."""
from __future__ import annotations

from pathlib import Path

from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.ui.sidebar import Sidebar
from nerdvana_cli.ui.sidebar_sections import (
    SidebarContextSection,
    SidebarHeaderSection,
    SidebarMcpSection,
    SidebarSkillsSection,
    SidebarTasksSection,
    SidebarToolsSection,
)


def test_sidebar_has_fixed_width() -> None:
    sb = Sidebar()
    assert sb.styles.width is not None
    assert str(sb.styles.width) == "35"


def test_sidebar_default_visibility_is_hidden() -> None:
    sb = Sidebar()
    assert "hidden" in sb.classes


def test_header_section_renders_topic_and_cwd(tmp_path: Path) -> None:
    section = SidebarHeaderSection()
    section.set_state(topic="refactor auth", cwd=str(tmp_path))
    text = str(section.render())
    assert "refactor auth" in text
    assert str(tmp_path) in text or tmp_path.name in text


def test_header_section_truncates_long_topic() -> None:
    section = SidebarHeaderSection()
    section.set_state(topic="x" * 80, cwd="/tmp")
    text = str(section.render())
    assert len(max(text.splitlines(), key=len)) <= 33


def test_context_section_renders_provider_model_pct() -> None:
    section = SidebarContextSection()
    section.set_state(provider="anthropic", model="claude-sonnet-4-6", pct=42)
    text = str(section.render())
    assert "anthropic" in text
    assert "claude-sonnet-4-6" in text
    assert "42" in text


def test_tools_section_collapsed_shows_count() -> None:
    section = SidebarToolsSection()
    section.set_state(["Bash", "FileRead", "FileWrite"])
    text = str(section.render())
    assert "3" in text
    assert "Bash" not in text


def test_tools_section_expanded_lists_names() -> None:
    section = SidebarToolsSection()
    section.set_state(["Bash", "FileRead"])
    section.toggle_expanded()
    text = str(section.render())
    assert "Bash" in text
    assert "FileRead" in text


def test_mcp_section_renders_servers_with_status() -> None:
    section = SidebarMcpSection()
    section.set_state([
        ("nerdvana", "connected"),
        ("context7", "connected"),
        ("brave", "error"),
    ])
    text = str(section.render())
    assert "nerdvana" in text
    assert "brave" in text
    for name in ["nerdvana", "context7", "brave"]:
        assert name in text


def test_skills_section_collapsed_by_default_shows_count() -> None:
    section = SidebarSkillsSection()
    section.set_state(["/debug", "/explain", "/review", "/compress"])
    text = str(section.render())
    assert "4" in text
    assert "/debug" not in text


def test_skills_section_expanded_lists_triggers() -> None:
    section = SidebarSkillsSection()
    section.set_state(["/debug", "/explain"])
    section.toggle_expanded()
    text = str(section.render())
    assert "/debug" in text
    assert "/explain" in text


def test_tasks_section_shows_running_count_and_rows() -> None:
    reg = TaskRegistry()
    task = TaskState(id="t1", description="plan refactor")
    task.status = TaskStatus.RUNNING
    reg.register(task)
    section = SidebarTasksSection(registry=reg)
    section.refresh_rows()
    text = str(section.render())
    assert "TASKS" in text
    assert "1" in text
    assert "plan refactor" in text
