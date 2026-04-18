"""Tests for --approval-mode CLI flag → mode mapping (Phase F §6.3).

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import pytest

from nerdvana_cli.main import _APPROVAL_MODE_MAP


class TestApprovalModeMap:
    def test_default_maps_to_interactive_balanced(self) -> None:
        mode, trust = _APPROVAL_MODE_MAP["default"]
        assert mode  == "interactive"
        assert trust == "balanced"

    def test_auto_edit_maps_to_editing_balanced(self) -> None:
        mode, trust = _APPROVAL_MODE_MAP["auto_edit"]
        assert mode  == "editing"
        assert trust == "balanced"

    def test_yolo_maps_to_one_shot_yolo(self) -> None:
        mode, trust = _APPROVAL_MODE_MAP["yolo"]
        assert mode  == "one-shot"
        assert trust == "yolo"

    def test_plan_maps_to_planning_strict(self) -> None:
        mode, trust = _APPROVAL_MODE_MAP["plan"]
        assert mode  == "planning"
        assert trust == "strict"

    def test_all_four_entries_present(self) -> None:
        assert set(_APPROVAL_MODE_MAP.keys()) == {"default", "auto_edit", "yolo", "plan"}


class TestPlanningGateCompat:
    """Ensure planning_gate=true in settings maps to default_mode=planning."""

    def test_planning_gate_compat(self) -> None:
        from nerdvana_cli.core.settings import NerdvanaSettings
        import yaml, os, tempfile

        cfg = {"session": {"planning_gate": True}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8"
        ) as fh:
            yaml.dump(cfg, fh)
            path = fh.name

        try:
            s = NerdvanaSettings.load(path)
            assert s.session.default_mode == "planning"
        finally:
            os.unlink(path)
