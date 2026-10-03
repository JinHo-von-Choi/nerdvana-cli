"""Verify command for write-tests-js-chunk.

The tests the agent writes in chunk.test.js must pass against the correct
implementation and fail against each of two defective ones (swapped in here).
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile

REFERENCE = "'use strict';\n\n/**\n * Split items into consecutive groups of size elements; the last group may be\n * shorter. Throws RangeError unless size is a positive integer. The input\n * array is not modified.\n */\nfunction chunk(items, size) {\n  if (!Number.isInteger(size) || size < 1) throw new RangeError(`invalid size: ${size}`);\n  const groups = [];\n  for (let i = 0; i < items.length; i += size) groups.push(items.slice(i, i + size));\n  return groups;\n}\n\nmodule.exports = { chunk };\n"

DEFECTS = {
    "the shorter last group is dropped": "'use strict';\n\n/**\n * Split items into consecutive groups of size elements; the last group may be\n * shorter. Throws RangeError unless size is a positive integer. The input\n * array is not modified.\n */\nfunction chunk(items, size) {\n  if (!Number.isInteger(size) || size < 1) throw new RangeError(`invalid size: ${size}`);\n  const groups = [];\n  for (let i = 0; i + size <= items.length; i += size) groups.push(items.slice(i, i + size));\n  return groups;\n}\n\nmodule.exports = { chunk };\n",
    "the input array is modified": "'use strict';\n\n/**\n * Split items into consecutive groups of size elements; the last group may be\n * shorter. Throws RangeError unless size is a positive integer. The input\n * array is not modified.\n */\nfunction chunk(items, size) {\n  if (!Number.isInteger(size) || size < 1) throw new RangeError(`invalid size: ${size}`);\n  const groups = [];\n  const rest = items;\n  while (rest.length) groups.push(rest.splice(0, size));\n  return groups;\n}\n\nmodule.exports = { chunk };\n",
}

TEST_FILE = "chunk.test.js"


def run_against(source: str) -> subprocess.CompletedProcess:
    """Run the agent's tests in a scratch copy of the project whose chunk.js is *source*."""
    with tempfile.TemporaryDirectory() as scratch:
        target = pathlib.Path(scratch) / "work"
        shutil.copytree(".", target, ignore=shutil.ignore_patterns("check.py"))
        (target / "chunk.js").write_text(source, encoding="utf-8")
        return subprocess.run(
            ["node", "--test", TEST_FILE],
            cwd=target, capture_output=True, text=True, timeout=60,
        )


assert pathlib.Path(TEST_FILE).is_file(), f"{TEST_FILE} does not exist"

good = run_against(REFERENCE)
assert good.returncode == 0, "the tests fail on the correct implementation:\n" + good.stdout[-800:]
assert "# tests 0" not in good.stdout, "no tests were run"

for label, source in DEFECTS.items():
    bad = run_against(source)
    assert bad.returncode != 0, f"the tests do not catch this defect: {label}"
print("ok")
