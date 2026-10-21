"""Plugins: install puts skills, commands, MCP servers and (when allowed) hooks in place; remove takes back exactly that."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nerdvana_cli.cli.plugins import PluginError, install, installed, remove


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


def _plugin(root: Path, name: str = "review-kit") -> Path:
    (root / "skills" / "deep-review").mkdir(parents=True)
    (root / "skills" / "deep-review" / "SKILL.md").write_text("---\nname: deep-review\ndescription: d\n---\nBody\n")
    (root / "commands").mkdir()
    (root / "commands" / "pr.md").write_text("Review the PR $ARGUMENTS\n")
    (root / "mcp.json").write_text(json.dumps({"mcpServers": {"kit-search": {"command": "node", "args": ["s.js"]}}}))
    (root / "hooks.yml").write_text(yaml.safe_dump({"hooks": [{"event": "session_start", "command": "echo hi"}]}))
    (root / "nerdvana-plugin.yml").write_text(yaml.safe_dump({"name": name, "version": "1.0.0", "description": "Review kit"}))
    return root


def test_install_places_everything_but_hooks_unless_allowed(home: Path, tmp_path: Path) -> None:
    plugin = install(str(_plugin(tmp_path / "src")))
    assert (home / "skills" / "deep-review" / "SKILL.md").is_file()
    assert (home / "commands" / "review-kit" / "pr.md").is_file()
    assert "kit-search" in json.loads((home / "mcp.json").read_text())["mcpServers"]
    assert not (home / "hooks.yml").exists()
    assert "allow-hooks" in plugin.hooks[0]["skipped"]
    assert [p.name for p in installed()] == ["review-kit"]


def test_remove_takes_back_only_what_the_plugin_added(home: Path, tmp_path: Path) -> None:
    home.mkdir(parents=True)
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}))
    (home / "hooks.yml").write_text(yaml.safe_dump({"hooks": [{"event": "session_end", "command": "echo mine"}]}))
    install(str(_plugin(tmp_path / "src")), allow_hooks=True)
    assert len(yaml.safe_load((home / "hooks.yml").read_text())["hooks"]) == 2
    remove("review-kit")
    assert not (home / "skills" / "deep-review").exists() and not (home / "commands" / "review-kit").exists()
    assert json.loads((home / "mcp.json").read_text())["mcpServers"] == {"mine": {"command": "x"}}
    assert yaml.safe_load((home / "hooks.yml").read_text())["hooks"] == [{"event": "session_end", "command": "echo mine"}]
    assert installed() == []


def test_a_clashing_mcp_server_aborts_and_leaves_nothing_behind(home: Path, tmp_path: Path) -> None:
    home.mkdir(parents=True)
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"kit-search": {"command": "mine"}}}))
    with pytest.raises(PluginError, match="kit-search"):
        install(str(_plugin(tmp_path / "src")))
    assert not (home / "skills" / "deep-review").exists()
    assert installed() == []


def test_a_second_install_needs_force(home: Path, tmp_path: Path) -> None:
    source = str(_plugin(tmp_path / "src"))
    install(source)
    with pytest.raises(PluginError, match="--force"):
        install(source)
    assert install(source, force=True).name == "review-kit"


def test_a_directory_without_a_manifest_or_with_a_bad_name_is_refused(home: Path, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(PluginError, match="nerdvana-plugin.yml"):
        install(str(tmp_path / "empty"))
    bad = _plugin(tmp_path / "bad", name="Bad Name")
    with pytest.raises(PluginError, match="lowercase"):
        install(str(bad))
