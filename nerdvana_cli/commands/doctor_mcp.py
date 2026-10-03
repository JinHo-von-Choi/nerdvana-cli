"""MCP related checks of nerdvana doctor: reachability, configuration files and confinement."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from nerdvana_cli.commands.doctor_result import CheckResult


def _check_mcp_servers() -> CheckResult:
    """Check reachability of configured MCP servers."""
    from nerdvana_cli.mcp.config import McpServerConfig, load_mcp_config

    configs: dict[str, McpServerConfig] = load_mcp_config()
    if not configs:
        return CheckResult("mcp_servers", "skip", "no MCP servers configured")

    ok_names:   list[str] = []
    warn_names: list[str] = []

    for name, cfg in configs.items():
        if cfg.transport in ("http", "sse") and cfg.url:
            status_code = _ping_http(cfg.url, cfg.headers)
            if status_code in (200, 401, 403, 404):
                ok_names.append(name)
            else:
                warn_names.append(f"{name}({status_code})")
        else:
            # stdio: only verify the command binary exists
            cmd = cfg.command
            if cmd and shutil.which(cmd):
                ok_names.append(name)
            elif cmd:
                warn_names.append(f"{name}(cmd not found: {cmd})")
            else:
                ok_names.append(name)

    if warn_names:
        return CheckResult(
            "mcp_servers",
            "warn",
            f"unreachable: {', '.join(warn_names)}; ok: {', '.join(ok_names) or '(none)'}",
        )
    return CheckResult("mcp_servers", "ok", f"{len(ok_names)} server(s) reachable")


def _ping_http(url: str, headers: dict[str, str]) -> int:
    """Return HTTP status code for GET /, or -1 on network error."""
    try:
        import urllib.error
        import urllib.request

        base = url.rstrip("/")
        req  = urllib.request.Request(base + "/", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return -1


def _mcp_config_files() -> list[Path]:
    """Global then project MCP config paths, in load order."""
    from nerdvana_cli.core import paths as _paths

    return [_paths.user_mcp_json(), Path.cwd() / ".mcp.json"]


def _validate_mcp_server(name: str, raw: Any) -> tuple[str, str]:
    """Return ``(problem, missing_command)`` for one server entry; empty when fine."""
    if not isinstance(raw, dict):
        return f"{name}: entry must be an object", ""
    transport = raw.get("type", "stdio")
    if transport not in ("stdio", "http", "sse"):
        return f"{name}: unknown type '{transport}'", ""
    if transport in ("http", "sse"):
        url = raw.get("url")
        if not isinstance(url, str) or not url:
            return f"{name}: {transport} server needs a 'url'", ""
        return "", ""
    command = raw.get("command")
    if not isinstance(command, str) or not command:
        return f"{name}: stdio server needs a 'command'", ""
    args = raw.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return f"{name}: 'args' must be a list of strings", ""
    if shutil.which(command) is None:
        return "", f"{name}({command})"
    return "", ""


def _check_mcp_config() -> CheckResult:
    """MCP config files must parse; stdio commands must exist on PATH. No network, no spawning."""
    problems: list[str] = []
    missing:  list[str] = []
    total = 0

    for path in _mcp_config_files():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{path.name}: cannot parse ({exc.__class__.__name__})")
            continue
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            problems.append(f"{path.name}: 'mcpServers' must be an object")
            continue
        for name, raw in servers.items():
            total += 1
            problem, absent = _validate_mcp_server(str(name), raw)
            if problem:
                problems.append(problem)
            if absent:
                missing.append(absent)

    if problems:
        return CheckResult("mcp_config", "fail", "; ".join(problems)[:200])
    if total == 0:
        return CheckResult("mcp_config", "skip", "no MCP config files or servers")
    if missing:
        return CheckResult("mcp_config", "warn", f"command not on PATH: {', '.join(missing)}")
    return CheckResult("mcp_config", "ok", f"{total} server(s) parse; stdio commands found")


def _check_mcp_sandbox() -> CheckResult:
    """Report which stdio MCP servers start confined, from the configuration; no server is started."""
    from nerdvana_cli.mcp.sandbox import confinement_report
    return CheckResult("mcp_sandbox", *confinement_report(os.getcwd()))
