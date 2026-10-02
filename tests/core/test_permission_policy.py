"""Central permission policy: rule order and mode resolution.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

import pytest

from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.policy import PermissionPolicy
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import PermissionBehavior, PermissionResult, ToolResult

ALLOW = PermissionResult(PermissionBehavior.ALLOW)
ASK   = PermissionResult(PermissionBehavior.ASK, "tool asks")
DENY  = PermissionResult(PermissionBehavior.DENY, "tool denies")


class _Tool(BaseTool[Any]):
    name             = "Probe"
    description_text = "probe"

    def __init__(self, name: str = "Probe", category: ToolCategory = ToolCategory.READ) -> None:
        self.name     = name
        self.category = category  # type: ignore[misc]

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ran")


def _write() -> _Tool:
    return _Tool("FileWrite", ToolCategory.WRITE)


def test_always_deny_beats_always_allow_and_tool_allow() -> None:
    policy = PermissionPolicy(always_allow=["FileWrite"], always_deny=["File*"])
    assert policy.decide(_write(), ALLOW).behavior == PermissionBehavior.DENY


def test_mode_exclusion_denies_even_when_allow_listed() -> None:
    policy = PermissionPolicy(mode_name="planning", always_allow=["FileWrite"], excluded_tools=["FileWrite"])
    result = policy.decide(_write(), ALLOW)
    assert result.behavior == PermissionBehavior.DENY
    assert "planning" in result.message


def test_tool_deny_is_not_overridden_by_allow_list_or_yolo() -> None:
    policy = PermissionPolicy(trust_level="yolo", always_allow=["Bash"])
    assert policy.decide(_Tool("Bash", ToolCategory.WRITE), DENY).behavior == PermissionBehavior.DENY


def test_allow_list_turns_tool_ask_into_allow() -> None:
    policy = PermissionPolicy(always_allow=["Bash"])
    assert policy.decide(_Tool("Bash", ToolCategory.WRITE), ASK).behavior == PermissionBehavior.ALLOW


@pytest.mark.parametrize(
    ("trust", "category", "expected"),
    [
        ("strict",   ToolCategory.WRITE,       PermissionBehavior.ASK),
        ("strict",   ToolCategory.READ,        PermissionBehavior.ALLOW),
        ("balanced", ToolCategory.WRITE,       PermissionBehavior.ALLOW),
        ("balanced", ToolCategory.DESTRUCTIVE, PermissionBehavior.ASK),
        ("yolo",     ToolCategory.DESTRUCTIVE, PermissionBehavior.ALLOW),
    ],
)
def test_trust_level_on_tool_allow(trust: str, category: ToolCategory, expected: PermissionBehavior) -> None:
    policy = PermissionPolicy(trust_level=trust)
    assert policy.decide(_Tool("X", category), ALLOW).behavior == expected


def test_yolo_turns_tool_ask_into_allow() -> None:
    assert PermissionPolicy(trust_level="yolo").decide(_write(), ASK).behavior == PermissionBehavior.ALLOW


def test_included_tools_hide_everything_else() -> None:
    policy = PermissionPolicy(included_tools=["FileRead"])
    assert policy.is_visible("FileRead")
    assert not policy.is_visible("Bash")


def _settings(tmp_path: Any, **session: Any) -> NerdvanaSettings:
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    for key, value in session.items():
        setattr(settings.session, key, value)
    return settings


def test_from_settings_reads_planning_mode_exclusions(tmp_path: Any) -> None:
    policy = PermissionPolicy.from_settings(_settings(tmp_path, default_mode="planning"))
    assert policy.mode_name == "planning"
    assert not policy.is_visible("FileWrite")
    assert not policy.is_visible("Bash")


def test_from_settings_maps_yaml_permission_mode(tmp_path: Any) -> None:
    settings = _settings(tmp_path)
    settings.permissions.mode         = "bypass"
    settings.permissions.always_allow = ["Bash"]
    policy = PermissionPolicy.from_settings(settings)
    assert policy.mode_name == "one-shot"
    assert policy.trust_level == "yolo"
    assert policy.always_allow == ["Bash"]


def test_from_settings_unknown_mode_falls_back(tmp_path: Any) -> None:
    policy = PermissionPolicy.from_settings(_settings(tmp_path, default_mode="no-such-mode"))
    assert policy.mode_name == "interactive"


async def test_executor_applies_policy_before_running_the_tool(tmp_path: Any) -> None:
    tool     = _write()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        registry = registry,
        hooks    = HookEngine(),
        settings = NerdvanaSettings(),
        policy   = PermissionPolicy(always_deny=["FileWrite"]),
    )
    results = await executor.run_batch(
        [{"id": "call_1", "name": "FileWrite", "input": {}}],
        ToolContext(cwd=str(tmp_path)),
    )
    assert results[0].is_error
    assert "always_deny" in results[0].content


def test_requires_confirmation_asks_outside_yolo() -> None:
    tool = _Tool("TaskStop", ToolCategory.META)
    tool.requires_confirmation = True  # type: ignore[misc]
    assert PermissionPolicy().decide(tool, ALLOW).behavior == PermissionBehavior.ASK
    assert PermissionPolicy(trust_level="yolo").decide(tool, ALLOW).behavior == PermissionBehavior.ALLOW
