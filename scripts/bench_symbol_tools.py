"""bench_symbol_tools.py: how often the symbol tools answer correctly, and how fast, without a model.

Author: 최진호
Date:   2026-10-03

The symbol tools (find_symbol, symbol_overview, find_referencing_symbols, replace_symbol_body) sit on a
language server. This script asks them questions about this repository whose answers are known from the
source itself (the ``ast`` module, not the language server), checks each answer, and writes a JSON report
with the success rate and the latency per tool. No model is involved; the real language server is.

The cases are discovered from the source of the seed files, so they follow the code instead of rotting:

* find_symbol: every sampled function, class and method must come back with its name path, its file and
  its line; one substring query must find a symbol whose name contains the fragment;
* symbol_overview: every top-level function and class of a seed file must be listed (score: the share listed);
* find_referencing_symbols: the references of a symbol that other files use must include every one of those
  files (found by ``ast``: a name, an attribute or an import of that identifier); score: the share of those
  files reached;
* replace_symbol_body, on a temporary copy of the package: preview and apply a body that has one comment
  line added; the edited file must parse to the same syntax tree as the original, hold the comment once and
  be exactly the original plus that one line (the lines after the symbol kept).

Usage::

    uv run python scripts/bench_symbol_tools.py [--repeat 3] [--out report.json]

Needs ``pyright-langserver`` on PATH. Without it the report says ``skipped`` and the exit status is 2.
Exit status 1 when a case failed, 0 when all passed.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

ROOT        = Path(__file__).resolve().parent.parent
SEED_FILES  = (
    "nerdvana_cli/core/session.py",
    "nerdvana_cli/core/token_estimator.py",
    "nerdvana_cli/core/context_budget.py",
    "nerdvana_cli/core/observation_mask.py",
    "nerdvana_cli/core/nirnamd.py",
    "nerdvana_cli/commands/cost_command.py",
)
SCAN_DIRS   = ("nerdvana_cli", "tests", "scripts")
EDIT_MARKER = "# bench-symbol-tools marker"
TOOLS       = ("find_symbol", "symbol_overview", "find_referencing_symbols", "replace_symbol_body")
Definition  = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


class ToolOutput(NamedTuple):
    """What a tool returned: its text and whether it called that an error."""

    content:  str
    is_error: bool


Executor = Callable[[str, dict[str, Any]], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class Verdict:
    """The result of a check: what was wrong (None when nothing was) and an observation that is not a failure."""

    problem: str | None   = None
    note:    str          = ""
    score:   float | None = None


@dataclass(frozen=True)
class Case:
    """One question for one tool. ``check`` runs it through the executor and says what came of it."""

    name:  str
    tool:  str
    check: Callable[[Executor], Awaitable[Verdict]]


@dataclass(frozen=True)
class Outcome:
    """One run of one case."""

    name:       str
    tool:       str
    run:        int
    ok:         bool
    latency_ms: float
    problem:    str
    note:       str          = ""
    score:      float | None = None


@dataclass(frozen=True)
class Symbol:
    """A function, class or method found in a source file, from ``ast``."""

    path:      str
    name:      str
    name_path: str
    kind:      str
    line:      int
    def_line:  int
    end_line:  int
    indent:    int


# ---------------------------------------------------------------------------
# Ground truth from the source
# ---------------------------------------------------------------------------


def _symbol(node: Definition, path: str, prefix: str, kind: str) -> Symbol:
    """A symbol starts at its first decorator, as the language server reports it."""
    first = min([d.lineno for d in node.decorator_list] + [node.lineno])
    return Symbol(path, node.name, f"{prefix}{node.name}", kind, first, node.lineno, node.end_lineno or node.lineno, node.body[0].col_offset)


def symbols_of(path: Path, relative: str) -> list[Symbol]:
    """The top-level functions and classes of a file and the methods of those classes."""
    found: list[Symbol] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef):
            found.append(_symbol(node, relative, "", "class"))
            found += [_symbol(m, relative, f"{node.name}/", "method") for m in node.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)]
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append(_symbol(node, relative, "", "function"))
    return found


def _used_names(tree: ast.Module) -> set[str]:
    """Identifiers a module uses as a name, an attribute or an import.

    A function or class the module defines itself is a different symbol from one of the same name in another
    module, so using it is not a use of that one unless the module also imports the name.
    """
    names:    set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            imported.add(node.name.split(".")[-1])
    defined = {node.name for node in tree.body if isinstance(node, Definition)}
    return (names - defined) | imported


def identifier_index(root: Path, directories: Iterable[str] = SCAN_DIRS) -> dict[str, set[str]]:
    """Identifier to the relative paths of the files that use it."""
    index: dict[str, set[str]] = {}
    for directory in directories:
        for path in sorted((root / directory).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for name in _used_names(tree):
                index.setdefault(name, set()).add(path.relative_to(root).as_posix())
    return index


# ---------------------------------------------------------------------------
# Checks of tool output
# ---------------------------------------------------------------------------


def _json(output: ToolOutput) -> Any:
    if output.is_error:
        raise ValueError(f"the tool reported an error: {output.content[:200]}")
    try:
        return json.loads(output.content)
    except ValueError as exc:
        raise ValueError(f"the answer is not JSON: {output.content[:200]}") from exc


def check_find_symbol(output: ToolOutput, expected: Symbol) -> str | None:
    """None when *output* lists *expected* with its file and line, else the problem."""
    try:
        matches = _json(output)["matches"]
    except (ValueError, KeyError) as exc:
        return str(exc)
    for match in matches:
        location = match.get("location", {})
        if match.get("name_path") == expected.name_path and str(location.get("file", "")).endswith(expected.path):
            return None if location.get("line") == expected.line else f"line {location.get('line')}, expected {expected.line}"
    return f"{expected.name_path} not among {[m.get('name_path') for m in matches]}"


def check_overview(output: ToolOutput, expected: list[str]) -> Verdict:
    """Passes when every name of *expected* is a top-level symbol in *output*; the score is the share found."""
    try:
        listed = {s.get("name") for s in _json(output)["symbols"]}
    except (ValueError, KeyError) as exc:
        return Verdict(str(exc), score=0.0)
    missing = [name for name in expected if name not in listed]
    return Verdict(f"missing {missing}" if missing else None, score=1 - len(missing) / len(expected))


def check_references(output: ToolOutput, expected_files: set[str]) -> Verdict:
    """Passes when the references of *output* reach every file of *expected_files*; the score is the share reached."""
    try:
        reported = {r.get("file", "") for r in _json(output)["references"]}
    except (ValueError, KeyError) as exc:
        return Verdict(str(exc), score=0.0)
    missing = sorted(f for f in expected_files if not any(r.endswith(f) for r in reported))
    return Verdict(f"no reference in {missing}" if missing else None, score=1 - len(missing) / len(expected_files))


def marked_body(lines: list[str], symbol: Symbol) -> str:
    """The source of *symbol* with the marker comment on the line after its ``def`` or ``class`` line."""
    source = lines[symbol.line - 1:symbol.end_line]
    after  = symbol.def_line - symbol.line + 1
    return "".join([*source[:after], " " * symbol.indent + EDIT_MARKER + "\n", *source[after:]])


def expected_after_edit(original: str, symbol: Symbol) -> str:
    """*original* with the marker comment added to *symbol*."""
    lines = original.splitlines(keepends=True)
    return "".join(lines[:symbol.line - 1]) + marked_body(lines, symbol) + "".join(lines[symbol.end_line:])


def judge_edit(original: str, result: str, symbol: Symbol) -> Verdict:
    """Whether *result* is *original* with the marker comment added to *symbol*, and only that."""
    try:
        same_tree = ast.dump(ast.parse(result)) == ast.dump(ast.parse(original))
    except SyntaxError as exc:
        return Verdict(f"the edited file no longer parses: {exc}")
    if result.count(EDIT_MARKER) != 1:
        return Verdict(f"the marker is in the file {result.count(EDIT_MARKER)} times")
    if not same_tree:
        return Verdict("the syntax tree of the file changed")
    if result != expected_after_edit(original, symbol):
        return Verdict("the text around the symbol changed: the blank lines after it were not kept")
    return Verdict()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def find_symbol_case(symbol: Symbol) -> Case:
    async def check(execute: Executor) -> Verdict:
        arguments = {"name_path": symbol.name_path, "within_relative_path": symbol.path}
        return Verdict(check_find_symbol(await execute("find_symbol", arguments), symbol))

    return Case(f"find_symbol {symbol.path}::{symbol.name_path}", "find_symbol", check)


def substring_case(symbol: Symbol) -> Case:
    fragment = symbol.name[1:-1] if len(symbol.name) > 4 else symbol.name

    async def check(execute: Executor) -> Verdict:
        arguments = {"name_path": fragment, "substring_matching": True, "within_relative_path": symbol.path}
        return Verdict(check_find_symbol(await execute("find_symbol", arguments), symbol))

    return Case(f"find_symbol substring {fragment!r} in {symbol.path}", "find_symbol", check)


def overview_case(path: str, names: list[str]) -> Case:
    async def check(execute: Executor) -> Verdict:
        return check_overview(await execute("symbol_overview", {"relative_path": path}), names)

    return Case(f"symbol_overview {path}", "symbol_overview", check)


def references_case(symbol: Symbol, expected_files: set[str]) -> Case:
    async def check(execute: Executor) -> Verdict:
        output = await execute("find_referencing_symbols", {"name_path": symbol.name_path, "relative_path": symbol.path})
        return check_references(output, expected_files)

    return Case(f"find_referencing_symbols {symbol.path}::{symbol.name_path}", "find_referencing_symbols", check)


def edit_case(copy_root: Path, symbol: Symbol) -> Case:
    target = copy_root / symbol.path

    async def check(execute: Executor) -> Verdict:
        original = target.read_text(encoding="utf-8")
        body     = marked_body(original.splitlines(keepends=True), symbol)
        preview  = await execute("replace_symbol_body", {"name_path": symbol.name_path, "relative_path": symbol.path, "body": body})
        try:
            preview_id = _json(preview)["preview_id"]
            applied    = _json(await execute("replace_symbol_body", {"preview_id": preview_id, "apply": True}))
        except (ValueError, KeyError) as exc:
            return Verdict(str(exc))
        if applied.get("status") != "applied":
            return Verdict(f"status {applied.get('status')!r}")
        return judge_edit(original, target.read_text(encoding="utf-8"), symbol)

    return Case(f"replace_symbol_body {symbol.path}::{symbol.name_path}", "replace_symbol_body", check)


def pick(symbols: list[Symbol], count: int, kinds: tuple[str, ...]) -> list[Symbol]:
    """Up to *count* symbols of the given kinds, spread over the list, in a fixed order."""
    chosen = [s for s in symbols if s.kind in kinds]
    step   = max(1, len(chosen) // max(count, 1))
    return chosen[::step][:count]


def discover_cases(root: Path, copy_root: Path, seeds: Iterable[str], per_file: int, edits: int, scan: Iterable[str] = SCAN_DIRS) -> list[Case]:
    """The cases for *seeds* under *root*; edit cases work on the same files under *copy_root*.

    The directories in *scan* are searched for the files that use a symbol.
    """
    index                   = identifier_index(root, scan)
    cases: list[Case]       = []
    edit_pool: list[Symbol] = []
    for relative in seeds:
        symbols = symbols_of(root / relative, relative)
        top     = [s for s in symbols if s.kind != "method"]
        cases  += [find_symbol_case(s) for s in pick(symbols, per_file, ("function", "class", "method"))]
        if top:
            cases += [overview_case(relative, [s.name for s in top]), substring_case(top[0])]
        for symbol in pick(top, per_file, ("function", "class")):
            used_by = index.get(symbol.name, set()) - {relative}
            if used_by:
                cases.append(references_case(symbol, used_by))
        edit_pool += [s for s in symbols if s.kind == "function" and s.end_line > s.line]
    return cases + [edit_case(copy_root, s) for s in pick(edit_pool, edits, ("function",))]


# ---------------------------------------------------------------------------
# Running and reporting
# ---------------------------------------------------------------------------


async def run_case(case: Case, execute: Executor, run: int, timeout: float) -> Outcome:
    """One run of *case*: its latency, and whether it passed (an exception or a timeout is a failure)."""
    started = time.perf_counter()
    try:
        verdict = await asyncio.wait_for(case.check(execute), timeout)
    except TimeoutError:
        verdict = Verdict(f"no answer within {timeout:g}s")
    except Exception as exc:  # noqa: BLE001 - a broken tool is a failed case, not a crashed benchmark
        verdict = Verdict(f"{type(exc).__name__}: {exc}")
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return Outcome(case.name, case.tool, run, verdict.problem is None, elapsed, verdict.problem or "", verdict.note, verdict.score)


def _latency(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    p95 = statistics.quantiles(samples, n=20, method="inclusive")[18] if len(samples) > 1 else samples[0]
    return {
        "mean_ms": round(statistics.mean(samples), 2),
        "p50_ms":  round(statistics.median(samples), 2),
        "p95_ms":  round(p95, 2),
        "max_ms":  round(max(samples), 2),
    }


def _block(group: list[Outcome]) -> dict[str, Any]:
    passed = sum(o.ok for o in group)
    notes  = [o.note for o in group if o.note]
    scores = [o.score for o in group if o.score is not None]
    return {
        "runs":         len(group),
        "passed":       passed,
        "success_rate": round(passed / len(group), 4) if group else 0.0,
        **({"mean_score": round(statistics.mean(scores), 4)} if scores else {}),
        **_latency([o.latency_ms for o in group]),
        "notes":        {note: notes.count(note) for note in sorted(set(notes))},
    }


def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    """Runs, successes, success rate and latency, overall and per tool."""
    by_tool = {tool: _block([o for o in outcomes if o.tool == tool]) for tool in TOOLS if any(o.tool == tool for o in outcomes)}
    return {"overall": _block(outcomes), "by_tool": by_tool}


def build_report(outcomes: list[Outcome], server: str, status: str = "ok", reason: str = "", repository: str = "") -> dict[str, Any]:
    """The JSON report: what was measured with, the summary and every run."""
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repository":   repository,
        "server":       server,
        "status":       status,
        "reason":       reason,
        "summary":      summarize(outcomes),
        "failures":     [asdict(o) for o in outcomes if not o.ok],
        "runs":         [asdict(o) for o in outcomes],
    }


def exit_code(report: dict[str, Any]) -> int:
    """2 when the benchmark could not run, 1 when a case failed, else 0."""
    if report["status"] != "ok":
        return 2
    return 1 if report["failures"] else 0


async def run_all(cases: list[Case], execute: Executor, repeat: int, timeout: float) -> list[Outcome]:
    """Every case *repeat* times, in order."""
    return [await run_case(case, execute, run, timeout) for case in cases for run in range(1, repeat + 1)]


# ---------------------------------------------------------------------------
# The real language server
# ---------------------------------------------------------------------------


def _executor(tools: dict[str, Any], cwd: str) -> Executor:
    from nerdvana_cli.core.tool import ToolContext

    context = ToolContext(cwd=cwd)

    async def execute(name: str, arguments: dict[str, Any]) -> ToolOutput:
        tool   = tools[name]
        result = await tool.call(tool.parse_args(arguments), context, None)
        return ToolOutput(str(result.content), bool(result.is_error))

    return execute


async def _run_against(base: Path, cases: list[Case], repeat: int, timeout: float) -> list[Outcome]:
    """Run *cases* through the symbol tools of a language server that works on *base*."""
    from nerdvana_cli.core.code_editor import CodeEditor
    from nerdvana_cli.core.lsp_client import LspClient
    from nerdvana_cli.core.symbol import LanguageServerSymbolRetriever
    from nerdvana_cli.tools.symbol_tools import create_symbol_tools

    client    = LspClient(project_root=str(base))
    retriever = LanguageServerSymbolRetriever(client=client, project_root=str(base))
    tools     = {t.name: t for t in create_symbol_tools(client, retriever, CodeEditor(project_root=str(base)))}
    try:
        return await run_all(cases, _executor(tools, str(base)), repeat, timeout)
    finally:
        await client.close()


async def _measure(options: argparse.Namespace, copy_root: Path) -> list[Outcome]:
    """The read-only cases against the repository, the edit cases (once each) against *copy_root*."""
    cases = discover_cases(ROOT, copy_root, options.seed, options.per_file, options.edits)
    reads = [c for c in cases if c.tool != "replace_symbol_body"]
    edits = [c for c in cases if c.tool == "replace_symbol_body"]
    return await _run_against(ROOT, reads, options.repeat, options.timeout) + await _run_against(copy_root, edits, 1, options.timeout)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Success rate and latency of the symbol tools, without a model.")
    parser.add_argument("--repeat",   type=int,   default=3,    help="runs of each read-only case (edits run once)")
    parser.add_argument("--timeout",  type=float, default=60.0, help="seconds a case may take")
    parser.add_argument("--per-file", type=int,   default=3,    help="symbols sampled per seed file")
    parser.add_argument("--edits",    type=int,   default=4,    help="replace_symbol_body cases")
    parser.add_argument("--seed",     action="append",          help="seed file (repeatable; default: a fixed list of core modules)")
    parser.add_argument("--out",      default="",               help="also write the JSON report to this path")
    options      = parser.parse_args(sys.argv[1:] if argv is None else argv)
    options.seed = options.seed or list(SEED_FILES)
    return options


def main(argv: list[str] | None = None) -> int:
    options = _parse(argv)
    server  = shutil.which("pyright-langserver")
    if server is None:
        report = build_report([], "", "skipped", "pyright-langserver is not on PATH", str(ROOT))
    else:
        with tempfile.TemporaryDirectory(prefix="bench-symbols-") as tmp:
            copy_root = Path(tmp) / "repo"
            shutil.copytree(ROOT / "nerdvana_cli", copy_root / "nerdvana_cli", ignore=shutil.ignore_patterns("__pycache__"))
            report = build_report(asyncio.run(_measure(options, copy_root)), server, repository=str(ROOT))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if options.out:
        Path(options.out).write_text(text + "\n", encoding="utf-8")
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
