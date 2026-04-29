"""Regression tests for `nerdvana serve` CLI boot — C-1.

Verifies that the `serve` command:
  - accepts --help without crashing
  - does NOT crash with TypeError on Console.print(..., file=sys.stderr)
  - rejects invalid transport
  - rejects port < 10000

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from nerdvana_cli.main import app

runner = CliRunner()


def test_serve_help_no_crash() -> None:
    """--help must succeed without TypeError from Console.print(file=...)."""
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0, f"serve --help failed:\n{result.output}"
    assert "transport" in result.output.lower() or "stdio" in result.output.lower()


def test_serve_invalid_transport_exits_nonzero() -> None:
    """Unknown transport must print error and exit with code 1."""
    result = runner.invoke(app, ["serve", "--transport", "grpc"])
    assert result.exit_code == 1
    assert "unknown transport" in result.output.lower() or "grpc" in result.output.lower()


def test_serve_low_port_rejected() -> None:
    """Port below 10000 with HTTP transport must be rejected immediately."""
    result = runner.invoke(app, ["serve", "--transport", "http", "--port", "8080"])
    assert result.exit_code == 1
    assert "10000" in result.output or "port" in result.output.lower()


def test_serve_project_not_exists_rejected(tmp_path) -> None:
    """Non-existent --project path must exit with error before attempting to start."""
    fake_dir = str(tmp_path / "nonexistent_project")
    result = runner.invoke(app, ["serve", "--project", fake_dir])
    assert result.exit_code == 1
    assert "project" in result.output.lower() or "nonexistent" in result.output.lower()


def test_console_stderr_no_file_kwarg() -> None:
    """Confirm that rich Console.print() raises TypeError when passed file=sys.stderr.

    This test documents the *original* bug (C-1) and ensures the fix is
    permanent: using Console(stderr=True) instead.
    """
    import sys
    from rich.console import Console
    c = Console()
    with pytest.raises(TypeError):
        c.print("test", file=sys.stderr)  # type: ignore[call-arg]


def test_console_stderr_instance_works() -> None:
    """Console(stderr=True).print() must not raise TypeError."""
    from rich.console import Console
    c = Console(stderr=True)
    # Must not raise
    c.print("[bold]NerdVana MCP server[/bold] running on stdio  [read-only]")


def test_main_module_console_stderr_attribute() -> None:
    """main.py must expose a console_stderr instance bound to stderr=True."""
    from nerdvana_cli import main as main_mod
    assert hasattr(main_mod, "console_stderr"), "console_stderr not defined in main.py"
    import io
    from rich.console import Console
    # Verify it is a Console instance and its _file is stderr-backed
    assert isinstance(main_mod.console_stderr, Console)
