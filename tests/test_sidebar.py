"""Unit tests for the Sidebar widget and its sections."""
from __future__ import annotations

import pytest

from nerdvana_cli.ui.sidebar import Sidebar


def test_sidebar_has_fixed_width() -> None:
    sb = Sidebar()
    assert sb.styles.width is not None
    assert str(sb.styles.width) == "35"


def test_sidebar_default_visibility_is_hidden() -> None:
    sb = Sidebar()
    assert "hidden" in sb.classes
