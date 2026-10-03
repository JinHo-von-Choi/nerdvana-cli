"""Managed policy: settings an administrator sets for the machine, above every user and project setting.

Author: 최진호
Date:   2026-10-03

Drop-in files ``*.yml`` and ``*.yaml`` are read from ``/etc/nerdvana/managed-settings.d/`` and,
when set, from the directory named by ``NERDVANA_MANAGED_DIR``. They are merged in lexical order of
the file name (the system directory wins a tie). Every control only restricts, so the merge is the
strictest combination of the files and the order never decides a result: allow-lists must all be
satisfied, deny-lists and ``permissions.always_deny`` are added up, ``session.max_cost_usd`` takes the
smallest value, ``sandbox.mode`` the strictest and ``hooks.allow_project_hooks: false`` wins.

A file that cannot be read, is not valid YAML or holds an unknown key or a wrong type stops the
start with the exact error; nothing is ignored silently.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.sandbox import MODES as SANDBOX_MODES

if TYPE_CHECKING:
    from nerdvana_cli.core.settings import NerdvanaSettings
    from nerdvana_cli.mcp.config import McpServerConfig

logger = logging.getLogger(__name__)

SUFFIXES = (".yml", ".yaml")

# Dotted key -> (source file, value) pairs, one per file that set the key.
Rules = dict[str, list[tuple[str, Any]]]


class ManagedPolicyError(ValueError):
    """A managed file is malformed, or the requested model is outside the policy."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems if problems is not None else [message]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("expected a list of non-empty strings")
    return [item.strip() for item in value]


def _allow_list(value: Any) -> list[str]:
    items = _strings(value)
    if not items:
        raise ValueError("an empty allow-list would refuse everything; remove the key to allow all")
    return items


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"expected true or false, got {type(value).__name__}")
    return value


