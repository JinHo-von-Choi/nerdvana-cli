"""Boundary reproductions for the Grep reader, preview fingerprints, the
subprocess environment filter, and stdio transport hygiene.

Each test fails against the pre-fix code and passes after it. They are
reproductions of a specific escape or confusion, not coverage padding.

작성자: 최진호
작성일: 2026-09-10
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.code_editor import CodeEditor, StalePreviewError
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.main import app
from nerdvana_cli.tools.bash_tool import _build_env
from nerdvana_cli.tools.search_tools import GrepArgs, GrepTool

pytestmark = pytest.mark.security

runner = CliRunner()


# ---------------------------------------------------------------------------
# Grep: symlinks planted inside cwd must not read outside it
# ---------------------------------------------------------------------------


class TestGrepSymlinkContainment:
    """A symlink under cwd is a name inside the sandbox pointing outside it.

    os.walk with followlinks=False refuses to descend a symlinked directory,
    but it still lists symlinked *files*, and a plain open() then follows
    them. The reader has to refuse, not the walker.
    """

    SECRET = "root:x:0:0:boundary-canary:/root:/bin/sh"

    def test_symlink_to_file_outside_cwd_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        secret_file = outside / "passwd"
        secret_file.write_text(self.SECRET + "\n", encoding="utf-8")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "passwd").symlink_to(secret_file)

        result = asyncio.run(
            GrepTool().call(
                GrepArgs(pattern="boundary-canary"),
                ToolContext(cwd=str(workspace)),
            )
        )

        assert self.SECRET not in result.content
        assert "passwd:1:" not in result.content
        assert "No matches found" in result.content

    def test_symlinked_directory_inside_cwd_is_not_traversed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.txt").write_text(self.SECRET + "\n", encoding="utf-8")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "escape").symlink_to(outside, target_is_directory=True)

        result = asyncio.run(
            GrepTool().call(
                GrepArgs(pattern="boundary-canary"),
                ToolContext(cwd=str(workspace)),
            )
        )

        assert self.SECRET not in result.content
        assert "No matches found" in result.content

    def test_regular_file_inside_cwd_is_still_searched(self, tmp_path: Path) -> None:
        """The happy path: hardening must not cost ordinary search."""
        workspace = tmp_path / "workspace"
        (workspace / "pkg").mkdir(parents=True)
        (workspace / "pkg" / "module.py").write_text(
            "def handler():\n    return 'boundary-canary'\n",
            encoding="utf-8",
        )

        result = asyncio.run(
            GrepTool().call(
                GrepArgs(pattern="boundary-canary"),
                ToolContext(cwd=str(workspace)),
            )
        )

        assert "boundary-canary" in result.content
        assert os.path.join("pkg", "module.py") in result.content


# ---------------------------------------------------------------------------
# CodeEditor: fingerprint and validation must observe the same bytes
# ---------------------------------------------------------------------------


class TestPreviewFingerprintByteParity:
    def test_crlf_file_preview_applies_without_going_stale(self, tmp_path: Path) -> None:
        """Text-mode reading normalises CRLF, and the digest of the normalised
        text can never match the digest of the bytes on disk."""
        target = tmp_path / "windows.py"
        target.write_bytes(b"def hello():\r\n    return 1\r\n")

        editor = CodeEditor(project_root=str(tmp_path))
        preview_id, _ = editor.create_preview(
            "replace_body",
            {},
            {str(target): "def hello():\r\n    return 2\r\n"},
        )

        # Nothing has touched the file between preview and apply.
        editor.apply(preview_id)

    def test_new_file_preview_applies_without_going_stale(self, tmp_path: Path) -> None:
        """An absent file must fingerprint as absent, not as an empty file."""
        target = tmp_path / "created.py"
        assert not target.exists()

        editor = CodeEditor(project_root=str(tmp_path))
        preview_id, _ = editor.create_preview(
            "replace_body",
            {},
            {str(target): "def created():\n    return 0\n"},
        )

        editor.apply(preview_id)

    def test_real_modification_is_still_detected(self, tmp_path: Path) -> None:
        """The staleness check must keep working; parity is not a bypass."""
        target = tmp_path / "edited.py"
        target.write_bytes(b"a = 1\n")

        editor = CodeEditor(project_root=str(tmp_path))
        preview_id, _ = editor.create_preview("replace_body", {}, {str(target): "a = 2\n"})

        target.write_bytes(b"a = 99\n")

        with pytest.raises(StalePreviewError):
            editor.apply(preview_id)

    def test_new_file_created_before_apply_is_detected(self, tmp_path: Path) -> None:
        target = tmp_path / "raced.py"

        editor = CodeEditor(project_root=str(tmp_path))
        preview_id, _ = editor.create_preview("replace_body", {}, {str(target): "b = 1\n"})

        target.write_bytes(b"b = 0\n")

        with pytest.raises(StalePreviewError):
            editor.apply(preview_id)


# ---------------------------------------------------------------------------
# Subprocess environment: key material and connection strings
# ---------------------------------------------------------------------------


class TestSubprocessEnvironmentFilter:
    """Enumerating names is a mitigation, but os.environ is finite, so the
    enumeration is at least tractable. These names leaked before the widening.
    """

    WITHHELD = (
        "OPENAI_KEY",
        "DATABASE_URL",
        "GITHUB_PAT",
        "SSH_PRIVATE_KEY",
        "REDIS_URL",
        "POSTGRES_URI",
        "SERVICE_ACCOUNT_PEM",
        "APP_DSN",
        "ACCESS_KEY_ID",
        "STRIPE_SECRET",
        "USER_PASSPHRASE",
        "GITLAB_TOKEN",
        "OPENAI_API_KEY",
        "AWS_CREDENTIALS",
    )

    KEPT = (
        "PATH",
        "HOME",
        "LANG",
        "TOKENIZERS_PARALLELISM",
        "PROJECT_MODE",
        "VIRTUAL_ENV",
        "NODE_ENV",
        "PATTERN_FILE",
        "KEYBOARD_LAYOUT",
    )

    def test_sensitive_names_are_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in self.WITHHELD:
            monkeypatch.setenv(name, "canary-value")

        env = _build_env(cwd="/workdir")

        leaked = [name for name in self.WITHHELD if name in env]
        assert leaked == [], f"credential-named variables reached the subprocess: {leaked}"
        assert "canary-value" not in env.values()

    def test_ordinary_names_survive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in self.KEPT:
            monkeypatch.setenv(name, "ordinary-value")

        env = _build_env(cwd="/workdir")

        dropped = [name for name in self.KEPT if name not in env]
        assert dropped == [], f"ordinary variables were withheld: {dropped}"
        assert env["PWD"] == "/workdir"


# ---------------------------------------------------------------------------
# serve --transport stdio: stdout belongs to JSON-RPC
# ---------------------------------------------------------------------------


class _JsonRpcOnlyServer:
    """Stand-in server that writes one JSON-RPC frame and returns."""

    FRAME = '{"jsonrpc":"2.0","id":1,"result":{}}'

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def run(self) -> None:
        import sys

        sys.stdout.write(self.FRAME + "\n")


def _force_update_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the startup check report a newer release, unconditionally."""
    from nerdvana_cli.core import updater

    async def _fake_check(_current: str, ttl_hours: int = 24) -> dict[str, str]:
        return {"version": "99.0.0", "url": "https://example.invalid/99.0.0"}

    monkeypatch.delenv("NERDVANA_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(updater, "cached_or_check", _fake_check)
    monkeypatch.setattr(updater, "is_update_check_enabled", lambda _flag=True: True)


class TestStdioTransportStdoutHygiene:
    def test_update_notice_does_not_precede_the_first_frame(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from nerdvana_cli.server import mcp_server

        _force_update_notice(monkeypatch)
        monkeypatch.setattr(mcp_server, "NerdvanaMcpServer", _JsonRpcOnlyServer)

        result = runner.invoke(app, ["serve", "--transport", "stdio"])

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith('{"jsonrpc"'), (
            f"stdout did not begin with a JSON-RPC frame: {result.stdout!r}"
        )
        assert "99.0.0" not in result.stdout

    def test_notice_is_still_emitted_on_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Routing away from stdout must not silence the notice altogether."""
        from nerdvana_cli.server import mcp_server

        _force_update_notice(monkeypatch)
        monkeypatch.setattr(mcp_server, "NerdvanaMcpServer", _JsonRpcOnlyServer)

        result = runner.invoke(app, ["serve", "--transport", "stdio"])

        assert result.exit_code == 0, result.output
        assert "99.0.0" in result.stderr


# ---------------------------------------------------------------------------
# serve --tls-key: split certificate and key deployments
# ---------------------------------------------------------------------------


class TestServeTlsKeyOption:
    def test_tls_key_option_is_exposed(self) -> None:
        """Read the registered parameter rather than the rendered help.

        Rich wraps and truncates option names to the terminal width, so
        asserting against printed help passes locally and fails on a narrow
        CI terminal for reasons that have nothing to do with the option.
        """
        import click
        import typer.main

        command = typer.main.get_command(app)
        assert isinstance(command, click.Group)
        serve = command.get_command(click.Context(command), "serve")
        assert serve is not None

        option_names = {opt for param in serve.params for opt in param.opts}
        assert "--tls-key" in option_names

    def test_split_cert_and_key_are_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from nerdvana_cli.server import mcp_server

        cert = tmp_path / "server.crt"
        key = tmp_path / "server.key"
        cert.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        key.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")

        captured: dict[str, object] = {}

        class _Capturing(_JsonRpcOnlyServer):
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)
                super().__init__()

        monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
        monkeypatch.setattr(mcp_server, "NerdvanaMcpServer", _Capturing)

        result = runner.invoke(
            app,
            [
                "serve",
                "--transport", "http",
                "--tls-cert", str(cert),
                "--tls-key", str(key),
            ],
        )

        assert result.exit_code == 0, result.output
        assert captured["tls_key"] == key
        assert captured["tls_cert"] == cert

    def test_tls_misconfiguration_reports_without_traceback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        key = tmp_path / "server.key"
        key.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")

        monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")

        result = runner.invoke(app, ["serve", "--transport", "http", "--tls-key", str(key)])

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# --help provider list is generated, not transcribed
# ---------------------------------------------------------------------------


class TestProviderHelpList:
    def test_help_matches_the_provider_enum(self) -> None:
        from nerdvana_cli.providers.base import ProviderName

        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        flattened = " ".join(result.output.split())

        for provider in ProviderName:
            assert provider.value in flattened, f"{provider.value} missing from --help"

        for phantom in ("sambanova", "novitaai"):
            assert phantom not in flattened.lower(), f"{phantom} is not a ProviderName"
