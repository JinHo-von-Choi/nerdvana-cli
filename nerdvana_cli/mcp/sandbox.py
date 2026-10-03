"""Confinement of stdio MCP server processes with the Landlock launcher.

A stdio MCP server is code from a third party that runs as the user. The per-server ``sandbox`` setting
starts it through the launcher of ``core/safety/sandbox.py`` instead, which restricts what the process and its
children can write (and, on a kernel that offers it, which TCP connections they can make). Reading is not
restricted, as with the ``Bash`` tool.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Literal

from nerdvana_cli.core.config.settings_sections import SANDBOX_MODES
from nerdvana_cli.core.safety import sandbox as core_sandbox
from nerdvana_cli.core.safety.sandbox import SandboxPolicy
from nerdvana_cli.mcp.config import McpServerConfig

CONFINED    = "confined"
UNCONFINED  = "unconfined"
NOT_LOCAL   = "not applicable"


class SandboxConfigError(RuntimeError):
    """The sandbox settings of a server are not usable, or confinement is required but unavailable."""


@dataclass(frozen=True)
class ServerLaunch:
    """How to start one stdio server: the argument list and a one-line statement of how it is confined."""

    argv:   list[str]
    status: str


def config_problem(config: McpServerConfig) -> str:
    """What is wrong with the sandbox settings of *config*; an empty string when they are usable."""
    if config.sandbox not in SANDBOX_MODES:
        return f"sandbox must be one of {', '.join(SANDBOX_MODES)}, got {config.sandbox!r}"
    if not isinstance(config.network, bool):
        return f"network must be true or false, got {config.network!r}"
    return ""


def plan_server_launch(config: McpServerConfig, cwd: str) -> ServerLaunch:
    """The argument list that starts the stdio server *config*, confined as its ``sandbox`` setting asks.

    Raises:
        SandboxConfigError: A setting is invalid, or ``sandbox`` is ``require`` and the system cannot confine.
    """
    plain = [config.command, *config.args]
    if problem := config_problem(config):
        raise SandboxConfigError(f"MCP server '{config.name}': {problem}")
    if config.sandbox == "off":
        return ServerLaunch(plain, f"{UNCONFINED} (sandbox off)")
    policy = SandboxPolicy(config.sandbox, config.network, tuple(config.write_paths), project=False, scratch=True)
    launch = core_sandbox.plan_launch(policy, "exec " + shlex.join(plain), cwd)
    if launch.refused:
        raise SandboxConfigError(f"MCP server '{config.name}': {launch.notice}")
    if launch.argv is None:
        return ServerLaunch(plain, f"{UNCONFINED} ({launch.notice})")
    scope = "network allowed" if config.network else "network refused"
    return ServerLaunch(launch.argv, f"{CONFINED} (writes limited, {scope})")


def confinement_report(cwd: str) -> tuple[Literal["ok", "warn", "fail", "skip"], str]:
    """The ``nerdvana doctor`` status (``ok``, ``warn``, ``fail`` or ``skip``) and a detail line for the configured servers.

    Plans the launch of every configured stdio server without starting one. It fails when a setting is unusable
    or ``require`` cannot be met, and warns when a server asks for confinement that this system cannot give.
    """
    from nerdvana_cli.mcp.config import load_mcp_config

    stdio = {name: cfg for name, cfg in load_mcp_config().items() if cfg.transport == "stdio"}
    if not stdio:
        return "skip", "no stdio MCP servers configured"
    confined: list[str] = []
    plain:    list[str] = []
    problems: list[str] = []
    for name, cfg in stdio.items():
        try:
            launch = plan_server_launch(cfg, cwd)
        except SandboxConfigError as exc:
            problems.append(str(exc))
            continue
        (confined if launch.status.startswith(CONFINED) else plain).append(name)
    detail = f"confined: {', '.join(confined) or '(none)'}; unconfined: {', '.join(plain) or '(none)'}"
    if problems:
        return "fail", "; ".join(problems)[:200]
    asked = [name for name in plain if stdio[name].sandbox != "off"]
    if asked:
        return "warn", f"{', '.join(asked)} ask for confinement but run unconfined here; {detail}"
    return "ok", detail
