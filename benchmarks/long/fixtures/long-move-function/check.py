"""Checks the move of the money helpers. Exit status 0 means done."""

import ast
import hashlib
import os
import pathlib
import subprocess
import sys

MOVED = {"format_money", "parse_money", "CURRENCY_SYMBOLS"}
HELPERS = "acme.legacy.helpers"
OUTPUT_DIGEST = "6bedcc5610083baf"
problems = []


def top_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name.split(".")[0]) for a in node.names)
    return names


def absolute(path, node):
    if node.level == 0:
        return node.module or ""
    parts = list(path.with_suffix("").parts)[:-1]
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def scan(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = absolute(path, node)
            if module == HELPERS and MOVED & {a.name for a in node.names}:
                problems.append(f"{path}:{node.lineno}: imports a moved name from {HELPERS}")
            if module == "acme.legacy":
                bound.update(a.asname or a.name for a in node.names if a.name == "helpers")
        elif isinstance(node, ast.Import):
            bound.update(a.asname for a in node.names if a.name == HELPERS and a.asname)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in MOVED and isinstance(node.value, ast.Name) and node.value.id in bound:
            problems.append(f"{path}:{node.lineno}: {node.value.id}.{node.attr} still goes through the helper module")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and any(f"{HELPERS}.{n}" in node.value for n in MOVED):
            problems.append(f"{path}:{node.lineno}: string refers to {node.value}")


helpers = pathlib.Path("acme/legacy/helpers.py")
if MOVED & top_names(ast.parse(helpers.read_text(encoding="utf-8"))):
    problems.append("acme/legacy/helpers.py still defines or imports a moved name")
money = pathlib.Path("acme/money/formatting.py")
if not money.is_file() or not MOVED <= top_names(ast.parse(money.read_text(encoding="utf-8"))):
    problems.append("acme/money/formatting.py must define format_money, parse_money and CURRENCY_SYMBOLS")
for path in sorted(list(pathlib.Path("acme").rglob("*.py")) + list(pathlib.Path("tests").rglob("*.py"))):
    scan(path)
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
if not problems:
    run = subprocess.run([sys.executable, "-c", "import hashlib, acme.app as a; print(hashlib.sha256(a.run_all().encode()).hexdigest()[:16])"], capture_output=True, text=True, env=env)
    if run.returncode != 0:
        problems.append("running every module failed: " + (run.stderr.strip().splitlines() or ["no output"])[-1])
    elif run.stdout.strip() != OUTPUT_DIGEST:
        problems.append("the modules print different output than before the move")
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], capture_output=True, text=True, env=env)
    if tests.returncode != 0:
        problems.append("unit tests fail: " + (tests.stderr.strip().splitlines() or ["no output"])[-1])
for problem in problems[:30]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
