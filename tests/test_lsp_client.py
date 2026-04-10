"""Tests for LspClient — uses a mock language server process."""
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.core.lsp_client import LspClient, LspError


@pytest.mark.asyncio
async def test_has_any_server_true_when_binary_exists():
    """has_any_server() returns True when at least one LS binary is found."""
    client = LspClient()
    with patch("shutil.which", return_value="/usr/bin/pyright"):
        assert client.has_any_server() is True


@pytest.mark.asyncio
async def test_has_any_server_false_when_none_found():
    client = LspClient()
    with patch("shutil.which", return_value=None):
        assert client.has_any_server() is False


@pytest.mark.asyncio
async def test_available_tools_returns_names():
    """available_tools() returns tool objects for installed servers."""
    client = LspClient()
    with patch("shutil.which", side_effect=lambda b: "/usr/bin/pyright" if b == "pyright" else None):
        tools = client.available_tools()
    tool_names = [t.name for t in tools]
    assert "lsp_diagnostics" in tool_names


@pytest.mark.asyncio
async def test_diagnostics_parses_response():
    """diagnostics() correctly parses a minimal LSP diagnostic payload."""
    client = LspClient()

    fake_result = {
        "diagnostics": [
            {
                "range": {"start": {"line": 0, "character": 0}},
                "severity": 1,
                "message": "Undefined name 'foo'",
            }
        ]
    }

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=fake_result):
        diags = await client.diagnostics("/tmp/test.py")
    assert len(diags) == 1
    assert diags[0]["message"] == "Undefined name 'foo'"
    assert diags[0]["severity"] == "error"


@pytest.mark.asyncio
async def test_lsp_error_on_server_crash():
    """diagnostics() raises LspError when the server fails to start."""
    client = LspClient()
    with patch.object(client, "_start_server", side_effect=LspError("crash")), pytest.raises(LspError):
        await client.diagnostics("/tmp/test.py")
