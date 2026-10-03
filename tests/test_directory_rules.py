"""Tests for AGENTS.md / CLAUDE.md compatibility and directory-scoped rule injection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nerdvana_cli.core.builtin_hooks import RULE_BUDGET_BYTES, DirectoryRuleInjector
from nerdvana_cli.core.context.nirnamd import format_nirna_for_prompt, load_nirna_files
from nerdvana_cli.core.hooks.hooks import HookContext, HookEvent


def _load(root: Path) -> list[Any]:
    return load_nirna_files(cwd=str(root), global_path=str(root / "no-global.md"))


def _touch(
    injector: DirectoryRuleInjector,
    root:     Path,
    target:   str,
    tool:     str = "FileRead",
) -> list[str]:
    ctx = HookContext(
        event       = HookEvent.AFTER_TOOL,
        settings    = SimpleNamespace(cwd=str(root)),
        tool_name   = tool,
        tool_input  = {"path": target},
        tool_result = SimpleNamespace(is_error=False),
    )
    result = injector.handle(ctx)
    return [m["content"] for m in result.inject_messages] if result else []


class TestRootCompatFiles:
    def test_agents_and_claude_load_after_nirna(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("claude rules")
        (tmp_path / "AGENTS.md").write_text("agents rules")
        (tmp_path / "NIRNA.md").write_text("nirna rules")
        (tmp_path / "NIRNA.local.md").write_text("local rules")

        files = _load(tmp_path)

        names = [Path(f.path).name for f in files]
        assert names == ["NIRNA.md", "NIRNA.local.md", "AGENTS.md", "CLAUDE.md"]
        assert all(f.type == "project" for f in files if Path(f.path).name in ("AGENTS.md", "CLAUDE.md"))

    def test_compat_files_reach_prompt_after_nirna(self, tmp_path: Path) -> None:
        (tmp_path / "NIRNA.md").write_text("nirna rules")
        (tmp_path / "AGENTS.md").write_text("agents rules")

        prompt = format_nirna_for_prompt(_load(tmp_path))

        assert prompt is not None
        assert prompt.index("nirna rules") < prompt.index("agents rules")

    def test_compat_file_is_capped_at_50kb(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("a" * 80_000)

        files = _load(tmp_path)

        assert len(files) == 1
        assert len(files[0].content) == 50_000

    def test_symlink_resolving_outside_root_is_skipped(self, tmp_path: Path) -> None:
        root    = tmp_path / "project"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.md").write_text("outside rules")
        (root / "AGENTS.md").symlink_to(outside / "secret.md")

        assert _load(root) == []

    def test_symlink_resolving_inside_root_is_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "rules.md").write_text("inside rules")
        (tmp_path / "AGENTS.md").symlink_to(tmp_path / "docs" / "rules.md")

        files = _load(tmp_path)

        assert [f.content for f in files] == ["inside rules"]


class TestDirectoryRuleInjector:
    def test_subdir_rule_injected_once(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "AGENTS.md").write_text("pkg rules")
        (tmp_path / "pkg" / "mod.py").write_text("x = 1")
        injector = DirectoryRuleInjector()

        first  = _touch(injector, tmp_path, "pkg/mod.py")
        second = _touch(injector, tmp_path, "pkg/mod.py")

        assert len(first) == 1
        assert "pkg rules" in first[0]
        assert "pkg/AGENTS.md" in first[0]
        assert second == []

    @pytest.mark.parametrize("tool", ["FileRead", "FileEdit", "FileWrite"])
    def test_all_file_tools_trigger(self, tmp_path: Path, tool: str) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "CLAUDE.md").write_text("pkg rules")

        assert len(_touch(DirectoryRuleInjector(), tmp_path, "pkg/new.py", tool)) == 1

    def test_other_tools_do_not_trigger(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "CLAUDE.md").write_text("pkg rules")

        assert _touch(DirectoryRuleInjector(), tmp_path, "pkg/mod.py", "Bash") == []

    def test_nearest_first_ordering(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "AGENTS.md").write_text("outer rules")
        (tmp_path / "a" / "b" / "AGENTS.md").write_text("inner rules")

        messages = _touch(DirectoryRuleInjector(), tmp_path, "a/b/file.py")

        assert len(messages) == 2
        assert "inner rules" in messages[0]
        assert "outer rules" in messages[1]

    def test_rule_filenames_in_one_directory_are_all_injected(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "NIRNA.md").write_text("nirna pkg")
        (tmp_path / "pkg" / "AGENTS.md").write_text("agents pkg")
        (tmp_path / "pkg" / "CLAUDE.md").write_text("claude pkg")

        messages = _touch(DirectoryRuleInjector(), tmp_path, "pkg/file.py")

        assert len(messages) == 3
        assert "pkg/NIRNA.md" in messages[0]

    def test_identical_content_is_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "AGENTS.md").write_text("shared rules")
        (tmp_path / "b" / "AGENTS.md").write_text("shared rules")
        injector = DirectoryRuleInjector()

        first  = _touch(injector, tmp_path, "a/x.py")
        second = _touch(injector, tmp_path, "b/y.py")

        assert len(first) == 1
        assert second == []

    def test_total_budget_is_capped_and_truncation_mentioned(self, tmp_path: Path) -> None:
        chunk = 12 * 1024
        for name in ("a", "b", "c"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "AGENTS.md").write_text(name * chunk)
        injector = DirectoryRuleInjector()

        first  = _touch(injector, tmp_path, "a/x.py")
        second = _touch(injector, tmp_path, "b/x.py")
        third  = _touch(injector, tmp_path, "c/x.py")

        assert len(first) == 1
        assert len(second) == 1
        assert len(third) == 1
        assert "budget" in third[0]
        assert "c/AGENTS.md" in third[0]
        assert "c" * chunk not in third[0]
        assert _touch(injector, tmp_path, "c/x.py") == []
        assert RULE_BUDGET_BYTES == 32 * 1024

    def test_outside_root_symlinked_rule_is_ignored(self, tmp_path: Path) -> None:
        root    = tmp_path / "project"
        outside = tmp_path / "outside"
        (root / "pkg").mkdir(parents=True)
        outside.mkdir()
        (outside / "evil.md").write_text("outside rules")
        (root / "pkg" / "AGENTS.md").symlink_to(outside / "evil.md")

        assert _touch(DirectoryRuleInjector(), root, "pkg/mod.py") == []

    def test_file_symlinked_outside_root_is_ignored(self, tmp_path: Path) -> None:
        root    = tmp_path / "project"
        outside = tmp_path / "outside"
        (root / "pkg").mkdir(parents=True)
        outside.mkdir()
        (outside / "AGENTS.md").write_text("outside rules")
        (outside / "data.py").write_text("x = 1")
        (root / "pkg" / "AGENTS.md").write_text("pkg rules")
        (root / "pkg" / "link.py").symlink_to(outside / "data.py")

        assert _touch(DirectoryRuleInjector(), root, "pkg/link.py") == []

    def test_path_outside_root_is_ignored(self, tmp_path: Path) -> None:
        root    = tmp_path / "project"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "AGENTS.md").write_text("outside rules")

        assert _touch(DirectoryRuleInjector(), root, str(outside / "f.py")) == []
        assert _touch(DirectoryRuleInjector(), root, "../outside/f.py") == []

    def test_root_level_file_injects_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("root rules")
        (tmp_path / "NIRNA.md").write_text("root nirna")

        assert _touch(DirectoryRuleInjector(), tmp_path, "main.py") == []

    def test_absolute_path_inside_root_is_handled(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "AGENTS.md").write_text("pkg rules")

        messages = _touch(DirectoryRuleInjector(), tmp_path, str(tmp_path / "pkg" / "mod.py"))

        assert len(messages) == 1

    def test_failed_tool_call_injects_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "AGENTS.md").write_text("pkg rules")
        ctx = HookContext(
            event       = HookEvent.AFTER_TOOL,
            settings    = SimpleNamespace(cwd=str(tmp_path)),
            tool_name   = "FileRead",
            tool_input  = {"path": "pkg/mod.py"},
            tool_result = SimpleNamespace(is_error=True),
        )

        assert DirectoryRuleInjector().handle(ctx) is None

    def test_reset_allows_reinjection(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "AGENTS.md").write_text("pkg rules")
        injector = DirectoryRuleInjector()

        assert len(_touch(injector, tmp_path, "pkg/mod.py")) == 1
        injector.reset()

        assert len(_touch(injector, tmp_path, "pkg/mod.py")) == 1


class TestInjectionDelivery:
    def test_executor_queues_after_tool_injections_until_drained(self, tmp_path: Path) -> None:
        from nerdvana_cli.core.execution.tool_executor import ToolExecutor
        from nerdvana_cli.core.hooks.hooks import HookEngine
        from nerdvana_cli.core.tool import ToolRegistry
        from nerdvana_cli.types import ToolResult

        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "AGENTS.md").write_text("pkg rules")
        hooks = HookEngine()
        hooks.register(HookEvent.AFTER_TOOL, DirectoryRuleInjector().handle)
        executor = ToolExecutor(
            registry = ToolRegistry(),
            hooks    = hooks,
            settings = SimpleNamespace(cwd=str(tmp_path)),
        )
        call = {"id": "call_x", "name": "FileRead", "input": {"path": "pkg/mod.py"}}

        executor._fire_after_tool(call, ToolResult(tool_use_id="call_x", content="ok"))

        drained = executor.drain_injections()
        assert len(drained) == 1
        assert "pkg rules" in drained[0]["content"]
        assert executor.drain_injections() == []
