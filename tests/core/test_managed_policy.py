"""Managed policy: drop-in loading, merging, enforcement over user settings, and refusal of models.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.managed_policy import (
    ManagedPolicy,
    ManagedPolicyError,
    load_managed_policy,
    scan_managed_policy,
)
from nerdvana_cli.core.settings import NerdvanaSettings, SettingsLoadError


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated managed directory, named by NERDVANA_MANAGED_DIR; the system directory is an empty temp one."""
    directory = tmp_path / "managed"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_MANAGED_DIR", str(directory))
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(core_paths, "system_managed_dir", lambda: tmp_path / "system")
    monkeypatch.chdir(tmp_path)
    return directory


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _settings_with(tmp_path: Path, config: str = "") -> NerdvanaSettings:
    if config:
        (tmp_path / "nerdvana.yml").write_text(config, encoding="utf-8")
    return NerdvanaSettings.load()


class TestDiscovery:
    def test_no_directory_means_no_policy(self, managed: Path) -> None:
        managed.rmdir()
        policy, problems = scan_managed_policy([core_paths.system_managed_dir()])
        assert not policy.active and problems == []

    def test_an_empty_directory_is_no_policy(self, managed: Path) -> None:
        assert not load_managed_policy().active

    def test_files_are_read_in_lexical_order(self, managed: Path) -> None:
        _write(managed, "20-b.yml", "session:\n  max_cost_usd: 5\n")
        _write(managed, "10-a.yaml", "session:\n  max_cost_usd: 9\n")
        _write(managed, "05-c.yml", "sandbox:\n  mode: auto\n")
        assert [Path(f.path).name for f in load_managed_policy().files] == ["05-c.yml", "10-a.yaml", "20-b.yml"]

    def test_the_system_directory_wins_a_tie_on_the_file_name(self, managed: Path, tmp_path: Path) -> None:
        system = tmp_path / "system"
        system.mkdir()
        _write(system, "10-x.yml", "sandbox:\n  mode: auto\n")
        _write(managed, "10-x.yml", "sandbox:\n  mode: require\n")
        files = [f.path for f in load_managed_policy().files]
        assert files == [str(system / "10-x.yml"), str(managed / "10-x.yml")]

    def test_other_files_are_ignored(self, managed: Path) -> None:
        _write(managed, "notes.txt", "this: is: not yaml: [")
        _write(managed, ".hidden.yml", "this: is: not yaml: [")
        (managed / "sub.yml").mkdir()
        _write(managed, "10-real.yml", "sandbox:\n  mode: auto\n")
        assert [Path(f.path).name for f in load_managed_policy().files] == ["10-real.yml"]

    def test_an_empty_file_is_valid_and_sets_nothing(self, managed: Path) -> None:
        _write(managed, "10-empty.yml", "")
        policy = load_managed_policy()
        assert policy.active and policy.files[0].keys == ()

    def test_the_environment_directory_must_exist(self, managed: Path) -> None:
        managed.rmdir()
        with pytest.raises(ManagedPolicyError, match="does not exist"):
            load_managed_policy()


