"""Unit tests for scripts/bench_lsp_diff.py.

Three scenarios:
  a) no regression  — baseline == current => all "ok", exit 0
  b) warn_level     — one metric +30%    => WARN row,  exit 0
  c) fail_level     — one metric +75%    => FAIL row,  exit 1

Tests invoke the script via subprocess so the exit-code contract is
exercised end-to-end alongside the stdout content.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "bench_lsp_diff.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload(
    cold: float,
    diag_p95: float,
    diag_mean: float,
    goto_p95: float,
    refs_p95: float,
) -> dict:
    return {
        "status":          "ok",
        "cold_open_ms":    cold,
        "diagnostics":     {"p95_ms": diag_p95, "mean_ms": diag_mean},
        "goto_definition": {"p95_ms": goto_p95},
        "find_references": {"p95_ms": refs_p95},
    }


def _run(
    baseline: dict,
    current: dict,
    tmp_path: Path,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    b_file = tmp_path / "baseline.json"
    c_file = tmp_path / "current.json"
    b_file.write_text(json.dumps(baseline), encoding="utf-8")
    c_file.write_text(json.dumps(current),  encoding="utf-8")
    cmd = [sys.executable, str(_SCRIPT), str(b_file), str(c_file)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


_BASELINE = _payload(
    cold=100.0,
    diag_p95=10.0,
    diag_mean=5.0,
    goto_p95=8.0,
    refs_p95=12.0,
)


# ---------------------------------------------------------------------------
# Test a) — no regression
# ---------------------------------------------------------------------------

def test_no_regression(tmp_path: Path) -> None:
    """baseline == current => all rows 'ok', exit 0."""
    result = _run(_BASELINE, _BASELINE, tmp_path)
    assert result.returncode == 0, result.stdout
    assert "cold_open_ms"         in result.stdout
    assert "diagnostics / p95_ms" in result.stdout
    assert "ok"                   in result.stdout
    assert "WARN"                 not in result.stdout
    assert "FAIL"                 not in result.stdout


# ---------------------------------------------------------------------------
# Test b) — warn level (+30%)
# ---------------------------------------------------------------------------

def test_warn_level(tmp_path: Path) -> None:
    """diagnostics.p95_ms +30% => WARN row present, no FAIL, exit 0."""
    current = _payload(
        cold=100.0,
        diag_p95=13.0,   # +30%
        diag_mean=5.0,
        goto_p95=8.0,
        refs_p95=12.0,
    )
    result = _run(_BASELINE, current, tmp_path)
    assert result.returncode == 0, result.stdout
    assert "WARN"                 in result.stdout
    assert "FAIL"                 not in result.stdout
    assert "diagnostics / p95_ms" in result.stdout


# ---------------------------------------------------------------------------
# Test c) — fail level (+75%)
# ---------------------------------------------------------------------------

def test_fail_level(tmp_path: Path) -> None:
    """cold_open_ms +75% => FAIL row present, exit 1."""
    current = _payload(
        cold=175.0,   # +75%
        diag_p95=10.0,
        diag_mean=5.0,
        goto_p95=8.0,
        refs_p95=12.0,
    )
    result = _run(_BASELINE, current, tmp_path)
    assert result.returncode == 1, result.stdout
    assert "FAIL"          in result.stdout
    assert "cold_open_ms"  in result.stdout
