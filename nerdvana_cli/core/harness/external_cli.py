"""Other coding-agent CLIs as a benchmark substrate: how to run one on a task and read what it used.

``scripts/bench_agent.py --substrate external:claude`` runs a task with that CLI instead of
NerdVana's own loop, in the same working directory and judged by the same verify command, so
two CLIs can be compared on the same tasks. Each CLI runs headless, with its permission prompts
turned off, in the throwaway working directory the benchmark prepares.

The usage a CLI reports is read from its own output. A CLI that reports nothing leaves the
figure out, and a missing figure is "not measured", never zero.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The headless invocation of each CLI. ``{prompt}`` and ``{cwd}`` are filled in per task.
EXTERNAL_CLIS: dict[str, list[str]] = {
    "claude":   ["claude", "-p", "{prompt}", "--dangerously-skip-permissions", "--output-format", "json"],
    "codex":    ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json", "{prompt}"],
    "mcode":    ["mcode", "exec", "--cwd", "{cwd}", "--permission", "full", "--output-format", "json", "{prompt}"],
    "opencode": ["opencode", "run", "--format", "json", "{prompt}"],
    "copilot":  ["copilot", "-p", "{prompt}", "--allow-all-tools", "--allow-all-paths"],
}

# The flag that picks the model, per CLI.
_MODEL_FLAG: dict[str, str] = {"claude": "--model", "codex": "-m", "mcode": "--model", "opencode": "-m", "copilot": "--model"}


def external_command(cli: str, prompt: str, cwd: str, model: str = "") -> list[str]:
    """The command that runs *prompt* with *cli* in *cwd*; raises KeyError for an unknown CLI."""
    command = [part.replace("{prompt}", prompt).replace("{cwd}", cwd) for part in EXTERNAL_CLIS[cli]]
    if model:
        command += [_MODEL_FLAG[cli], model]
    return command


def external_usage(cli: str, stdout: str) -> dict[str, int]:
    """``turns``, ``input_tokens`` and ``output_tokens`` as far as *cli* reported them in *stdout*."""
    reader = _READERS.get(cli)
    return reader(stdout) if reader else {}


def _json_lines(stdout: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _claude(stdout: str) -> dict[str, int]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        lines = _json_lines(stdout)
        data = lines[-1] if lines else {}
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return {}
    prompt = sum(int(usage.get(key, 0) or 0) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {"turns": int(data.get("num_turns", 0) or 0), "input_tokens": prompt, "output_tokens": int(usage.get("output_tokens", 0) or 0)}


def _codex(stdout: str) -> dict[str, int]:
    turns = [event["usage"] for event in _json_lines(stdout) if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)]
    if not turns:
        return {}
    return {
        "input_tokens":  sum(int(u.get("input_tokens", 0) or 0) for u in turns),
        "output_tokens": sum(int(u.get("output_tokens", 0) or 0) for u in turns),
    }


def _opencode(stdout: str) -> dict[str, int]:
    found = {"input": 0, "output": 0, "read": 0}
    seen  = False
    for key in found:
        for value in re.findall(rf'"{key}"\s*:\s*(\d+)', stdout):
            found[key] += int(value)
            seen = True
    return {"input_tokens": found["input"] + found["read"], "output_tokens": found["output"]} if seen else {}


_READERS = {"claude": _claude, "codex": _codex, "opencode": _opencode}