class TestMalformedFilesFailClosed:
    @pytest.mark.parametrize(("text", "expected"), [
        ("model: [unclosed\n", "10-bad.yml"),
        ("- just\n- a list\n", "top level must be a mapping"),
        ("modle:\n  allowed_models: [x]\n", "unknown key 'modle.allowed_models'"),
        ("model:\n  allowed: [x]\n", "unknown key 'model.allowed'"),
        ("model: text\n", "model: expected a mapping"),
        ("model:\n  allowed_models: claude-*\n", "model.allowed_models: expected a list"),
        ("model:\n  allowed_models: []\n", "empty allow-list"),
        ("model:\n  denied_models: [1]\n", "model.denied_models: expected a list of non-empty strings"),
        ("model:\n  denied_models: ['']\n", "model.denied_models: expected a list of non-empty strings"),
        ("mcp:\n  allowed_servers: {a: b}\n", "mcp.allowed_servers: expected a list"),
        ("hooks:\n  allow_project_hooks: no_idea\n", "hooks.allow_project_hooks: expected true or false"),
        ("hooks:\n  allow_project_hooks: 0\n", "hooks.allow_project_hooks: expected true or false"),
        ("session:\n  max_cost_usd: 0\n", "session.max_cost_usd: expected a number above 0"),
        ("session:\n  max_cost_usd: -3\n", "session.max_cost_usd: expected a number above 0"),
        ("session:\n  max_cost_usd: true\n", "session.max_cost_usd: expected a number above 0"),
        ("session:\n  max_cost_usd: lots\n", "session.max_cost_usd: expected a number above 0"),
        ("sandbox:\n  mode: strict\n", "sandbox.mode: expected one of off, auto, require"),
    ])
    def test_the_exact_problem_is_reported(self, managed: Path, text: str, expected: str) -> None:
        path = _write(managed, "10-bad.yml", text)
        with pytest.raises(ManagedPolicyError) as caught:
            load_managed_policy()
        assert str(path) in str(caught.value)
        assert expected in str(caught.value)

    def test_every_broken_file_is_listed(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "sandbox:\n  mode: nope\n")
        _write(managed, "20-b.yml", "session:\n  max_cost_usd: -1\n")
        _write(managed, "30-ok.yml", "sandbox:\n  mode: auto\n")
        with pytest.raises(ManagedPolicyError) as caught:
            load_managed_policy()
        assert len(caught.value.problems) == 2
        policy, problems = scan_managed_policy()
        assert len(problems) == 2 and [Path(f.path).name for f in policy.files] == ["30-ok.yml"]

    def test_an_unreadable_file_is_an_error_not_a_skip(self, managed: Path) -> None:
        (managed / "10-x.yml").write_bytes(b"\xff\xfe\x00bad")
        with pytest.raises(ManagedPolicyError, match="10-x.yml"):
            load_managed_policy()

    def test_settings_load_refuses_to_start_with_the_exact_error(self, managed: Path, tmp_path: Path) -> None:
        path = _write(managed, "10-bad.yml", "sandbox:\n  mode: nope\n")
        with pytest.raises(SettingsLoadError) as caught:
            NerdvanaSettings.load()
        assert str(path) in str(caught.value) and "sandbox.mode" in str(caught.value)


