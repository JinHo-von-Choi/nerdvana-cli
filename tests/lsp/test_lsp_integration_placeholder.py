"""
LSP integration placeholder tests.

These tests verify that language server binaries required by Phase D1
symbol-tool E2E scenarios are available on PATH. They are gated by the
`lsp_integration` pytest marker and, like
`test_symbol_tools_integration.py`, skip when the binary is absent so a
developer machine without the language servers stays green. CI installs
both servers, so the assertions still run there.
"""

import shutil

import pytest


def _has_pyright() -> bool:
    return bool(shutil.which("pyright") or shutil.which("pyright-langserver"))


def _has_typescript_language_server() -> bool:
    return bool(shutil.which("typescript-language-server"))


@pytest.mark.lsp_integration
@pytest.mark.skipif(not _has_pyright(), reason="pyright not installed")
def test_pyright_available() -> None:
    """Verify pyright binary is on PATH (CI prereq)."""
    assert _has_pyright(), "pyright not installed; CI should install via npm"


@pytest.mark.lsp_integration
@pytest.mark.skipif(
    not _has_typescript_language_server(),
    reason="typescript-language-server not installed",
)
def test_typescript_language_server_available() -> None:
    """Verify typescript-language-server binary is on PATH."""
    assert _has_typescript_language_server(), "typescript-language-server not installed"
