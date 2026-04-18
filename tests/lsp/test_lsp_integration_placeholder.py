"""
LSP integration placeholder tests.

These tests verify that language server binaries required by Phase D1
symbol-tool E2E scenarios are available on PATH. They run only in CI
(or locally when the binaries are installed) and are gated by the
`lsp_integration` pytest marker.
"""

import shutil

import pytest


@pytest.mark.lsp_integration
def test_pyright_available() -> None:
    """Verify pyright binary is on PATH (CI prereq)."""
    assert shutil.which("pyright") or shutil.which("pyright-langserver"), (
        "pyright not installed; CI should install via npm"
    )


@pytest.mark.lsp_integration
def test_typescript_language_server_available() -> None:
    """Verify typescript-language-server binary is on PATH."""
    assert shutil.which("typescript-language-server"), (
        "typescript-language-server not installed"
    )
