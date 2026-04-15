"""Unit tests for the Sidebar widget and its sections."""
from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.ui.sidebar import Sidebar


def test_sidebar_has_fixed_width() -> None:
    sb = Sidebar()
    assert sb.styles.width is not None
    assert str(sb.styles.width) == "35"


def test_sidebar_default_visibility_is_hidden() -> None:
    sb = Sidebar()
    assert "hidden" in sb.classes


from nerdvana_cli.ui.sidebar_sections import SidebarHeaderSection


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


from nerdvana_cli.ui.sidebar_sections import SidebarContextSection


def test_context_section_renders_provider_model_pct() -> None:
    section = SidebarContextSection()
    section.set_state(provider="anthropic", model="claude-sonnet-4-6", pct=42)
    text = str(section.render())
    assert "anthropic" in text
    assert "claude-sonnet-4-6" in text
    assert "42" in text
