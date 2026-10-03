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
from pathlib import Path
from typing import TYPE_CHECKING

from nerdvana_cli.cli.commands.doctor_mcp import (
    _check_mcp_config,
    _check_mcp_sandbox,
    _check_mcp_servers,
)
from nerdvana_cli.cli.commands.doctor_policy import check_managed_policy
from nerdvana_cli.cli.commands.doctor_result import CheckResult

if TYPE_CHECKING:
    from nerdvana_cli.providers.base import ProviderName


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
    from nerdvana_cli.core.config import paths as _paths

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
    """Check for the pyright and typescript language server binaries."""
    found   = [b for b in ("pyright-langserver", "typescript-language-server") if shutil.which(b)]
    missing = [b for b in ("pyright-langserver", "typescript-language-server") if b not in found]

    if not found:
        return CheckResult(
            "lsp_servers",
            "warn",
            "Neither pyright-langserver nor typescript-language-server found on PATH",
        )

    detail = f"found: {', '.join(found)}"
    if missing:
        detail += f"; missing: {', '.join(missing)}"
    return CheckResult("lsp_servers", "ok", detail)


def _check_config_warnings() -> CheckResult:
    """Report fields replaced by defaults or ignored while loading the config file."""
    from nerdvana_cli.core.config.settings import NerdvanaSettings, SettingsLoadError

    try:
        settings = NerdvanaSettings.load()
    except SettingsLoadError as exc:
        return CheckResult("config_warnings", "fail", str(exc)[:200])
    except Exception as exc:
        return CheckResult("config_warnings", "fail", f"config could not be loaded: {exc}"[:200])

    warnings = settings.load_warnings
    if not warnings:
        source = settings.config_path or "defaults"
        return CheckResult("config_warnings", "ok", f"no problems ({source})")

    shown = "; ".join(w.format() for w in warnings[:3])
    extra = f" (+{len(warnings) - 3} more)" if len(warnings) > 3 else ""
    return CheckResult("config_warnings", "warn", f"{len(warnings)} issue(s): {shown}{extra}")


def _resolve_provider_class(provider_name: str) -> tuple[ProviderName | None, str]:
    """Return ``(ProviderName, error)``; the name is ``None`` when unresolvable."""
    from nerdvana_cli.providers.base import ProviderName
    from nerdvana_cli.providers.factory import provider_class_for

    try:
        prov = ProviderName(provider_name)
    except ValueError:
        known = ", ".join(p.value for p in ProviderName)
        return None, f"unknown provider '{provider_name}' (known: {known})"
    if provider_class_for(prov) is None:
        return None, f"no provider class registered for '{prov.value}'"
    return prov, ""


def _check_model_resolution() -> CheckResult:
    """Configured provider and model must resolve to a provider class and context window."""
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.providers.base import resolve_context_window
    from nerdvana_cli.providers.factory import provider_class_for

    try:
        settings = NerdvanaSettings.load()
    except Exception as exc:
        return CheckResult("model_resolution", "fail", f"config could not be loaded: {exc}"[:200])

    model = settings.model.model
    if not model:
        return CheckResult("model_resolution", "fail", "model is empty")

    prov, error = _resolve_provider_class(settings.model.provider)
    if prov is None:
        return CheckResult("model_resolution", "fail", error)

    window = resolve_context_window(prov, model)
    if window <= 0:
        return CheckResult("model_resolution", "fail", f"no context window for {prov.value}/{model}")

    cls = provider_class_for(prov)
    cls_name = cls.__name__ if cls else "?"
    return CheckResult("model_resolution", "ok", f"{prov.value}/{model} -> {cls_name}, context {window}")


def _check_fallback_models() -> CheckResult:
    """Every fallback entry must resolve, on its own provider when it names one.

    Entries are ``model`` (run under the configured provider) or
    ``provider:model`` (switch provider for that fallback).
    """
    from nerdvana_cli.core.config.model_routing import parse_fallback
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.providers.base import ProviderName, detect_provider, resolve_context_window
    from nerdvana_cli.providers.factory import resolve_api_key

    try:
        settings = NerdvanaSettings.load()
    except Exception as exc:
        return CheckResult("fallback_models", "fail", f"config could not be loaded: {exc}"[:200])

    entries = settings.model.fallback_models
    if not entries:
        return CheckResult("fallback_models", "skip", "no fallback models configured")

    prov, error = _resolve_provider_class(settings.model.provider)
    if prov is None:
        return CheckResult("fallback_models", "fail", error)

    failed:   list[str] = []
    warnings: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            failed.append(f"{entry!r} (empty or not a string)")
            continue
        explicit, model = parse_fallback(entry)
        target          = ProviderName(explicit) if explicit else prov
        if resolve_context_window(target, model) <= 0:
            failed.append(f"{entry} (no context window)")
        elif explicit and target != prov and not resolve_api_key(target):
            warnings.append(f"{entry} (no API key for {target.value})")
        elif not explicit and detect_provider(model) != prov:
            warnings.append(
                f"{entry} (looks like {detect_provider(model).value}; write it as "
                f"{detect_provider(model).value}:{model} to switch provider)"
            )

    if failed:
        return CheckResult("fallback_models", "fail", f"unresolved: {', '.join(failed)}")
    if warnings:
        return CheckResult("fallback_models", "warn", "; ".join(warnings))
    return CheckResult("fallback_models", "ok", f"{len(entries)} fallback model(s) resolve")


