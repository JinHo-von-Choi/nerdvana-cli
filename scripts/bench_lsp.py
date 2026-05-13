"""LSP benchmark harness — measure cold-index, goto-definition, find-references.

Drives ``core.lsp_client.LspClient`` against a fixture project and emits a JSON
summary on stdout. Designed to be invoked manually or from a nightly workflow;
not part of the PR critical path because it requires ``pyright`` on PATH.

Usage::

    uv run python scripts/bench_lsp.py \\
        --root tests/lsp/fixtures/sample_python_project \\
        --iterations 5

Output (stdout)::

    {
      "fixture":       "...",
      "python_files":  3,
      "symbols":       8,
      "cold_open_ms":  1234.5,
      "diagnostics": {
        "n": 3,
        "mean_ms": 12.3,
        "p95_ms":  18.1
      },
      "goto_definition": {...},
      "find_references": {...},
      "available_tools": ["FindReferences", ...]
    }

Exits 0 on success, 2 when no LSP server is available (skip-friendly).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

from nerdvana_cli.core.lsp_client import LspClient, LspError


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def _summary(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"n": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "n":        len(samples_ms),
        "mean_ms":  round(statistics.mean(samples_ms), 2),
        "p50_ms":   round(statistics.median(samples_ms), 2),
        "p95_ms":   round(_percentile(samples_ms, 95), 2),
        "max_ms":   round(max(samples_ms), 2),
    }


def _count_symbols(root: Path) -> int:
    """Cheap heuristic — counts top-level ``def``/``class`` lines."""
    total = 0
    for py in root.rglob("*.py"):
        try:
            for line in py.read_text(encoding="utf-8").splitlines():
                stripped = line.lstrip()
                if stripped.startswith(("def ", "async def ", "class ")):
                    total += 1
        except OSError:
            continue
    return total


async def _bench(root: Path, iterations: int) -> dict[str, object]:
    files = sorted(p for p in root.rglob("*.py") if not p.name.startswith("_"))
    if not files:
        raise SystemExit(f"no .py files under {root}")

    out: dict[str, object] = {
        "fixture":      str(root.resolve()),
        "python_files": len(files),
        "symbols":      _count_symbols(root),
    }

    client = LspClient(project_root=str(root))
    if not client.has_any_server():
        out["available_tools"] = []
        out["status"]          = "no-lsp-server-on-path"
        return out
    out["available_tools"] = [t.__class__.__name__ for t in client.available_tools()]

    # Cold open: time first diagnostics call on the first file.
    cold_t0 = time.monotonic()
    try:
        await client.diagnostics(str(files[0]))
    except LspError as exc:
        out["status"]       = "lsp-error"
        out["error"]        = str(exc)
        await client.close()
        return out
    out["cold_open_ms"] = round((time.monotonic() - cold_t0) * 1000, 2)

    # Diagnostics on each remaining file.
    diag_samples: list[float] = []
    for py in files[1:]:
        t0 = time.monotonic()
        await client.diagnostics(str(py))
        diag_samples.append((time.monotonic() - t0) * 1000)
    out["diagnostics"] = _summary(diag_samples)

    # Goto / references — drive against the first def/class we can find.
    target_file = ""
    target_line = 0
    target_sym  = ""
    for py in files:
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines()):
            stripped = line.lstrip()
            if stripped.startswith(("def ", "class ")):
                head = stripped.split(maxsplit=1)[1].split("(")[0].split(":")[0]
                target_file = str(py)
                target_line = i
                target_sym  = head
                break
        if target_sym:
            break

    goto_samples: list[float] = []
    refs_samples: list[float] = []
    if target_sym:
        for _ in range(iterations):
            t0 = time.monotonic()
            await client.goto_definition(file_path=target_file, line=target_line, symbol=target_sym)
            goto_samples.append((time.monotonic() - t0) * 1000)

            t0 = time.monotonic()
            await client.find_references(file_path=target_file, line=target_line, symbol=target_sym)
            refs_samples.append((time.monotonic() - t0) * 1000)

    out["target"]          = {"file": target_file, "line": target_line, "symbol": target_sym}
    out["goto_definition"] = _summary(goto_samples)
    out["find_references"] = _summary(refs_samples)
    out["status"]          = "ok"

    await client.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--root",
        default="tests/lsp/fixtures/sample_python_project",
        help="Project root to benchmark (default: small sample fixture).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of repetitions for goto/find-references (default: 5).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional path to write the JSON result (in addition to stdout).",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: --root {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    result = asyncio.run(_bench(root, args.iterations))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)

    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")

    if result.get("status") == "no-lsp-server-on-path":
        sys.exit(2)


if __name__ == "__main__":
    main()