def _ceiling(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("expected a number above 0")
    return float(value)


def _sandbox_mode(value: Any) -> str:
    if value not in SANDBOX_MODES:
        raise ValueError(f"expected one of {', '.join(SANDBOX_MODES)}")
    return str(value)


_KEYS: dict[str, Callable[[Any], Any]] = {
    "model.allowed_models":      _allow_list,
    "model.denied_models":       _strings,
    "mcp.allowed_servers":       _strings,
    "hooks.allow_project_hooks": _boolean,
    "permissions.always_deny":   _strings,
    "session.max_cost_usd":      _ceiling,
    "sandbox.mode":              _sandbox_mode,
}


@dataclass(frozen=True)
class ManagedFile:
    """One drop-in file that was read, with the keys it set."""

    path: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class AppliedKey:
    """One managed control and what it did to the settings of this run."""

    key:     str
    value:   Any
    sources: tuple[str, ...]
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        """The audit form of this record."""
        return {"key": self.key, "value": self.value, "sources": list(self.sources), "changed": self.changed}


def _flatten(rules: list[tuple[str, Any]]) -> list[Any]:
    """The values of a list-valued key from every file, without repeats, in file order."""
    merged: list[Any] = []
    for _source, values in rules:
        merged.extend(item for item in values if item not in merged)
    return merged


def _effective(rules: list[tuple[str, Any]]) -> Any:
    """The merged value of a key that has no enforcement step of its own."""
    first = rules[0][1]
    return _flatten(rules) if isinstance(first, list) else first


def _apply_always_deny(settings: NerdvanaSettings, rules: list[tuple[str, Any]]) -> tuple[Any, bool]:
    merged  = _flatten(rules)
    current = settings.permissions.always_deny
    missing = [rule for rule in merged if rule not in current]
    current.extend(missing)
    return merged, bool(missing)


def _apply_cost_ceiling(settings: NerdvanaSettings, rules: list[tuple[str, Any]]) -> tuple[Any, bool]:
    ceiling = min(value for _source, value in rules)
    current = settings.session.max_cost_usd
    if current <= 0 or current > ceiling:
        settings.session.max_cost_usd = ceiling
        return ceiling, True
    return ceiling, False


def _apply_project_hooks(settings: NerdvanaSettings, rules: list[tuple[str, Any]]) -> tuple[Any, bool]:
    if any(value is False for _source, value in rules):
        changed = settings.hooks.allow_project_hooks
        settings.hooks.allow_project_hooks = False
        return False, changed
    return True, False


def _apply_sandbox_floor(settings: NerdvanaSettings, rules: list[tuple[str, Any]]) -> tuple[Any, bool]:
    floor = max((value for _source, value in rules), key=SANDBOX_MODES.index)
    if SANDBOX_MODES.index(settings.sandbox.mode) < SANDBOX_MODES.index(floor):
        settings.sandbox.mode = floor
        return floor, True
    return floor, False


_APPLIERS: dict[str, Callable[[Any, list[tuple[str, Any]]], tuple[Any, bool]]] = {
    "permissions.always_deny":   _apply_always_deny,
    "session.max_cost_usd":      _apply_cost_ceiling,
    "hooks.allow_project_hooks": _apply_project_hooks,
    "sandbox.mode":              _apply_sandbox_floor,
}


@dataclass
class ManagedPolicy:
    """The merged managed files, and the checks and the enforcement built on them."""

    files:   list[ManagedFile] = field(default_factory=list)
    rules:   Rules             = field(default_factory=dict)
    applied: list[AppliedKey]  = field(default_factory=list)

    @property
    def active(self) -> bool:
        """True when at least one managed file was read."""
        return bool(self.files)

    def add_file(self, path: str, values: dict[str, Any]) -> None:
        """Merge the validated keys of one file."""
        self.files.append(ManagedFile(path, tuple(values)))
        for key, value in values.items():
            self.rules.setdefault(key, []).append((path, value))

    def sources_of(self, *keys: str) -> tuple[str, ...]:
        """The files that set any of *keys*."""
        found: list[str] = []
        for key in keys:
            found.extend(source for source, _value in self.rules.get(key, []) if source not in found)
        return tuple(found)

    def model_refusal(self, model: str, provider: str = "") -> str | None:
        """Why *model* is outside the policy, naming the file that said so; None when it is allowed.

        A pattern is matched, as a glob, against the model name and against ``provider:model``.
        """
        names = [model, f"{provider}:{model}"] if provider else [model]
        for source, patterns in self.rules.get("model.denied_models", []):
            hit = next((p for p in patterns if any(fnmatchcase(n, p) for n in names)), None)
            if hit:
                return f"model '{model}' is denied by managed policy: {source} lists '{hit}' in model.denied_models"
        for source, patterns in self.rules.get("model.allowed_models", []):
            if not any(fnmatchcase(n, p) for p in patterns for n in names):
                return f"model '{model}' is not in model.allowed_models of managed policy file {source}"
        return None

    def server_refusal(self, name: str) -> str | None:
        """Why the MCP server *name* may not run, naming the file that said so; None when it is allowed."""
        for source, patterns in self.rules.get("mcp.allowed_servers", []):
            if not any(fnmatchcase(name, p) for p in patterns):
                return f"MCP server '{name}' is not in mcp.allowed_servers of managed policy file {source}"
        return None

    def filter_mcp_servers(self, configs: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
        """The configured MCP servers the policy allows; a blocked one is logged with the reason."""
        allowed: dict[str, McpServerConfig] = {}
        for name, config in configs.items():
            reason = self.server_refusal(name)
            if reason:
                logger.warning("%s; it is not started", reason)
            else:
                allowed[name] = config
        return allowed

    def _restrict_models(self, settings: NerdvanaSettings) -> dict[str, list[str]]:
        """Drop the fallback, escalation and category models the policy refuses; returns what was dropped."""
        from nerdvana_cli.core.provider_recovery import parse_fallback

        provider = settings.model.provider

        def refused(spec: str) -> bool:
            other, model = parse_fallback(spec)
            return self.model_refusal(model, other or provider) is not None

        escalation = settings.session.escalation_model
        dropped = {
            "model.fallback_models":    [spec for spec in settings.model.fallback_models if refused(spec)],
            "agents.categories":        [name for name, spec in settings.agents.categories.items() if refused(spec)],
            "session.escalation_model": [escalation] if escalation and refused(escalation) else [],
        }
        settings.model.fallback_models = [s for s in settings.model.fallback_models if s not in dropped["model.fallback_models"]]
        for name in dropped["agents.categories"]:
            del settings.agents.categories[name]
        if dropped["session.escalation_model"]:
            settings.session.escalation_model = ""
        return dropped

    def apply(self, settings: NerdvanaSettings) -> list[AppliedKey]:
        """Force the managed controls onto *settings*; safe to repeat after every later change.

        The primary model is not touched here (see ``enforce``): a command line option may still
        replace it. Returns one record per control, also kept in ``applied``. A record that said
        it changed a setting keeps saying so on later calls.
        """
        previous = {item.key: item for item in self.applied}
        applied: list[AppliedKey] = []
        for key, rules in self.rules.items():
            handler = _APPLIERS.get(key)
            value, changed = handler(settings, rules) if handler else (_effective(rules), False)
            applied.append(AppliedKey(key, value, self.sources_of(key), changed or (key in previous and previous[key].changed)))
        if self.rules.keys() & {"model.allowed_models", "model.denied_models"}:
            sources = self.sources_of("model.allowed_models", "model.denied_models")
            for key, dropped in self._restrict_models(settings).items():
                earlier = previous[key].value if key in previous else []
                kept    = [*earlier, *(item for item in dropped if item not in earlier)]
                if kept:
                    applied.append(AppliedKey(key, kept, sources, True))
        self.applied = applied
        return applied

    def enforce(self, settings: NerdvanaSettings) -> None:
        """Apply the policy and refuse the model *settings* ended up with when it is outside the policy.

        Called once every command line option is applied. Writes the audit record either way.
        """
        self.apply(settings)
        refusal = self.model_refusal(settings.model.model, settings.model.provider)
        self.write_audit(refusal)
        if refusal:
            raise ManagedPolicyError(refusal)

    def write_audit(self, refusal: str | None = None) -> None:
        """Append one JSON line naming the files read and the keys applied; nothing when no file was read."""
        if not self.active:
            return
        record = {
            "time":    datetime.now(UTC).isoformat(timespec="seconds"),
            "files":   [item.path for item in self.files],
            "applied": [item.to_dict() for item in self.applied],
            "refused": refusal,
        }
        path = core_paths.managed_audit_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("managed policy audit record not written to %s: %s", path, exc)

    def describe(self) -> list[str]:
        """Human readable lines: the files read and what each control did."""
        if not self.active:
            return ["No managed policy files."]
        lines = [f"Managed policy files ({len(self.files)}):"]
        lines.extend(f"  {item.path}: {', '.join(item.keys) or 'no keys'}" for item in self.files)
        lines.append("Applied controls:")
        for item in self.applied:
            note = "  (changed your setting)" if item.changed else ""
            lines.append(f"  {item.key} = {item.value}{note}  [{', '.join(Path(s).name for s in item.sources)}]")
        return lines


def _parse_file(path: Path) -> dict[str, Any]:
    """The validated dotted keys of one drop-in file; ValueError carries a message naming the file."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: {' '.join(str(exc).split())}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level must be a mapping, got {type(data).__name__}")
    values: dict[str, Any] = {}
    for section, body in data.items():
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise ValueError(f"{path}: {section}: expected a mapping, got {type(body).__name__}")
        for name, raw in body.items():
            key = f"{section}.{name}"
            if key not in _KEYS:
                raise ValueError(f"{path}: unknown key '{key}' (known: {', '.join(_KEYS)})")
            try:
                values[key] = _KEYS[key](raw)
            except ValueError as exc:
                raise ValueError(f"{path}: {key}: {exc}") from exc
    return values


def _drop_in_files(dirs: Iterable[Path], problems: list[str]) -> list[Path]:
    """The drop-in files of *dirs* ordered by file name, the earlier directory first on a tie.

    Only the system directory may be absent; a directory named by the environment must exist.
    """
    found: list[tuple[str, int, Path]] = []
    for index, directory in enumerate(dirs):
        try:
            entries = sorted(directory.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            if directory != core_paths.system_managed_dir():
                problems.append(f"{directory}: managed settings directory does not exist")
            continue
        except OSError as exc:
            problems.append(f"{directory}: {exc.strerror or exc}")
            continue
        found.extend(
            (entry.name, index, entry) for entry in entries
            if entry.suffix in SUFFIXES and not entry.name.startswith(".") and entry.is_file()
        )
    return [path for _name, _index, path in sorted(found)]


def scan_managed_policy(dirs: Iterable[Path] | None = None) -> tuple[ManagedPolicy, list[str]]:
    """Read every drop-in file; return the merged policy of the good ones and one message per problem."""
    problems: list[str] = []
    policy = ManagedPolicy()
    for path in _drop_in_files(core_paths.managed_settings_dirs() if dirs is None else dirs, problems):
        try:
            policy.add_file(str(path), _parse_file(path))
        except ValueError as exc:
            problems.append(str(exc))
    return policy, problems


def load_managed_policy(dirs: Iterable[Path] | None = None) -> ManagedPolicy:
    """The merged managed policy; ManagedPolicyError with every problem found when a file is malformed.

    The policy fails closed: a broken file is never skipped, because skipping it would drop its limits.
    """
    policy, problems = scan_managed_policy(dirs)
    if problems:
        raise ManagedPolicyError("managed policy: " + "; ".join(problems), problems)
    return policy
