"""Verify command for write-tests-py-merge-intervals.

The tests the agent writes in test_intervals.py must pass against the correct
implementation and fail against each of two defective ones (swapped in here).
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REFERENCE = '"""Interval merging."""\n\n\ndef merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:\n    """Merge closed intervals that overlap or touch and return them sorted by start.\n\n    The input may be unsorted and is not modified. An interval whose start is\n    greater than its end raises ValueError.\n    """\n    for start, end in intervals:\n        if start > end:\n            raise ValueError(f"invalid interval: ({start}, {end})")\n    merged: list[tuple[int, int]] = []\n    for start, end in sorted(intervals):\n        if merged and start <= merged[-1][1]:\n            merged[-1] = (merged[-1][0], max(merged[-1][1], end))\n        else:\n            merged.append((start, end))\n    return merged\n'

DEFECTS = {
    "touching intervals are not merged": '"""Interval merging."""\n\n\ndef merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:\n    """Merge closed intervals that overlap or touch and return them sorted by start.\n\n    The input may be unsorted and is not modified. An interval whose start is\n    greater than its end raises ValueError.\n    """\n    for start, end in intervals:\n        if start > end:\n            raise ValueError(f"invalid interval: ({start}, {end})")\n    merged: list[tuple[int, int]] = []\n    for start, end in sorted(intervals):\n        if merged and start < merged[-1][1]:\n            merged[-1] = (merged[-1][0], max(merged[-1][1], end))\n        else:\n            merged.append((start, end))\n    return merged\n',
    "a contained interval shrinks the merged end": '"""Interval merging."""\n\n\ndef merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:\n    """Merge closed intervals that overlap or touch and return them sorted by start.\n\n    The input may be unsorted and is not modified. An interval whose start is\n    greater than its end raises ValueError.\n    """\n    for start, end in intervals:\n        if start > end:\n            raise ValueError(f"invalid interval: ({start}, {end})")\n    merged: list[tuple[int, int]] = []\n    for start, end in sorted(intervals):\n        if merged and start <= merged[-1][1]:\n            merged[-1] = (merged[-1][0], end)\n        else:\n            merged.append((start, end))\n    return merged\n',
}

TEST_FILE = "test_intervals.py"


def run_against(source: str) -> subprocess.CompletedProcess:
    """Run the agent's tests in a scratch copy of the project whose intervals.py is *source*."""
    with tempfile.TemporaryDirectory() as scratch:
        target = pathlib.Path(scratch) / "work"
        shutil.copytree(".", target, ignore=shutil.ignore_patterns("__pycache__", "check.py"))
        (target / "intervals.py").write_text(source, encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-m", "unittest", "test_intervals"],
            cwd=target, capture_output=True, text=True, timeout=60, env=env,
        )


assert pathlib.Path(TEST_FILE).is_file(), f"{TEST_FILE} does not exist"

good = run_against(REFERENCE)
assert good.returncode == 0, "the tests fail on the correct implementation:\n" + good.stderr[-800:]
assert "Ran 0 tests" not in good.stderr, "no tests were run"

for label, source in DEFECTS.items():
    bad = run_against(source)
    assert bad.returncode != 0, f"the tests do not catch this defect: {label}"
print("ok")
