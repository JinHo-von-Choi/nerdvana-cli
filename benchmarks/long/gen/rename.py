"""Task long-rename-snake-case: change a naming convention across 25 of 40 modules.

Author: 최진호
Date:   2026-10-03

A package of 40 modules in which 25 define camelCase functions. The agent renames them to
snake_case and updates every import, call and string reference. The checker compares the
defined names, scans for leftover camelCase text and runs every function through a registry
that resolves names from strings.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .common import TaskBuild, camel, fill, prose, rng_for, run_python

TASK_ID = "long-rename-snake-case"
PACKAGE = "warehouse"
NOUNS = [
    "inventory", "orders", "shipping", "returns", "pricing", "billing", "taxes", "discounts", "catalog", "suppliers",
    "carriers", "pallets", "zones", "labels", "barcodes", "audits", "forecasts", "reorder", "stocktake", "receiving",
    "putaway", "picking", "packing", "dispatch", "routing", "tracking", "invoices", "refunds", "payments", "ledgers",
    "rebates", "tariffs", "customs", "vendors", "contracts", "slotting", "cartons", "bins", "shifts", "docks",
]
VERB_TOKENS = ["compute", "fetch", "build", "resolve", "apply", "normalize", "merge", "collect"]
OBJ_TOKENS  = ["total", "index", "window", "offset", "balance", "quota", "digest", "margin"]
MODULI      = [9973, 10007, 10061, 10103, 10133, 10163]
LEGACY_COUNT = 25


@dataclass
class Func:
    """One function of the generated package."""

    tokens: list[str]
    kind:   int
    a:      int
    b:      int
    m:      int
    n:      int
    callee: tuple[int, int] | None = None


@dataclass
class Module:
    """One module of the generated package."""

    index:   int
    noun:    str
    legacy:  bool
    funcs:   list[Func] = field(default_factory=list)
    doc:     str = ""
    styles:  dict[int, str] = field(default_factory=dict)
    notes:   list[str] = field(default_factory=list)


def build_spec(rng: random.Random) -> list[Module]:
    """Modules with their functions, call graph (acyclic: callees live in earlier modules) and import styles."""
    legacy  = set(rng.sample(range(len(NOUNS)), LEGACY_COUNT))
    modules = []
    for i, noun in enumerate(NOUNS):
        funcs = [Func([VERB_TOKENS[(i + 3 * f) % 8], noun, OBJ_TOKENS[(i * 5 + f) % 8]], f, rng.randrange(3, 40),
                      rng.randrange(1, 99), rng.choice(MODULI), rng.randrange(3, 9)) for f in range(4)]
        modules.append(Module(i, noun, i in legacy, funcs, prose(rng, 6), notes=[prose(rng, 3) for _ in range(8)]))
    for mod in modules[1:]:
        pool = [j for j in range(mod.index) if modules[j].legacy] or list(range(mod.index))
        for func in mod.funcs[2:]:
            source      = pool if rng.random() < 0.8 else list(range(mod.index))
            target      = rng.choice(source)
            func.callee = (target, rng.randrange(4))
            mod.styles.setdefault(target, rng.choice(["from", "module", "alias"]))
    return modules


def func_name(mod: Module, func: Func, after: bool) -> str:
    """The name a function carries in the before or after rendering."""
    return "_".join(func.tokens) if (after or not mod.legacy) else camel(func.tokens)


def _imports(modules: list[Module], mod: Module, after: bool) -> list[str]:
    lines = []
    for target in sorted(mod.styles):
        other, style = modules[target], mod.styles[target]
        if style == "from":
            used = sorted({func_name(other, other.funcs[g], after) for f in mod.funcs if f.callee and f.callee[0] == target for g in [f.callee[1]]})
            lines.append(f"from {PACKAGE}.{other.noun} import {', '.join(used)}")
        elif style == "module":
            lines.append(f"from {PACKAGE} import {other.noun}")
        else:
            lines.append(f"import {PACKAGE}.{other.noun} as w{other.noun}")
    return lines


def _call(modules: list[Module], mod: Module, func: Func, after: bool) -> str:
    target, g = func.callee  # type: ignore[misc]
    other = modules[target]
    name  = func_name(other, other.funcs[g], after)
    style = mod.styles[target]
    return name if style == "from" else (f"{other.noun}.{name}" if style == "module" else f"w{other.noun}.{name}")


def _body(modules: list[Module], mod: Module, func: Func, after: bool) -> str:
    sib = lambda k: func_name(mod, mod.funcs[k], after)  # noqa: E731
    if func.kind == 0:
        return f"    return (x * {func.a} + {func.b}) % {func.m}"
    if func.kind == 1:
        return f"    acc = x\n    for step in range({func.n}):\n        acc = (acc * {func.a} + step) % {func.m}\n    return acc"
    if func.kind == 2:
        inner = _call(modules, mod, func, after) if func.callee else sib(0)
        return f"    return {inner}(x + {func.a}) % {func.m}"
    inner = _call(modules, mod, func, after) if func.callee else sib(0)
    return f"    first = {sib(1)}(x)\n    second = {inner}(first)\n    return (first + second + {func.b}) % {func.m}"


def render_module(modules: list[Module], mod: Module, after: bool) -> str:
    """Source text of one module."""
    names  = [func_name(mod, f, after) for f in mod.funcs]
    head   = [f'"""{mod.noun.capitalize()} helpers of the {PACKAGE} package.\n\n{mod.doc}\n"""', "", "from __future__ import annotations", ""]
    imports = _imports(modules, mod, after)
    if imports:
        head += imports + [""]
    head += ["__all__ = [" + ", ".join(f'"{n}"' for n in names) + "]", ""]
    head += [f"NOTE_{k + 1} = (\n    \"{note}\"\n)" for k, note in enumerate(mod.notes)] + ["", "", ""]
    parts = []
    for func, name in zip(mod.funcs, names, strict=True):
        doc = f'    """{prose(random.Random(f"{mod.noun}{func.kind}"), 3)}\n\n    The result is reduced modulo {func.m} so that it stays a small non-negative integer.\n    """'
        parts.append(f"def {name}(x: int) -> int:\n{doc}\n{_body(modules, mod, func, after)}\n")
    cls = (f"class {mod.noun.capitalize()}Calculator:\n    \"\"\"Wraps the first function of the module for object style callers.\"\"\"\n\n"
           f"    def run(self, x: int) -> int:\n        return {names[0]}(x)\n")
    return "\n".join(head) + "\n\n".join(parts) + "\n\n" + cls


def render_registry(modules: list[Module], after: bool) -> str:
    """The route table that names every function as a string."""
    routes = [f'    "{mod.noun}.{func_name(mod, f, after)}",' for mod in modules for f in mod.funcs]
    return '"""Route table: every function of the package, named as ``module.function``."""\n\nROUTES = [\n' + "\n".join(routes) + "\n]\n"


APP = '''"""Resolve every route by name and evaluate it."""

from __future__ import annotations

import importlib

from warehouse.registry import ROUTES


def resolve(route: str):
    """The function a route names."""
    module_name, function_name = route.split(".")
    module = importlib.import_module(f"warehouse.{module_name}")
    return getattr(module, function_name)


def run(seed: int = 7) -> list[int]:
    """Evaluate every route once, each with its own input."""
    return [resolve(route)(seed + index) for index, route in enumerate(ROUTES)]


def main() -> None:
    print(",".join(str(value) for value in run()))


if __name__ == "__main__":
    main()
'''

CHECK = '''"""Checks the snake_case migration of the warehouse package. Exit status 0 means done."""

import ast
import os
import pathlib
import re
import subprocess
import sys

EXPECTED = __EXPECTED__
OUTPUT_DIGEST = "__DIGEST__"
CAMEL = re.compile(r"\\b[a-z]+[A-Z][A-Za-z]*\\b")

problems = []
for module, names in EXPECTED.items():
    path = pathlib.Path("warehouse") / f"{module}.py"
    text = path.read_text(encoding="utf-8")
    defined = {n.name for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
    if defined != set(names):
        problems.append(f"{path}: functions {sorted(defined ^ set(names))} differ from the expected snake_case names")
for path in sorted(pathlib.Path("warehouse").glob("*.py")):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        found = CAMEL.findall(line)
        if found:
            problems.append(f"{path}:{number}: camelCase left: {found[0]}")
run = subprocess.run(
    [sys.executable, "-c", "import hashlib, warehouse.app as a; print(hashlib.sha256(','.join(map(str, a.run())).encode()).hexdigest()[:16])"],
    capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
)
if run.returncode != 0:
    problems.append("running the package failed: " + (run.stderr.strip().splitlines() or ["no output"])[-1])
elif run.stdout.strip() != OUTPUT_DIGEST:
    problems.append("the package computes different values than before the rename")
for problem in problems[:40]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
'''


def build() -> TaskBuild:
    rng     = rng_for(TASK_ID)
    modules = build_spec(rng)
    before  = {f"{PACKAGE}/{m.noun}.py": render_module(modules, m, False) for m in modules}
    after   = {f"{PACKAGE}/{m.noun}.py": render_module(modules, m, True) for m in modules}
    before[f"{PACKAGE}/registry.py"] = render_registry(modules, False)
    after[f"{PACKAGE}/registry.py"]  = render_registry(modules, True)
    for files in (before, after):
        files[f"{PACKAGE}/app.py"]      = APP
        files[f"{PACKAGE}/__init__.py"] = '"""Warehouse operations toolkit."""\n'
    code    = "import hashlib, warehouse.app as a; print(hashlib.sha256(','.join(map(str, a.run())).encode()).hexdigest()[:16])"
    output  = run_python(before, code).strip()
    assert run_python(after, code).strip() == output, "the rename must not change behaviour"
    expected = {m.noun: ["_".join(f.tokens) for f in m.funcs] for m in modules}
    check    = fill(CHECK, expected=repr(expected), digest=output)
    solution = {path: text for path, text in after.items() if before[path] != text}
    legacy   = sum(m.legacy for m in modules)
    prompt   = (f"The {PACKAGE} package has {len(modules)} modules. {legacy} of them define camelCase functions (for example computeOrderTotal) "
                "but the project convention is snake_case (compute_order_total). Rename every camelCase function to snake_case, "
                "update every import, call site and string that refers to it (the package resolves some functions by name from strings), "
                "and leave behaviour unchanged. Class names stay as they are. `python3 check.py` must pass.")
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": prompt, "verify": "python3 check.py", "tags": ["python", "refactor", "multi-file"]},
        fixture  = {**before, "check.py": check},
        solution = solution,
        reading  = sorted(path for path in solution),
    )
