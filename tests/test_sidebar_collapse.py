"""Collapsible sidebar sections must actually change body on toggle."""
from __future__ import annotations

import pytest

from nerdvana_cli.ui.sidebar_sections import SidebarSkillsSection, SidebarToolsSection


def _line_count(section: object) -> int:
    return len(str(section.render()).splitlines())  # type: ignore[attr-defined]


def test_tools_section_expanded_renders_more_lines_than_collapsed() -> None:
    section = SidebarToolsSection()
    section.set_state(["Bash", "FileRead", "FileWrite", "Glob", "Grep"])
    collapsed_lines = _line_count(section)
    section.toggle_expanded()
    expanded_lines = _line_count(section)
    assert expanded_lines > collapsed_lines, (
        f"toggle_expanded did not grow the rendered output: "
        f"{collapsed_lines} -> {expanded_lines}"
    )


def test_skills_section_expanded_contains_every_trigger() -> None:
    section = SidebarSkillsSection()
    triggers = ["/a", "/b", "/c", "/d"]
    section.set_state(triggers)
    section.toggle_expanded()
    rendered = str(section.render())
    for t in triggers:
        assert t in rendered


def test_toggle_twice_returns_to_collapsed_state() -> None:
    section = SidebarToolsSection()
    section.set_state(["Bash"])
    collapsed = _line_count(section)
    section.toggle_expanded()
    section.toggle_expanded()
    assert _line_count(section) == collapsed


@pytest.mark.asyncio
async def test_tools_section_height_grows_when_toggled_in_tui() -> None:
    """Pilot-driven regression: widget.size.height must grow after toggle.

    On main before the fix, self.refresh() kept the cached height so the arrow
    flipped but the body stayed hidden. With refresh(layout=True) Textual
    re-measures and the section height follows render() output.
    """
    from nerdvana_cli.core.settings import NerdvanaSettings
    from nerdvana_cli.ui.app import NerdvanaApp

    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        section = app.query_one("#sidebar-tools", SidebarToolsSection)
        section.set_state(["Bash", "FileRead", "FileWrite", "Glob", "Grep"])
        await pilot.pause()
        collapsed_height = section.size.height

        section.toggle_expanded()
        await pilot.pause()
        expanded_height = section.size.height

        assert expanded_height > collapsed_height, (
            f"widget height did not grow after toggle: "
            f"{collapsed_height} -> {expanded_height}"
        )
