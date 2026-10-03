"""The `mcp` SDK is an optional extra: what runs without it must still load.

The tool adapter, the skill support and the tool registry are used in every session, so they must not pull
in the SDK. Only the connection itself (`nerdvana_cli.mcp.client` and `manager`) needs it, and the REPL
start reports a missing package instead of stopping.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from nerdvana_cli.cli import runtime

PROGRAM = """
import sys
for name in ("mcp", "mcp_types"):
    sys.modules[name] = None
from nerdvana_cli.mcp.tools import McpToolAdapter
from nerdvana_cli.mcp.skills import McpSkillFileTool, McpSkillLibrary
from nerdvana_cli.mcp.input_requests import bind_ask_user
from nerdvana_cli.mcp.sandbox import plan_server_launch
from nerdvana_cli.ui.slash.session_commands import show_session_context
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.core.config.settings import NerdvanaSettings
settings = NerdvanaSettings()
settings.cwd = "."
create_tool_registry(mcp_tools=[McpSkillFileTool(McpSkillLibrary())], settings=settings)
try:
    import nerdvana_cli.mcp.manager
except ImportError:
    print("manager needs the sdk")
"""


def test_everything_but_the_connection_loads_without_the_sdk() -> None:
    result = subprocess.run([sys.executable, "-c", PROGRAM], capture_output=True, text=True, timeout=60, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "manager needs the sdk"


def test_the_repl_start_reports_a_missing_package_instead_of_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"s": {"command": "node"}}}), encoding="utf-8")
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setitem(sys.modules, "nerdvana_cli.mcp.manager", None)

    manager = asyncio.run(runtime._connect_mcp(str(tmp_path)))

    assert manager is None
    assert "pip install" in capsys.readouterr().out