class TestMerge:
    def test_denied_models_and_always_deny_are_added_up(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "model:\n  denied_models: [a-*]\npermissions:\n  always_deny: ['Bash(rm *)']\n")
        _write(managed, "20-b.yml", "model:\n  denied_models: [b-*, a-*]\npermissions:\n  always_deny: ['Bash(sudo *)']\n")
        policy = load_managed_policy()
        assert policy.model_refusal("a-1") and policy.model_refusal("b-1") and policy.model_refusal("c-1") is None
        settings = SimpleNamespace(permissions=SimpleNamespace(always_deny=[]), model=SimpleNamespace(fallback_models=[], provider=""),
                                   agents=SimpleNamespace(categories={}), session=SimpleNamespace(escalation_model=""))
        policy.apply(settings)  # type: ignore[arg-type]
        assert settings.permissions.always_deny == ["Bash(rm *)", "Bash(sudo *)"]

    def test_allow_lists_must_all_be_satisfied(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "model:\n  allowed_models: ['claude-*', 'gpt-*']\n")
        _write(managed, "20-b.yml", "model:\n  allowed_models: ['claude-*']\n")
        policy = load_managed_policy()
        assert policy.model_refusal("claude-sonnet-5-5") is None
        assert "20-b.yml" in (policy.model_refusal("gpt-4o") or "")

    def test_the_cost_ceiling_is_the_smallest(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "session:\n  max_cost_usd: 8\n")
        _write(managed, "20-b.yml", "session:\n  max_cost_usd: 3.5\n")
        settings = NerdvanaSettings.load()
        assert settings.session.max_cost_usd == 3.5

    def test_the_sandbox_floor_is_the_strictest(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "sandbox:\n  mode: require\n")
        _write(managed, "20-b.yml", "sandbox:\n  mode: auto\n")
        assert NerdvanaSettings.load().sandbox.mode == "require"

    def test_hooks_false_wins_over_true(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-a.yml", "hooks:\n  allow_project_hooks: false\n")
        _write(managed, "20-b.yml", "hooks:\n  allow_project_hooks: true\n")
        assert _settings_with(tmp_path, "hooks:\n  allow_project_hooks: true\n").hooks.allow_project_hooks is False

    def test_hooks_true_alone_leaves_the_user_choice(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-a.yml", "hooks:\n  allow_project_hooks: true\n")
        assert _settings_with(tmp_path, "hooks:\n  allow_project_hooks: true\n").hooks.allow_project_hooks is True


class TestModelRefusal:
    def test_denied_names_the_file_and_the_pattern(self, managed: Path) -> None:
        path = _write(managed, "10-deny.yml", "model:\n  denied_models: ['gpt-*', 'o1*']\n")
        reason = load_managed_policy().model_refusal("o1-mini") or ""
        assert str(path) in reason and "'o1*'" in reason and "model.denied_models" in reason

    def test_not_allowed_names_the_file(self, managed: Path) -> None:
        path = _write(managed, "10-allow.yml", "model:\n  allowed_models: ['claude-*']\n")
        reason = load_managed_policy().model_refusal("gpt-4o") or ""
        assert str(path) in reason and "model.allowed_models" in reason

    def test_deny_beats_allow(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "model:\n  allowed_models: ['claude-*']\n  denied_models: ['claude-opus*']\n")
        policy = load_managed_policy()
        assert policy.model_refusal("claude-sonnet-5-5") is None
        assert policy.model_refusal("claude-opus-4") is not None

    def test_globs_are_case_sensitive(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "model:\n  allowed_models: ['claude-*']\n")
        assert load_managed_policy().model_refusal("Claude-x") is not None

    def test_a_pattern_may_name_the_provider(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "model:\n  allowed_models: ['anthropic:*']\n")
        policy = load_managed_policy()
        assert policy.model_refusal("claude-x", "anthropic") is None
        assert policy.model_refusal("claude-x", "openai") is not None
        assert policy.model_refusal("claude-x") is not None

    def test_no_model_rules_allow_everything(self, managed: Path) -> None:
        _write(managed, "10-a.yml", "sandbox:\n  mode: auto\n")
        assert load_managed_policy().model_refusal("anything") is None

    def test_an_empty_policy_allows_everything(self) -> None:
        assert ManagedPolicy().model_refusal("anything") is None


class TestEnforcementAboveUserSettings:
    def test_user_config_cannot_weaken_the_controls(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-p.yml", (
            "sandbox:\n  mode: require\n"
            "session:\n  max_cost_usd: 2\n"
            "hooks:\n  allow_project_hooks: false\n"
            "permissions:\n  always_deny: ['Bash(curl *)']\n"
        ))
        settings = _settings_with(tmp_path, (
            "sandbox:\n  mode: 'off'\n"
            "session:\n  max_cost_usd: 100\n"
            "hooks:\n  allow_project_hooks: true\n"
            "permissions:\n  always_allow: ['Bash(curl *)']\n  always_deny: []\n"
        ))
        assert settings.sandbox.mode == "require"
        assert settings.session.max_cost_usd == 2
        assert settings.hooks.allow_project_hooks is False
        assert settings.permissions.always_deny == ["Bash(curl *)"]

    def test_a_lower_user_ceiling_and_a_stricter_sandbox_stay(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-p.yml", "sandbox:\n  mode: auto\nsession:\n  max_cost_usd: 5\n")
        settings = _settings_with(tmp_path, "sandbox:\n  mode: require\nsession:\n  max_cost_usd: 1.5\n")
        assert settings.sandbox.mode == "require" and settings.session.max_cost_usd == 1.5

    def test_no_user_ceiling_gets_the_managed_one(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-p.yml", "session:\n  max_cost_usd: 5\n")
        assert _settings_with(tmp_path).session.max_cost_usd == 5

    def test_the_controls_hold_after_later_overrides(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-p.yml", "sandbox:\n  mode: auto\nsession:\n  max_cost_usd: 5\npermissions:\n  always_deny: ['Bash(rm *)']\n")
        settings = _settings_with(tmp_path)
        settings.sandbox.mode = "off"
        settings.session.max_cost_usd = 50
        settings.permissions.always_deny.clear()
        settings.managed_policy.enforce(settings)
        assert (settings.sandbox.mode, settings.session.max_cost_usd, settings.permissions.always_deny) == ("auto", 5, ["Bash(rm *)"])

    def test_the_applied_record_says_what_it_changed(self, managed: Path, tmp_path: Path) -> None:
        path = _write(managed, "10-p.yml", "sandbox:\n  mode: require\nsession:\n  max_cost_usd: 5\n")
        settings = _settings_with(tmp_path, "sandbox:\n  mode: 'off'\nsession:\n  max_cost_usd: 1\n")
        settings.managed_policy.enforce(settings)
        records = {item.key: item for item in settings.managed_policy.applied}
        assert records["sandbox.mode"].changed is True and records["sandbox.mode"].sources == (str(path),)
        assert records["session.max_cost_usd"].changed is False

    def test_settings_built_without_load_have_an_empty_policy(self) -> None:
        settings = NerdvanaSettings()
        assert not settings.managed_policy.active
        settings.managed_policy.enforce(settings)


class TestModelsAreRefusedAtStartup:
    def test_enforce_refuses_the_configured_model_with_the_reason(self, managed: Path, tmp_path: Path) -> None:
        path = _write(managed, "10-m.yml", "model:\n  allowed_models: ['claude-*']\n")
        settings = _settings_with(tmp_path, "model:\n  model: gpt-4o\n")
        with pytest.raises(ManagedPolicyError) as caught:
            settings.managed_policy.enforce(settings)
        assert "gpt-4o" in str(caught.value) and str(path) in str(caught.value)

    def test_load_alone_does_not_refuse_so_a_flag_can_pick_an_allowed_model(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-m.yml", "model:\n  allowed_models: ['claude-*']\n")
        settings = _settings_with(tmp_path, "model:\n  model: gpt-4o\n")
        settings.model.model = "claude-sonnet-5-5"
        settings.managed_policy.enforce(settings)

    def test_an_allowed_model_passes(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-m.yml", "model:\n  allowed_models: ['claude-*']\n")
        settings = _settings_with(tmp_path)
        settings.managed_policy.enforce(settings)

    def test_fallback_escalation_and_category_models_outside_the_policy_are_dropped(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-m.yml", "model:\n  allowed_models: ['claude-*']\n")
        settings = _settings_with(tmp_path, (
            "model:\n  fallback_models: [claude-haiku-4-5, 'openai:gpt-4o', gpt-4o-mini]\n"
            "session:\n  escalation_model: gpt-5\n"
            "agents:\n  categories: {quick: claude-haiku-4-5, deep: 'openai:o3'}\n"
        ))
        settings.managed_policy.enforce(settings)
        assert settings.model.fallback_models == ["claude-haiku-4-5"]
        assert settings.session.escalation_model == ""
        assert settings.agents.categories == {"quick": "claude-haiku-4-5"}
        keys = {item.key for item in settings.managed_policy.applied}
        assert {"model.fallback_models", "session.escalation_model", "agents.categories"} <= keys


class TestAudit:
    def test_enforce_appends_one_record_with_files_and_keys(self, managed: Path, tmp_path: Path) -> None:
        path = _write(managed, "10-a.yml", "sandbox:\n  mode: auto\n")
        settings = _settings_with(tmp_path)
        settings.managed_policy.enforce(settings)
        settings.managed_policy.enforce(settings)
        lines = core_paths.managed_audit_path().read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        record = json.loads(lines[0])
        assert record["files"] == [str(path)] and record["refused"] is None
        assert record["applied"][0]["key"] == "sandbox.mode" and record["applied"][0]["changed"] is True

    def test_a_refusal_is_recorded(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-a.yml", "model:\n  denied_models: ['*']\n")
        settings = _settings_with(tmp_path)
        with pytest.raises(ManagedPolicyError):
            settings.managed_policy.enforce(settings)
        record = json.loads(core_paths.managed_audit_path().read_text(encoding="utf-8").splitlines()[-1])
        assert "denied" in record["refused"]

    def test_nothing_is_written_without_a_policy(self, managed: Path, tmp_path: Path) -> None:
        settings = _settings_with(tmp_path)
        settings.managed_policy.enforce(settings)
        assert not core_paths.managed_audit_path().exists()

    def test_describe_lists_files_and_controls(self, managed: Path, tmp_path: Path) -> None:
        _write(managed, "10-a.yml", "sandbox:\n  mode: auto\n")
        settings = _settings_with(tmp_path)
        text = "\n".join(settings.managed_policy.describe())
        assert "10-a.yml" in text and "sandbox.mode = auto" in text
        assert ManagedPolicy().describe() == ["No managed policy files."]


class TestMcpAllowList:
    def _servers(self, tmp_path: Path, *names: str) -> None:
        servers = {name: {"command": "true"} for name in names}
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def test_servers_outside_the_list_are_left_out(self, managed: Path, tmp_path: Path) -> None:
        from nerdvana_cli.mcp.config import load_mcp_config

        _write(managed, "10-a.yml", "mcp:\n  allowed_servers: [docs, 'team-*']\n")
        self._servers(tmp_path, "docs", "team-search", "random")
        assert sorted(load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "none.json"))) == ["docs", "team-search"]

    def test_an_empty_list_blocks_every_server(self, managed: Path, tmp_path: Path) -> None:
        from nerdvana_cli.mcp.config import load_mcp_config

        _write(managed, "10-a.yml", "mcp:\n  allowed_servers: []\n")
        self._servers(tmp_path, "docs")
        assert load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "none.json")) == {}

    def test_without_a_list_every_server_stays(self, managed: Path, tmp_path: Path) -> None:
        from nerdvana_cli.mcp.config import load_mcp_config

        self._servers(tmp_path, "docs", "random")
        assert len(load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "none.json"))) == 2

    def test_the_refusal_names_the_file(self, managed: Path) -> None:
        path = _write(managed, "10-a.yml", "mcp:\n  allowed_servers: [docs]\n")
        assert str(path) in (load_managed_policy().server_refusal("other") or "")


class TestEntryPoints:
    def test_apply_model_spec_refuses_a_model_outside_the_policy(self, managed: Path, tmp_path: Path) -> None:
        from nerdvana_cli.core.model_routing import apply_model_spec

        _write(managed, "10-a.yml", "model:\n  allowed_models: ['claude-*']\n")
        settings = _settings_with(tmp_path)
        before = settings.model.model
        assert apply_model_spec(settings, "gpt-4o") is False
        assert settings.model.model == before

    @pytest.mark.asyncio
    async def test_the_model_command_refuses_with_the_reason(self, managed: Path, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from nerdvana_cli.commands.model_commands import handle_model

        path = _write(managed, "10-a.yml", "model:\n  denied_models: ['gpt-*']\n")
        settings = _settings_with(tmp_path)
        app = MagicMock()
        app.settings = settings
        before = settings.model.model
        await handle_model(app, "gpt-4o")
        assert settings.model.model == before
        shown = app._add_chat_message.call_args[0][0]
        assert "gpt-4o" in shown and str(path) in shown
        app._agent_loop.create_provider_from_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_policy_command_lists_the_files(self, managed: Path, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from nerdvana_cli.commands.system_commands import handle_policy

        _write(managed, "10-a.yml", "sandbox:\n  mode: auto\n")
        app = MagicMock()
        app.settings = _settings_with(tmp_path)
        await handle_policy(app, "")
        assert "10-a.yml" in app._add_chat_message.call_args[0][0]

    def test_run_overrides_cannot_loosen_the_policy_and_a_refused_model_ends_the_command(self, managed: Path, tmp_path: Path) -> None:
        import click

        from nerdvana_cli.main import _apply_run_overrides

        _write(managed, "10-a.yml", "sandbox:\n  mode: require\nmodel:\n  allowed_models: ['claude-*']\n")
        settings = _settings_with(tmp_path)
        _apply_run_overrides(settings, {"sandbox.mode": "off", "session.max_cost_usd": 0.0}, [])
        assert settings.sandbox.mode == "require"
        with pytest.raises(click.exceptions.Exit) as caught:
            _apply_run_overrides(settings, {"model.model": "gpt-4o"}, [])
        assert caught.value.exit_code == 2

    def test_the_run_command_refuses_a_model_outside_the_policy(self, managed: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        path = _write(managed, "10-a.yml", "model:\n  allowed_models: ['claude-*']\n")
        result = CliRunner().invoke(app, ["run", "hello", "--model", "gpt-4o"])
        assert result.exit_code == 2
        assert "gpt-4o" in result.output and str(path) in result.output

    def test_a_malformed_managed_file_stops_the_run_command(self, managed: Path) -> None:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        path = _write(managed, "10-a.yml", "sandbox:\n  mode: nope\n")
        result = CliRunner().invoke(app, ["run", "hello"])
        assert result.exit_code == 2
        assert str(path) in result.output and "sandbox.mode" in result.output


class TestDoctor:
    def test_skip_without_files(self, managed: Path) -> None:
        from nerdvana_cli.commands.doctor_policy import check_managed_policy

        assert check_managed_policy().status == "skip"

    def test_ok_lists_the_files(self, managed: Path) -> None:
        from nerdvana_cli.commands.doctor_policy import check_managed_policy

        path = _write(managed, "10-a.yml", "sandbox:\n  mode: auto\n")
        result = check_managed_policy()
        assert result.status == "ok" and str(path) in result.detail

    def test_fail_gives_the_exact_error_of_every_broken_file(self, managed: Path) -> None:
        from nerdvana_cli.commands.doctor_policy import check_managed_policy

        first  = _write(managed, "10-a.yml", "sandbox:\n  mode: nope\n")
        second = _write(managed, "20-b.yml", "weird:\n  a: 1\n")
        result = check_managed_policy()
        assert result.status == "fail"
        assert str(first) in result.detail and str(second) in result.detail and "unknown key" in result.detail

    def test_the_other_checks_survive_a_broken_file(self, managed: Path) -> None:
        from nerdvana_cli.commands import doctor_command as dc

        _write(managed, "10-a.yml", "sandbox:\n  mode: nope\n")
        assert dc._check_mcp_servers().status == "skip"
        assert dc._check_config_warnings().status == "fail"
