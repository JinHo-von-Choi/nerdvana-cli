"""Plugins: one folder (or git repository) that installs skills, slash commands, MCP servers and shell hooks together.

A plugin has a ``nerdvana-plugin.yml`` at its root::

    name: review-kit            # lowercase letters, digits and hyphens
    version: 1.0.0
    description: Review skills and commands

and any of ``skills/<skill>/SKILL.md``, ``commands/*.md``, ``mcp.json`` (an ``mcpServers`` object)
and ``hooks.yml`` (a ``hooks`` list). Installing copies the skills into ``~/.nerdvana/skills``,
the commands into ``~/.nerdvana/commands/<name>/`` (so they are ``/<name>:<command>``), adds the
MCP servers to ``~/.nerdvana/mcp.json`` and, only when asked, the hooks to ``~/.nerdvana/hooks.yml``.
What was installed is recorded in ``~/.nerdvana/plugins/<name>.json``, so removing takes back
exactly that and nothing a user added by hand.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core.config import paths

MANIFEST = "nerdvana-plugin.yml"
_NAME    = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PluginError(RuntimeError):
    """A plugin that cannot be installed or removed as asked."""


@dataclass
class InstalledPlugin:
    """What one installed plugin put where."""

    name:        str
    version:     str
    description: str
    source:      str
    paths:       list[str]            = field(default_factory=list)
    mcp_servers: list[str]            = field(default_factory=list)
    hooks:       list[dict[str, Any]] = field(default_factory=list)


def plugins_dir() -> Path:
    """Where the records of installed plugins are kept."""
    return paths.user_data_home() / "plugins"


def installed() -> list[InstalledPlugin]:
    """Every installed plugin, by name."""
    found: list[InstalledPlugin] = []
    for record in sorted(plugins_dir().glob("*.json")):
        try:
            found.append(InstalledPlugin(**json.loads(record.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return found


def install(source: str, force: bool = False, allow_hooks: bool = False) -> InstalledPlugin:
    """Install the plugin at *source* (a directory or a git URL); raises PluginError."""
    with tempfile.TemporaryDirectory(prefix="nerdvana-plugin-") as scratch:
        root = _fetch(source, Path(scratch))
        meta = _manifest(root)
        name = meta["name"]
        if (plugins_dir() / f"{name}.json").exists():
            if not force:
                raise PluginError(f"plugin {name} is installed; remove it first or pass --force")
            remove(name)
        plugin = InstalledPlugin(name=name, version=str(meta.get("version", "")), description=str(meta.get("description", "")), source=source)
        try:
            _copy_skills(root, plugin, force)
            _copy_commands(root, plugin)
            _add_mcp_servers(root, plugin)
            if allow_hooks:
                _add_hooks(root, plugin)
        except PluginError:
            _undo(plugin)
            raise
        hooks_skipped = (root / "hooks.yml").is_file() and not allow_hooks
    plugins_dir().mkdir(parents=True, exist_ok=True)
    (plugins_dir() / f"{name}.json").write_text(json.dumps(asdict(plugin), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if hooks_skipped:
        plugin.hooks = [{"skipped": "the plugin has hooks.yml; install with --allow-hooks to add its shell hooks"}]
    return plugin


def remove(name: str) -> InstalledPlugin:
    """Take back what the plugin installed; raises PluginError when it is not installed."""
    record = plugins_dir() / f"{name}.json"
    if not record.is_file():
        raise PluginError(f"plugin {name} is not installed")
    plugin = InstalledPlugin(**json.loads(record.read_text(encoding="utf-8")))
    _undo(plugin)
    record.unlink()
    return plugin


def _fetch(source: str, scratch: Path) -> Path:
    if Path(source).expanduser().is_dir():
        return Path(source).expanduser().resolve()
    if re.match(r"^(?:https?://|git@|ssh://)", source):
        target = scratch / "repo"
        done = subprocess.run(["git", "clone", "--depth", "1", "--quiet", source, str(target)], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise PluginError(f"git clone {source} failed: {done.stderr.strip()[-300:]}")
        return target
    raise PluginError(f"{source} is neither a directory nor a git URL")


def _manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST
    if not path.is_file():
        raise PluginError(f"{root} has no {MANIFEST}")
    try:
        meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PluginError(f"{MANIFEST} is not valid YAML: {exc}") from exc
    name = meta.get("name") if isinstance(meta, dict) else None
    if not isinstance(name, str) or not _NAME.match(name):
        raise PluginError(f"{MANIFEST} needs a name of lowercase letters, digits and hyphens")
    return meta


def _copy_skills(root: Path, plugin: InstalledPlugin, force: bool) -> None:
    source = root / "skills"
    if not source.is_dir():
        return
    target_root = paths.user_skills_dir()
    for skill in sorted(p for p in source.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        target = target_root / skill.name
        if target.exists():
            if not force:
                raise PluginError(f"skill {skill.name} already exists in {target_root}; pass --force to replace it")
            shutil.rmtree(target)
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill, target, symlinks=False)
        plugin.paths.append(str(target))


def _copy_commands(root: Path, plugin: InstalledPlugin) -> None:
    source = root / "commands"
    files  = sorted(source.rglob("*.md")) if source.is_dir() else []
    if not files:
        return
    target = paths.user_data_home() / "commands" / plugin.name
    if target.exists():
        shutil.rmtree(target)
    for file in files:
        destination = target / file.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, destination)
    plugin.paths.append(str(target))


def _add_mcp_servers(root: Path, plugin: InstalledPlugin) -> None:
    source = root / "mcp.json"
    if not source.is_file():
        return
    try:
        servers = json.loads(source.read_text(encoding="utf-8")).get("mcpServers", {})
    except (ValueError, AttributeError) as exc:
        raise PluginError(f"mcp.json is not an object with mcpServers: {exc}") from exc
    if not isinstance(servers, dict):
        raise PluginError("mcp.json: mcpServers must be an object")
    config = paths.user_mcp_json()
    data   = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    known  = data.setdefault("mcpServers", {})
    clash  = sorted(set(servers) & set(known))
    if clash:
        raise PluginError(f"MCP server(s) {', '.join(clash)} already configured in {config}")
    known.update(servers)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    plugin.mcp_servers = sorted(servers)


def _add_hooks(root: Path, plugin: InstalledPlugin) -> None:
    source = root / "hooks.yml"
    if not source.is_file():
        return
    entries = (yaml.safe_load(source.read_text(encoding="utf-8")) or {}).get("hooks", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise PluginError("hooks.yml: hooks must be a list of mappings")
    target = paths.user_data_home() / "hooks.yml"
    data   = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}) if target.is_file() else {}
    data.setdefault("hooks", []).extend(entries)
    target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    plugin.hooks = list(entries)


def _undo(plugin: InstalledPlugin) -> None:
    for path in plugin.paths:
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    config = paths.user_mcp_json()
    if plugin.mcp_servers and config.is_file():
        data = json.loads(config.read_text(encoding="utf-8"))
        for name in plugin.mcp_servers:
            data.get("mcpServers", {}).pop(name, None)
        config.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    hooks = paths.user_data_home() / "hooks.yml"
    real  = [entry for entry in plugin.hooks if "skipped" not in entry]
    if real and hooks.is_file():
        data = yaml.safe_load(hooks.read_text(encoding="utf-8")) or {}
        kept = list(data.get("hooks", []))
        for entry in real:
            if entry in kept:
                kept.remove(entry)
        data["hooks"] = kept
        hooks.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
