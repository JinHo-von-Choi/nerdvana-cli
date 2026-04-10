"""Tests for LSP tool registration in the tool registry."""
from unittest.mock import MagicMock, patch

from nerdvana_cli.tools.registry import create_tool_registry


def test_lsp_tools_registered_when_server_present():
    """LSP tools appear in registry when a language server binary is installed."""
    fake_tool = MagicMock()
    fake_tool.name = "lsp_diagnostics"
    with patch(
        "nerdvana_cli.core.lsp_client.LspClient.has_any_server",
        return_value=True,
    ), patch(
        "nerdvana_cli.core.lsp_client.LspClient.available_tools",
        return_value=[fake_tool],
    ):
        registry = create_tool_registry()
    tool_names = [t.name for t in registry.all_tools()]
    assert "lsp_diagnostics" in tool_names


def test_lsp_tools_absent_when_no_server():
    """LSP tools are NOT registered when no language server binary is found."""
    with patch(
        "nerdvana_cli.core.lsp_client.LspClient.has_any_server",
        return_value=False,
    ):
        registry = create_tool_registry()
    tool_names = [t.name for t in registry.all_tools()]
    lsp_names = [n for n in tool_names if n.startswith("lsp_")]
    assert lsp_names == []
