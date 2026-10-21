"""session.statusline_command: its first output line reaches the status bar; a failing or slow command shows nothing."""

from __future__ import annotations

from pathlib import Path

from nerdvana_cli.ui.statusline import run_statusline
from nerdvana_cli.ui.widgets.status_bar import StatusBar


async def test_the_command_gets_the_session_and_its_first_line_is_kept(tmp_path: Path) -> None:
    script = tmp_path / "line.py"
    script.write_text("import json, sys\nd = json.load(sys.stdin)\nprint(f\"{d['model']} {d['input_tokens']}\")\nprint('second')\n")
    text = await run_statusline(f"python3 {script}", {"model": "m1", "input_tokens": 7}, str(tmp_path))
    assert text == "m1 7"


async def test_a_failing_command_shows_nothing(tmp_path: Path) -> None:
    assert await run_statusline("exit 3", {}, str(tmp_path)) == ""


async def test_a_slow_command_shows_nothing(tmp_path: Path) -> None:
    assert await run_statusline("sleep 5", {}, str(tmp_path)) == ""


def test_the_bar_shows_the_extra_text_last() -> None:
    bar = StatusBar()
    shown: list[str] = []
    bar.update = lambda content="": shown.append(str(content))  # type: ignore[method-assign]
    bar.update_status(model="m", provider="p", tools=3)
    bar.set_extra("branch main")
    assert shown[-1] == "p/m | tools: 3 | branch main"