PROJECT_DOC_WARN_TOKENS = 3_000


def _check_project_docs() -> CheckResult:
    """Project documents ride along on every request; say how much they add."""
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.context.nirnamd import fit_to_budget, load_nirna_files
    from nerdvana_cli.core.context.token_estimator import approx_tokens

    try:
        settings = NerdvanaSettings.load()
    except Exception:  # noqa: BLE001 - config problems are reported by the config check
        return CheckResult("project_docs", "skip", "config could not be loaded")
    budget = settings.session.project_doc_max_tokens
    files  = fit_to_budget(load_nirna_files(cwd=os.getcwd()), budget)
    total  = sum(approx_tokens(f.content) for f in files)
    if not files:
        return CheckResult("project_docs", "ok", "no project documents")
    detail = f"{len(files)} document(s), about {total:,} tokens on every request"
    if budget <= 0 and total > PROJECT_DOC_WARN_TOKENS:
        return CheckResult("project_docs", "warn", f"{detail}; trim them or set session.project_doc_max_tokens")
    return CheckResult("project_docs", "ok", detail)


def _check_pricing_coverage() -> CheckResult:
    """The configured model and its fallbacks should have a known price, or a cost limit cannot apply."""
    from nerdvana_cli.core.config.model_routing import parse_fallback
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.telemetry.analytics import PricingTable

    try:
        settings = NerdvanaSettings.load()
    except Exception:  # noqa: BLE001 - config problems are reported by the config check
        return CheckResult("pricing_coverage", "skip", "config could not be loaded")
    table      = PricingTable()
    provider   = settings.model.provider
    candidates = [(provider, settings.model.model)]
    for entry in settings.model.fallback_models:
        other, model = parse_fallback(entry)
        candidates.append((other or provider, model))
    unpriced = [f"{p}/{m}" for p, m in candidates if not table.has_price(p, m)]
    if not unpriced:
        return CheckResult("pricing_coverage", "ok", f"{len(candidates)} model(s) have a known price")
    limited = settings.session.max_cost_usd > 0 or settings.session.require_price
    return CheckResult("pricing_coverage", "warn" if limited else "ok", f"no price for {', '.join(unpriced)}; a cost limit does not apply to them (session.max_total_tokens does)")


def _check_sandbox() -> CheckResult:
    """Report whether shell commands can be confined, and what the configuration asks for."""
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.safety.egress_proxy import describe_egress
    from nerdvana_cli.core.safety.sandbox import landlock_abi

    try:
        settings = NerdvanaSettings.load()
    except Exception:  # noqa: BLE001 - config problems are reported by the config check
        return CheckResult("sandbox", "skip", "config could not be loaded")
    sandbox = settings.sandbox
    abi = landlock_abi()
    if abi < 1:
        detail = "Landlock is not available on this system"
        if sandbox.mode == "require":
            return CheckResult("sandbox", "fail", f"sandbox.mode is require but {detail}")
        return CheckResult("sandbox", "warn" if sandbox.mode == "auto" else "ok", f"{detail}; mode {sandbox.mode}")
    if sandbox.network is not True and abi < 4:
        return CheckResult("sandbox", "fail" if sandbox.mode == "require" else "warn", f"Landlock ABI {abi}; sandbox.network {sandbox.network} needs ABI 4 (Linux 6.7)")
    return CheckResult("sandbox", "ok", f"Landlock ABI {abi} available; mode {sandbox.mode}; egress {describe_egress(sandbox, settings.secrets)}")


def _check_pricing_freshness() -> CheckResult:
    """Run check_pricing_freshness.py --report-only to detect stale snapshots."""
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_pricing_freshness.py"
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
    repo_root = Path(__file__).resolve().parents[3]
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
    check_managed_policy,
    _check_config_warnings,
    _check_model_resolution,
    _check_fallback_models,
    _check_parism,
    _check_lsp_servers,
    _check_mcp_servers,
    _check_mcp_config,
    _check_mcp_sandbox,
    _check_sandbox,
    _check_pricing_coverage,
    _check_project_docs,
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
