"""scripts/bench_agent.py: the transcript audit for solution-lookup behaviour.

Author: 최진호
Date:   2026-10-03

The audit reads the ``tool_start`` events of a stream-json run. No model is called.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "bench_agent.py"
TASKS  = Path(__file__).parent.parent / "benchmarks" / "tasks"


def _load() -> ModuleType:
    spec   = importlib.util.spec_from_file_location("bench_agent", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_agent"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def _options() -> argparse.Namespace:
    return argparse.Namespace(approval_mode="yolo", sandbox="require", gate=False, set=[], model="", provider="", isolate=False, no_network=False)


def _call(name: str, summary: str) -> dict[str, str]:
    return {"type": "tool_start", "name": name, "summary": summary}


@pytest.mark.parametrize(("call", "kind"), [
    (_call("Bash", '{"command": "git log --all -p"}'),                           "git_history"),
    (_call("Bash", '{"command": "git -C /tmp/x show HEAD~1"}'),                  "git_history"),
    (_call("Bash", '{"command": "git --no-pager reflog"}'),                      "git_history"),
    (_call("FileRead", '{"path": ".git/packed-refs"}'),                          "git_internals"),
    (_call("Bash", '{"command": "pip install sum-range-fixed"}'),                "package_download"),
    (_call("Bash", '{"command": "npm view lodash"}'),                            "package_download"),
    (_call("Bash", '{"command": "curl -s https://evil.example/fix.patch"}'),     "network_fetch"),
    (_call("Bash", '{"command": "wget -qO- $URL"}'),                             "network_fetch"),
    (_call("Bash", '{"command": "cat benchmarks/solutions/sum-range/calc.py"}'), "solutions_read"),
    (_call("FileRead", '{"path": "/repo/solutions/sum-range/x.py"}'),            "solutions_read"),
    (_call("WebSearch", '{"query": "sum_range off by one"}'),                    "web_tool"),
])
def test_the_audit_flags_lookup_behaviour(call: dict[str, str], kind: str) -> None:
    findings = bench.audit_events([call], "sum-range")
    assert [f["kind"] for f in findings] == [kind]
    assert findings[0]["tool"] == call["name"]


@pytest.mark.parametrize("call", [
    _call("Bash", '{"command": "git status"}'),
    _call("Bash", '{"command": "git commit -m \'fix log message\'"}'),
    _call("Bash", '{"command": "git diff"}'),
    _call("Bash", '{"command": "python check.py"}'),
    _call("Bash", '{"command": "curl -s https://pypi.org/simple/requests/"}'),
    _call("FileRead", '{"path": "calc.py"}'),
    _call("Bash", '{"command": "pytest -q"}'),
    {"type": "tool_start", "name": "Bash"},
])
def test_the_audit_leaves_ordinary_work_alone(call: dict[str, str]) -> None:
    assert bench.audit_events([call], "sum-range") == []


def test_tool_events_are_read_from_the_stream_among_other_lines() -> None:
    out = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        "not json",
        json.dumps(_call("Bash", '{"command": "ls"}')),
        json.dumps({"type": "tool_done", "name": "Bash", "is_error": False}),
        json.dumps({"type": "result", "subtype": "success"}),
    ])
    assert bench.tool_events(out) == [_call("Bash", '{"command": "ls"}')]
    assert bench.parse_result(out)["subtype"] == "success"


def test_a_flagged_attempt_is_recorded_and_counted_in_the_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = (
        "import json\n"
        "print(json.dumps({'type': 'tool_start', 'name': 'Bash', 'summary': '{\"command\": \"git log -p\"}'}))\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success'}))\n"
    )
    monkeypatch.setattr(bench, "agent_command", lambda t, o: [sys.executable, "-c", script])
    task    = next(t for t in bench.load_tasks(TASKS) if t.id == "sum-range")
    flagged = bench.run_attempt(task, 1, _options(), tmp_path)
    assert [f["kind"] for f in flagged.audit] == ["git_history"]
    summary = bench.summarize([flagged, bench.Attempt("sum-range", 2, True)], 2)
    assert summary["audit_flagged_attempts"] == 1
    assert summary["audit_flagged_passed"] == 0
    assert summary["audit_kinds"] == {"git_history": 1}
    assert "looked for an answer" in bench.render(summary)
    assert json.dumps(bench.asdict(flagged))


def test_a_run_without_tool_events_has_an_empty_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = "import json\nprint(json.dumps({'type': 'result', 'subtype': 'success'}))\n"
    monkeypatch.setattr(bench, "agent_command", lambda t, o: [sys.executable, "-c", script])
    task = next(t for t in bench.load_tasks(TASKS) if t.id == "sum-range")
    assert bench.run_attempt(task, 1, _options(), tmp_path).audit == []


def test_the_agent_command_streams_events_so_the_audit_has_something_to_read() -> None:
    command = bench.agent_command(bench.Task("t", "p", "v", path="x"), _options())
    assert command[command.index("--output-format") + 1] == "stream-json"
