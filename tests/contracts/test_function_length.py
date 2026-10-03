"""Function length ratchet for nerdvana_cli.

A function longer than ``LIMIT`` lines must be listed in ``KNOWN_LONG`` with the
length it has today. Listed functions may not grow, and a listed function that has
shrunk to the limit or vanished must be removed from the list, so the list only
ever gets shorter.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import ast
from pathlib import Path

import nerdvana_cli

ROOT  = Path(nerdvana_cli.__file__).resolve().parent
LIMIT = 60

KNOWN_LONG: dict[str, int] = {
    "core/setup.py:run_setup": 161,
    "ui/response_runner.py:run_response_stream": 151,
    "providers/openai_provider.py:OpenAIProvider.stream": 141,
    "main.py:run": 111,
    "tools/file_tools.py:FileEditTool.call": 116,
    "core/activity_state.py:summarize_tool_call": 108,
    "core/agent_loop.py:AgentLoop.__init__": 102,
    "commands/model_commands.py:switch_provider": 95,
    "core/settings.py:NerdvanaSettings.load": 92,
    "core/updater.py:run_self_update": 91,
    "tools/registry.py:create_tool_registry": 86,
    "providers/base.py:detect_provider": 82,
    "providers/gemini_provider.py:GeminiProvider.stream": 81,
    "providers/gemini_provider.py:GeminiProvider._convert_messages": 81,
    "tools/agent_tool.py:AgentTool.call": 74,
    "main.py:serve": 72,
    "providers/gemini_provider.py:GeminiProvider.send": 71,
    "tools/web_tools.py:WebSearchTool.call": 71,
    "core/migrate.py:run_if_needed": 70,
    "utils/path.py:safe_open_fd": 70,
    "tools/external_project_tools.py:RegisterExternalProjectTool._safe_resolve": 68,
    "core/code_editor.py:CodeEditor.prepare_insert_after": 67,
    "main.py:main": 67,
    "tools/file_tools.py:FileReadTool.call": 64,
    "tools/search_tools.py:GrepTool.call": 62,
    "utils/path.py:safe_makedirs": 62,
}


class _Lengths(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path   = path
        self.stack: list[str]       = []
        self.found: dict[str, int]  = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        if length > LIMIT:
            self.found[f"{self.path}:{'.'.join([*self.stack, node.name])}"] = length
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._function(node)


def _long_functions() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(ROOT.rglob("*.py")):
        visitor = _Lengths(path.relative_to(ROOT).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        found.update(visitor.found)
    return found


def test_no_new_function_is_longer_than_the_limit_and_known_ones_do_not_grow() -> None:
    problems = [
        f"{name}: {length} lines" + (f" (was {KNOWN_LONG[name]})" if name in KNOWN_LONG else f" (limit {LIMIT})")
        for name, length in _long_functions().items()
        if length > KNOWN_LONG.get(name, LIMIT)
    ]
    assert problems == []


def test_entries_that_shrank_or_vanished_are_removed_from_the_list() -> None:
    current = _long_functions()
    stale   = [name for name in KNOWN_LONG if name not in current]
    assert stale == []
