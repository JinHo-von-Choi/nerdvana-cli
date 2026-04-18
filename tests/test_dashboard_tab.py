"""Tests for DashboardTab widget and helper functions."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pure helper function tests (no Textual runtime needed)
# ---------------------------------------------------------------------------

class TestSparkline:
    def test_empty_returns_spaces(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _sparkline
        result = _sparkline([], width=10)
        assert len(result) == 10
        assert result.strip() == ""

    def test_single_value(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _sparkline
        result = _sparkline([100], width=5)
        assert len(result) == 5
        # Last char should be max block
        assert result[-1] == "█"

    def test_width_respected(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _sparkline
        result = _sparkline([1, 2, 3, 4, 5], width=4)
        assert len(result) == 4

    def test_zero_values(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _sparkline
        result = _sparkline([0, 0, 0], width=3)
        assert len(result) == 3


class TestBar:
    def test_full_bar(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _bar
        result = _bar(10, 10, width=10)
        assert result == "█" * 10

    def test_empty_bar(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _bar
        result = _bar(0, 10, width=10)
        assert result == "░" * 10

    def test_half_bar(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _bar
        result = _bar(5, 10, width=10)
        assert result.count("█") == 5
        assert result.count("░") == 5

    def test_zero_max(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import _bar
        result = _bar(5, 0, width=8)
        assert result == "░" * 8


# ---------------------------------------------------------------------------
# Widget class / import tests
# ---------------------------------------------------------------------------

class TestDashboardTabImport:
    def test_dashboard_tab_is_widget(self) -> None:
        from textual.widget import Widget
        from nerdvana_cli.ui.dashboard_tab import DashboardTab
        assert issubclass(DashboardTab, Widget)

    def test_session_header_is_static(self) -> None:
        from textual.widgets import Static
        from nerdvana_cli.ui.dashboard_tab import SessionHeader
        assert issubclass(SessionHeader, Static)

    def test_tool_heatmap_is_static(self) -> None:
        from textual.widgets import Static
        from nerdvana_cli.ui.dashboard_tab import ToolHeatmap
        assert issubclass(ToolHeatmap, Static)

    def test_failure_rate_panel_is_static(self) -> None:
        from textual.widgets import Static
        from nerdvana_cli.ui.dashboard_tab import FailureRatePanel
        assert issubclass(FailureRatePanel, Static)

    def test_token_sparkline_is_static(self) -> None:
        from textual.widgets import Static
        from nerdvana_cli.ui.dashboard_tab import TokenSparkline
        assert issubclass(TokenSparkline, Static)

    def test_health_footer_is_static(self) -> None:
        from textual.widgets import Static
        from nerdvana_cli.ui.dashboard_tab import HealthFooter
        assert issubclass(HealthFooter, Static)


# ---------------------------------------------------------------------------
# DashboardTab toggle state
# ---------------------------------------------------------------------------

class TestDashboardTabToggle:
    def test_initially_hidden(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import DashboardTab
        tab = DashboardTab()
        assert "active" not in tab.classes

    def test_toggle_adds_active(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import DashboardTab
        tab = DashboardTab()
        tab.toggle()
        assert "active" in tab.classes

    def test_double_toggle_removes_active(self) -> None:
        from nerdvana_cli.ui.dashboard_tab import DashboardTab
        tab = DashboardTab()
        tab.toggle()
        tab.toggle()
        assert "active" not in tab.classes
