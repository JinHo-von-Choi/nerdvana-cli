"""Verify command for refactor-py-dedupe-phone: behaviour is preserved and normalize_phone has one definition."""

import ast
import hashlib
import pathlib
import subprocess
import sys

TEST_HASHES = {
    "tests/test_customers.py": "ceb668e05f4cf07e643337eb53b4f2c04f85e3f64bec200ccd833ab18ef292a8",
    "tests/test_suppliers.py": "61107b670ebc62a8c8ce2ce48ddea2bdf5b51a6f7e54f1e9f99c4dbdc251e8eb",
}
for name, expected in TEST_HASHES.items():
    actual = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()
    assert actual == expected, f"{name} was modified"

done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
assert done.returncode == 0, "tests fail"

definitions = []
for path in pathlib.Path(".").rglob("*.py"):
    if path.name == "check.py" or "tests" in path.parts:
        continue
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_phone":
            definitions.append(str(path))
assert len(definitions) == 1, f"normalize_phone is defined {len(definitions)} times: {definitions}"
for module in ("customers.py", "suppliers.py"):
    assert "normalize_phone" in pathlib.Path(module).read_text(encoding="utf-8"), f"{module} no longer uses normalize_phone"
print("ok")
