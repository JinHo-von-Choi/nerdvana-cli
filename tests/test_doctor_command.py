"""Unit and integration tests for nerdvana doctor command.

작성자: 최진호
작성일: 2026-04-29
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(name: str, status: str, detail: str = ""):
    from nerdvana_cli.commands.doctor_command import CheckResult
    return CheckResult(name=name, status=status, detail=detail)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _check_python_version
# ---------------------------------------------------------------------------


class TestCheckPythonVersion:
    def test_ok_when_311(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_python_version
        with patch.object(sys, "version_info", (3, 11, 0)):
            r = _check_python_version()
        assert r.status == "ok"
        assert "3.11" in r.detail

    def test_fail_when_310(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_python_version
        with patch.object(sys, "version_info", (3, 10, 9)):
            r = _check_python_version()
        assert r.status == "fail"
        assert "3.11" in r.detail

    def test_ok_when_312(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_python_version
        with patch.object(sys, "version_info", (3, 12, 1)):
            r = _check_python_version()
        assert r.status == "ok"


# ---------------------------------------------------------------------------
# _check_uv_installed
# ---------------------------------------------------------------------------


class TestCheckUvInstalled:
    def test_ok_when_found(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_uv_installed
        with patch("shutil.which", return_value="/usr/local/bin/uv"):
            r = _check_uv_installed()
        assert r.status == "ok"
        assert "/usr/local/bin/uv" in r.detail

    def test_fail_when_missing(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_uv_installed
        with patch("shutil.which", return_value=None):
            r = _check_uv_installed()
        assert r.status == "fail"


# ---------------------------------------------------------------------------
# _check_install_paths
# ---------------------------------------------------------------------------


class TestCheckInstallPaths:
    def test_ok_when_writable(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_install_paths

        data_dir    = tmp_path / "data"
        install_dir = tmp_path / "install"
        data_dir.mkdir()
        install_dir.mkdir()

        with (
            patch("nerdvana_cli.core.paths.user_data_home", return_value=data_dir),
            patch("nerdvana_cli.core.paths.install_root",   return_value=install_dir),
        ):
            r = _check_install_paths()

        assert r.status == "ok"

    def test_fail_when_missing(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_install_paths

        missing = tmp_path / "nonexistent"

        with (
            patch("nerdvana_cli.core.paths.user_data_home", return_value=missing),
            patch("nerdvana_cli.core.paths.install_root",   return_value=missing),
        ):
            r = _check_install_paths()

        assert r.status == "fail"
        assert "Missing" in r.detail

    def test_warn_when_read_only(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_install_paths

        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(0o555)

        try:
            with (
                patch("nerdvana_cli.core.paths.user_data_home", return_value=ro_dir),
                patch("nerdvana_cli.core.paths.install_root",   return_value=ro_dir),
            ):
                r = _check_install_paths()
            assert r.status == "warn"
            assert "Read-only" in r.detail
        finally:
            ro_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# _check_provider_keys
# ---------------------------------------------------------------------------


class TestCheckProviderKeys:
    def test_warn_when_no_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nerdvana_cli.commands.doctor_command import _check_provider_keys
        from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS

        for env_vars in PROVIDER_KEY_ENVVARS.values():
            for v in env_vars:
                monkeypatch.delenv(v, raising=False)

        r = _check_provider_keys()
        assert r.status == "warn"
        assert "0/" in r.detail

    def test_ok_when_one_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nerdvana_cli.commands.doctor_command import _check_provider_keys
        from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS

        # Clear all then set exactly one
        for env_vars in PROVIDER_KEY_ENVVARS.values():
            for v in env_vars:
                monkeypatch.delenv(v, raising=False)

        first_var = next(iter(next(iter(PROVIDER_KEY_ENVVARS.values()))))
        monkeypatch.setenv(first_var, "test-key-value")

        r = _check_provider_keys()
        assert r.status == "ok"
        assert "/".join(["1", str(len(PROVIDER_KEY_ENVVARS))]) in r.detail


# ---------------------------------------------------------------------------
# _check_parism
# ---------------------------------------------------------------------------


class TestCheckParism:
    def test_warn_when_npx_missing(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_parism
        with patch("shutil.which", return_value=None):
            r = _check_parism()
        assert r.status == "warn"
        assert "npx" in r.detail

    def test_ok_when_parism_succeeds(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_parism

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout     = "1.2.3\n"
        mock_result.stderr     = ""

        with (
            patch("shutil.which", return_value="/usr/bin/npx"),
            patch("subprocess.run", return_value=mock_result),
        ):
            r = _check_parism()

        assert r.status == "ok"
        assert "1.2.3" in r.detail

    def test_warn_when_parism_fails(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_parism

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout     = ""
        mock_result.stderr     = "npm ERR!"

        with (
            patch("shutil.which", return_value="/usr/bin/npx"),
            patch("subprocess.run", return_value=mock_result),
        ):
            r = _check_parism()

        assert r.status == "warn"

    def test_warn_on_timeout(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_parism

        with (
            patch("shutil.which", return_value="/usr/bin/npx"),
            patch("subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("npx", 5)),
        ):
            r = _check_parism()

        assert r.status == "warn"
        assert "timeout" in r.detail


# ---------------------------------------------------------------------------
# _check_lsp_servers
# ---------------------------------------------------------------------------


class TestCheckLspServers:
    def test_ok_when_both_found(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_lsp_servers
        with patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}"):
            r = _check_lsp_servers()
        assert r.status == "ok"
        assert "pyright" in r.detail

    def test_ok_when_one_found(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_lsp_servers

        def _which(binary: str) -> str | None:
            return "/usr/bin/pyright" if binary == "pyright" else None

        with patch("shutil.which", side_effect=_which):
            r = _check_lsp_servers()

        assert r.status == "ok"
        assert "pyright" in r.detail

    def test_warn_when_none_found(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_lsp_servers
        with patch("shutil.which", return_value=None):
            r = _check_lsp_servers()
        assert r.status == "warn"


# ---------------------------------------------------------------------------
# _check_mcp_servers
# ---------------------------------------------------------------------------


class TestCheckMcpServers:
    def test_skip_when_no_config(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_mcp_servers
        with patch("nerdvana_cli.mcp.config.load_mcp_config", return_value={}):
            r = _check_mcp_servers()
        assert r.status == "skip"

    def test_ok_for_stdio_with_existing_command(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_mcp_servers
        from nerdvana_cli.mcp.config import McpServerConfig

        cfg = McpServerConfig(name="test", transport="stdio", command="python3")
        with (
            patch("nerdvana_cli.mcp.config.load_mcp_config", return_value={"test": cfg}),
            patch("shutil.which", return_value="/usr/bin/python3"),
        ):
            r = _check_mcp_servers()

        assert r.status == "ok"

    def test_warn_for_stdio_with_missing_command(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_mcp_servers
        from nerdvana_cli.mcp.config import McpServerConfig

        cfg = McpServerConfig(name="test", transport="stdio", command="nonexistent-tool")
        with (
            patch("nerdvana_cli.mcp.config.load_mcp_config", return_value={"test": cfg}),
            patch("shutil.which", return_value=None),
        ):
            r = _check_mcp_servers()

        assert r.status == "warn"

    def test_ok_for_http_200(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_mcp_servers
        from nerdvana_cli.mcp.config import McpServerConfig

        cfg = McpServerConfig(name="remote", transport="http", url="http://localhost:10830")
        with (
            patch("nerdvana_cli.mcp.config.load_mcp_config", return_value={"remote": cfg}),
            patch("nerdvana_cli.commands.doctor_command._ping_http", return_value=200),
        ):
            r = _check_mcp_servers()

        assert r.status == "ok"

    def test_warn_for_http_unreachable(self) -> None:
        from nerdvana_cli.commands.doctor_command import _check_mcp_servers
        from nerdvana_cli.mcp.config import McpServerConfig

        cfg = McpServerConfig(name="dead", transport="http", url="http://localhost:19999")
        with (
            patch("nerdvana_cli.mcp.config.load_mcp_config", return_value={"dead": cfg}),
            patch("nerdvana_cli.commands.doctor_command._ping_http", return_value=-1),
        ):
            r = _check_mcp_servers()

        assert r.status == "warn"


# ---------------------------------------------------------------------------
# _check_pricing_freshness
# ---------------------------------------------------------------------------


class TestCheckPricingFreshness:
    def test_skip_when_script_missing(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_pricing_freshness
        # Override repo-root detection by patching __file__
        with patch(
            "nerdvana_cli.commands.doctor_command.__file__",
            str(tmp_path / "commands" / "doctor_command.py"),
        ):
            r = _check_pricing_freshness()
        assert r.status == "skip"

    def test_ok_when_exit_0(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_pricing_freshness

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout     = "all ok\n"
        mock_result.stderr     = ""

        script = tmp_path / "scripts" / "check_pricing_freshness.py"
        script.parent.mkdir(parents=True)
        script.touch()

        with (
            patch(
                "nerdvana_cli.commands.doctor_command.__file__",
                str(tmp_path / "nerdvana_cli" / "commands" / "doctor_command.py"),
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            r = _check_pricing_freshness()

        assert r.status == "ok"

    def test_warn_when_exit_1(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_pricing_freshness

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout     = "stale: anthropic\n"
        mock_result.stderr     = ""

        script = tmp_path / "scripts" / "check_pricing_freshness.py"
        script.parent.mkdir(parents=True)
        script.touch()

        with (
            patch(
                "nerdvana_cli.commands.doctor_command.__file__",
                str(tmp_path / "nerdvana_cli" / "commands" / "doctor_command.py"),
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            r = _check_pricing_freshness()

        assert r.status == "warn"


# ---------------------------------------------------------------------------
# _check_collect_baseline
# ---------------------------------------------------------------------------


class TestCheckCollectBaseline:
    def test_skip_when_baseline_missing(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_collect_baseline

        # Create only the script, not the baseline
        script = tmp_path / "scripts" / "check_test_collection.py"
        script.parent.mkdir(parents=True)
        script.touch()

        with patch(
            "nerdvana_cli.commands.doctor_command.__file__",
            str(tmp_path / "nerdvana_cli" / "commands" / "doctor_command.py"),
        ):
            r = _check_collect_baseline()

        assert r.status == "skip"

    def test_skip_when_script_missing(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_collect_baseline

        # Create only the baseline, not the script
        baseline = tmp_path / "tests" / ".collect-baseline"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("total: 100\n")

        with patch(
            "nerdvana_cli.commands.doctor_command.__file__",
            str(tmp_path / "nerdvana_cli" / "commands" / "doctor_command.py"),
        ):
            r = _check_collect_baseline()

        assert r.status == "skip"

    def test_ok_when_exit_0(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_collect_baseline

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / ".collect-baseline").write_text("total: 100\n")
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "check_test_collection.py").touch()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout     = "105\n"
        mock_result.stderr     = ""

        with (
            patch(
                "nerdvana_cli.commands.doctor_command.__file__",
                str(tmp_path / "nerdvana_cli" / "commands" / "doctor_command.py"),
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            r = _check_collect_baseline()

        assert r.status == "ok"
        assert "105" in r.detail

    def test_warn_when_exit_1(self, tmp_path: Path) -> None:
        from nerdvana_cli.commands.doctor_command import _check_collect_baseline

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / ".collect-baseline").write_text("total: 200\n")
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "check_test_collection.py").touch()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout     = ""
        mock_result.stderr     = "FAIL: 180 tests, expected >= 200"

        with (
            patch(
                "nerdvana_cli.commands.doctor_command.__file__",
                str(tmp_path / "nerdvana_cli" / "commands" / "doctor_command.py"),
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            r = _check_collect_baseline()

        assert r.status == "warn"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestDoctorCliCommand:
    """Integration tests using typer.testing.CliRunner."""

    def _run(self, args: list[str]) -> Any:
        from typer.testing import CliRunner

        from nerdvana_cli.main import app

        runner = CliRunner()
        return runner.invoke(app, args)

    def test_basic_runs(self) -> None:
        """doctor runs without crashing and exits 0 or 1."""
        result = self._run(["doctor"])
        assert result.exit_code in (0, 1), (
            f"Unexpected exit code {result.exit_code}:\n{result.output}"
        )

    def test_json_output_is_valid(self) -> None:
        """--json produces valid JSON with 'checks' and 'exit_code' keys."""
        result = self._run(["doctor", "--json"])
        # Exit code 0 or 1 both valid
        assert result.exit_code in (0, 1)
        data = json.loads(result.output)
        assert "checks"    in data
        assert "exit_code" in data
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) == 9
        for item in data["checks"]:
            assert "name"   in item
            assert "status" in item
            assert item["status"] in ("ok", "warn", "fail", "skip")

    def test_strict_mode_exits_1_on_warn(self) -> None:
        """--strict causes exit 1 when any check is warn."""
        from nerdvana_cli.commands.doctor_command import CheckResult

        warn_results = [CheckResult("x", "warn", "test warn")]
        with patch(
            "nerdvana_cli.commands.doctor_command.run_all_checks",
            return_value=warn_results,
        ):
            result = self._run(["doctor", "--strict"])

        assert result.exit_code == 1

    def test_strict_mode_exits_0_all_ok(self) -> None:
        """--strict exits 0 when all checks pass."""
        from nerdvana_cli.commands.doctor_command import CheckResult

        ok_results = [CheckResult("x", "ok", "all good")]
        with patch(
            "nerdvana_cli.commands.doctor_command.run_all_checks",
            return_value=ok_results,
        ):
            result = self._run(["doctor", "--strict"])

        assert result.exit_code == 0

    def test_non_strict_warn_exits_0(self) -> None:
        """Without --strict, warn-only result exits 0."""
        from nerdvana_cli.commands.doctor_command import CheckResult

        warn_results = [CheckResult("x", "warn", "test warn")]
        with patch(
            "nerdvana_cli.commands.doctor_command.run_all_checks",
            return_value=warn_results,
        ):
            result = self._run(["doctor"])

        assert result.exit_code == 0

    def test_fail_exits_1(self) -> None:
        """Any fail result exits 1 regardless of --strict."""
        from nerdvana_cli.commands.doctor_command import CheckResult

        fail_results = [CheckResult("x", "fail", "something broken")]
        with patch(
            "nerdvana_cli.commands.doctor_command.run_all_checks",
            return_value=fail_results,
        ):
            result = self._run(["doctor"])

        assert result.exit_code == 1

    def test_skip_does_not_affect_exit_code(self) -> None:
        """skip status has no effect on exit code."""
        from nerdvana_cli.commands.doctor_command import CheckResult

        skip_results = [CheckResult("x", "skip", "not configured")]
        with patch(
            "nerdvana_cli.commands.doctor_command.run_all_checks",
            return_value=skip_results,
        ):
            result = self._run(["doctor"])

        assert result.exit_code == 0

    def test_json_with_fail_has_exit_code_1(self) -> None:
        """JSON output includes exit_code=1 when fail is present."""
        from nerdvana_cli.commands.doctor_command import CheckResult

        results = [CheckResult("broken", "fail", "oops")]
        with patch(
            "nerdvana_cli.commands.doctor_command.run_all_checks",
            return_value=results,
        ):
            result = self._run(["doctor", "--json"])

        data = json.loads(result.output)
        assert data["exit_code"] == 1
