"""Installation, key, and dependency diagnostics — nerdvana doctor.

작성자: 최진호
작성일: 2026-04-29
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass
class CheckResult:
    """Single diagnostic check result."""

    name:   str
    status: Literal["ok", "warn", "fail", "skip"]
    detail: str = ""


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    """Python >= 3.11 required."""
    vi          = sys.version_info
    major       = vi[0]
    minor       = vi[1]
    micro       = vi[2]
    version_str = f"{major}.{minor}.{micro}"
    if (major, minor) >= (3, 11):
        return CheckResult("python_version", "ok", version_str)
    return CheckResult(
        "python_version",
        "fail",
        f"{version_str} — 3.11+ required",
    )


def _check_uv_installed() -> CheckResult:
    """uv package manager must be on PATH."""
    path = shutil.which("uv")
    if path:
        return CheckResult("uv", "ok", path)
    return CheckResult("uv", "fail", "uv not found on PATH")


def _check_install_paths() -> CheckResult:
    """~/.nerdvana install root and data home must exist with write access."""
    from nerdvana_cli.core import paths as _paths

    root     = _paths.install_root()
    data     = _paths.user_data_home()
    missing  = [p for p in (root, data) if not p.exists()]

    if missing:
        return CheckResult(
            "install_paths",
            "fail",
            f"Missing: {', '.join(str(p) for p in missing)}",
        )

    read_only = [p for p in (root, data) if not os.access(p, os.W_OK)]
    if read_only:
        return CheckResult(
            "install_paths",
            "warn",
            f"Read-only: {', '.join(str(p) for p in read_only)}",
        )

    return CheckResult("install_paths", "ok", f"{root}")


def _check_provider_keys() -> CheckResult:
    """At least one provider API key should be set."""
    from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS

    present: list[str] = []
    absent:  list[str] = []

    for provider, env_vars in PROVIDER_KEY_ENVVARS.items():
        found = any(os.environ.get(v) for v in env_vars)
        if found:
            present.append(provider.value)
        else:
            absent.append(provider.value)

    total  = len(PROVIDER_KEY_ENVVARS)
    count  = len(present)
    detail = f"{count}/{total} providers configured"

    if count == 0:
        return CheckResult("provider_keys", "warn", detail + f" (missing: {', '.join(absent)})")

    missing_summary = f"; missing: {', '.join(absent[:5])}{'…' if len(absent) > 5 else ''}" if absent else ""
    return CheckResult("provider_keys", "ok", detail + missing_summary)


def _check_parism() -> CheckResult:
    """Parism LSP bridge (npx @nerdvana/parism) availability."""
    if shutil.which("npx") is None:
        return CheckResult("parism", "warn", "npx not found — Node.js required for Parism")

    try:
        result = subprocess.run(
            [
                "npx",
                "-y",
                "--package=@nerdvana/parism@latest",
                "parism",
                "--version",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version = result.stdout.strip() or "unknown"
            return CheckResult("parism", "ok", f"v{version}")
        err = (result.stderr or result.stdout).strip()
        return CheckResult("parism", "warn", f"parism --version failed: {err[:120]}")
    except subprocess.TimeoutExpired:
        return CheckResult("parism", "warn", "timeout (5 s) checking parism version")
    except Exception as exc:
        return CheckResult("parism", "warn", f"unexpected error: {exc}")


def _check_lsp_servers() -> CheckResult:
    """Check for pyright and typescript-language-server binaries."""
    found   = [b for b in ("pyright", "typescript-language-server") if shutil.which(b)]
    missing = [b for b in ("pyright", "typescript-language-server") if b not in found]

    if not found:
        return CheckResult(
            "lsp_servers",
            "warn",
            "Neither pyright nor typescript-language-server found on PATH",
        )

    detail = f"found: {', '.join(found)}"
    if missing:
        detail += f"; missing: {', '.join(missing)}"
    return CheckResult("lsp_servers", "ok", detail)


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


def _check_pricing_freshness() -> CheckResult:
    """Run check_pricing_freshness.py --report-only to detect stale snapshots."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_pricing_freshness.py"
    if not script.exists():
        return CheckResult("pricing_freshness", "skip", f"script not found: {script}")

    try:
        result = subprocess.run(
            [sys.executable, str(script), "--report-only"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return CheckResult("pricing_freshness", "ok", "all snapshots within TTL")
        summary = (result.stdout + result.stderr).strip()
        return CheckResult("pricing_freshness", "warn", summary[:200] or "stale snapshot(s) detected")
    except subprocess.TimeoutExpired:
        return CheckResult("pricing_freshness", "warn", "timeout checking pricing freshness")
    except Exception as exc:
        return CheckResult("pricing_freshness", "warn", f"error: {exc}")


def _check_collect_baseline() -> CheckResult:
    """Run check_test_collection.py to verify test count has not regressed."""
    repo_root = Path(__file__).resolve().parents[2]
    baseline  = repo_root / "tests" / ".collect-baseline"
    script    = repo_root / "scripts" / "check_test_collection.py"

    if not baseline.exists() or not script.exists():
        missing = ", ".join(
            str(p) for p in (baseline, script) if not p.exists()
        )
        return CheckResult("collect_baseline", "skip", f"not found: {missing}")

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            count = result.stdout.strip()
            return CheckResult("collect_baseline", "ok", f"{count} tests collected")
        summary = (result.stderr or result.stdout).strip()
        return CheckResult("collect_baseline", "warn", summary[:200] or "test count regressed")
    except subprocess.TimeoutExpired:
        return CheckResult("collect_baseline", "warn", "timeout running pytest collection")
    except Exception as exc:
        return CheckResult("collect_baseline", "warn", f"error: {exc}")


# ---------------------------------------------------------------------------
# Ordered check pipeline
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    _check_python_version,
    _check_uv_installed,
    _check_install_paths,
    _check_provider_keys,
    _check_parism,
    _check_lsp_servers,
    _check_mcp_servers,
    _check_pricing_freshness,
    _check_collect_baseline,
]


def run_all_checks() -> list[CheckResult]:
    """Execute every check in order and return results."""
    return [fn() for fn in _ALL_CHECKS]


# ---------------------------------------------------------------------------
# Typer entry point
# ---------------------------------------------------------------------------


def doctor_command(strict: bool = False, json_output: bool = False) -> int:
    """Run all diagnostic checks.

    Returns the integer exit code (0 = pass, 1 = fail).
    Called by the ``nerdvana doctor`` CLI command.
    """
    import typer
    from rich.console import Console
    from rich.table import Table

    results  = run_all_checks()
    console  = Console()

    status_colour = {
        "ok":   "green",
        "warn": "yellow",
        "fail": "red",
        "skip": "dim",
    }

    has_fail = any(r.status == "fail" for r in results)
    has_warn = any(r.status == "warn" for r in results)

    exit_code = 1 if has_fail or (has_warn and strict) else 0

    if json_output:
        payload = {
            "checks": [
                {"name": r.name, "status": r.status, "detail": r.detail}
                for r in results
            ],
            "exit_code": exit_code,
        }
        console.print_json(json.dumps(payload))
        raise typer.Exit(exit_code)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Check",  style="bold",       min_width=22)
    table.add_column("Status", min_width=6)
    table.add_column("Detail")

    for r in results:
        colour = status_colour.get(r.status, "")
        table.add_row(
            r.name,
            f"[{colour}]{r.status}[/{colour}]",
            r.detail,
        )

    console.print(table)
    raise typer.Exit(exit_code)
