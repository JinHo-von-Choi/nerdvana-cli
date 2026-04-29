"""
Verify test collection count has not regressed below the recorded baseline.

Usage:
    python scripts/check_test_collection.py [--update] [--baseline-path PATH]

Exit codes:
    0  collection count >= baseline
    1  collection count < baseline (tests removed or disabled)
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

_COLLECT_CMD = [
    "uv", "run", "pytest",
    "--collect-only",
    "-m", "not lsp_integration and not live",
    "-q",
]
_TOTAL_RE = re.compile(r"(\d+)(?:/\d+)?\s+tests?\s+collected")


def _read_baseline(path: Path) -> int:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("total:"):
            return int(stripped.split(":", 1)[1].strip())
    raise ValueError(f"No 'total:' line found in {path}")


def _write_baseline(path: Path, total: int) -> None:
    lines = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("# Updated:"):
                lines.append("# Updated: 2026-04-29")
            elif stripped.startswith("total:"):
                lines.append(f"total: {total}")
            else:
                lines.append(line)
    else:
        lines = [
            "# Test collection baseline for nerdvana-cli",
            "# Updated: 2026-04-29",
            "# Source: uv run pytest --collect-only -m \"not lsp_integration and not live\" -q",
            f"total: {total}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collect_current() -> int:
    result = subprocess.run(
        _COLLECT_CMD,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for line in reversed(output.splitlines()):
        m = _TOTAL_RE.search(line)
        if m:
            return int(m.group(1))
    raise RuntimeError(
        "Could not parse test count from pytest output.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update baseline to current count and exit 0.",
    )
    parser.add_argument(
        "--baseline-path",
        default=None,
        help="Override path to .collect-baseline (default: tests/.collect-baseline).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    baseline_path = (
        Path(args.baseline_path) if args.baseline_path else repo_root / "tests" / ".collect-baseline"
    )

    current = _collect_current()
    print(current, flush=True)

    if args.update:
        _write_baseline(baseline_path, current)
        print(f"Baseline updated to {current} in {baseline_path}", file=sys.stderr)
        return 0

    if not baseline_path.exists():
        print(
            f"Baseline file not found: {baseline_path}. Run with --update to create it.",
            file=sys.stderr,
        )
        return 1

    baseline = _read_baseline(baseline_path)

    if current < baseline:
        print(
            f"FAIL: {current} tests collected, expected >= {baseline}. "
            "Tests removed/disabled. Update tests/.collect-baseline if intentional.",
            file=sys.stderr,
        )
        return 1

    if current > baseline:
        print(
            f"WARN: {current} tests collected, baseline is {baseline}. "
            "Tests added. Update tests/.collect-baseline.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
