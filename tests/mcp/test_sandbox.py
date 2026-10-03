"""Confinement of stdio MCP server processes: planning, settings and a real confined server.

The confinement tests start a tiny stdio server that tries to write files and open connections, and are
skipped on a kernel without Landlock, as in tests/test_sandbox.py. The forbidden directory sits under
tests/, because /tmp is writable for every confined process.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from nerdvana_cli.core import sandbox
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig, load_mcp_config
from nerdvana_cli.mcp.manager import McpManager
from nerdvana_cli.mcp.sandbox import SandboxConfigError, plan_server_launch

needs_landlock      = pytest.mark.skipif(sandbox.landlock_abi() < 1, reason="the kernel has no Landlock")
needs_network_rules = pytest.mark.skipif(sandbox.landlock_abi() < 4, reason="needs Landlock ABI 4")

RAW_SERVER = str(Path(__file__).parent / "raw_server.py")


def _config(**fields: object) -> McpServerConfig:
    return McpServerConfig(name="s", transport="stdio", command=sys.executable, args=[RAW_SERVER, "writer"], **fields)  # type: ignore[arg-type]


@pytest.fixture
def forbidden() -> Iterator[Path]:
    """A directory under tests/, where a confined server may not write."""
    path = Path(__file__).parent / ".sandbox-forbidden"
    path.mkdir(exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def allowed() -> Iterator[Path]:
    """A directory under tests/ that the server is allowed to write."""
    path = Path(__file__).parent / ".sandbox-allowed"
    path.mkdir(exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


async def _attempt(config: McpServerConfig, tool: str, **arguments: object) -> tuple[str, str]:
    """The answer of one tool call on a freshly started server, and how the server was confined."""
    client = McpClient(config)
    await client.connect()
    try:
        result = await client.call_tool(tool, arguments)
    finally:
        await client.disconnect()
    return result["content"][0]["text"], client.confinement


class TestPlanning:
    def test_off_is_the_default_and_starts_the_command_as_it_is(self) -> None:
        launch = plan_server_launch(McpServerConfig(name="s", command="node", args=["a.js"]), "/work")

        assert launch.argv == ["node", "a.js"]
        assert launch.status == "unconfined (sandbox off)"

    def test_auto_wraps_the_command_in_the_launcher_when_the_kernel_can_confine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)

        launch = plan_server_launch(McpServerConfig(name="s", command="node", args=["my server.js"], sandbox="auto"), "/work")

        assert launch.argv[1:3] == ["-I", str(Path(sandbox.__file__).resolve())]
        assert launch.argv[-1] == "exec node 'my server.js'"
        assert launch.status.startswith("confined")

    def test_the_project_directory_is_not_writable_but_scratch_space_and_listed_paths_are(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)
        extra  = tmp_path / "data"
        extra.mkdir()
        config = McpServerConfig(name="s", command="x", sandbox="auto", write_paths=[str(extra)])

        argv    = plan_server_launch(config, str(tmp_path / "project")).argv
        granted = [argv[i + 1] for i, item in enumerate(argv) if item == "--write"]

        assert str(extra.resolve()) in granted
        assert "/dev" in granted
        assert str((tmp_path / "project").resolve()) not in granted

    def test_a_refused_network_is_passed_to_the_launcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 4)

        launch = plan_server_launch(McpServerConfig(name="s", command="x", sandbox="auto", network=False), "/work")

        assert "--no-network" in launch.argv
        assert "network refused" in launch.status

    def test_auto_runs_unconfined_with_the_reason_when_landlock_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)

        launch = plan_server_launch(McpServerConfig(name="s", command="node", sandbox="auto"), "/work")

        assert launch.argv == ["node"]
        assert launch.status.startswith("unconfined") and "Landlock is not available" in launch.status

    def test_require_refuses_to_start_when_landlock_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)

        with pytest.raises(SandboxConfigError, match="required but unavailable"):
            plan_server_launch(McpServerConfig(name="s", command="node", sandbox="require"), "/work")

    def test_refusing_the_network_needs_abi_four(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 3)

        with pytest.raises(SandboxConfigError, match="ABI 4"):
            plan_server_launch(McpServerConfig(name="s", command="x", sandbox="require", network=False), "/work")

    @pytest.mark.parametrize("fields", [{"sandbox": "strict"}, {"sandbox": True}, {"network": "false"}])
    def test_an_unusable_setting_is_an_error_not_a_silent_default(self, fields: dict[str, object]) -> None:
        with pytest.raises(SandboxConfigError):
            plan_server_launch(McpServerConfig(name="s", command="x", **fields), "/work")  # type: ignore[arg-type]


class TestConfigFile:
    def test_the_per_server_settings_are_read_from_mcp_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATA_DIR", "/srv/data")
        entry = {"command": "node", "sandbox": "require", "write_paths": ["${DATA_DIR}", "~/cache"], "network": False}
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"s": entry, "plain": {"command": "x"}}}), encoding="utf-8")

        configs = load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "absent.json"))

        assert (configs["s"].sandbox, configs["s"].write_paths, configs["s"].network) == ("require", ["/srv/data", "~/cache"], False)
        assert (configs["plain"].sandbox, configs["plain"].write_paths, configs["plain"].network) == ("off", [], True)

    def test_write_paths_that_are_not_a_list_of_strings_are_dropped(self, tmp_path: Path) -> None:
        entry = {"command": "node", "write_paths": "/srv/data"}
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"s": entry}}), encoding="utf-8")

        configs = load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "absent.json"))

        assert configs["s"].write_paths == []


@needs_landlock
class TestConfinedServer:
    @pytest.mark.asyncio
    async def test_a_confined_server_cannot_write_outside_its_paths(self, forbidden: Path) -> None:
        answer, status = await _attempt(_config(sandbox="auto"), "write", path=str(forbidden / "x.txt"))

        assert answer.startswith("denied")
        assert status.startswith("confined")
        assert not (forbidden / "x.txt").exists()

    @pytest.mark.asyncio
    async def test_an_unconfined_server_can_write_there(self, forbidden: Path) -> None:
        """The control: the same server with `sandbox` off writes the file, so the test above can fail."""
        answer, status = await _attempt(_config(), "write", path=str(forbidden / "x.txt"))

        assert answer == "ok"
        assert status.startswith("unconfined")
        assert (forbidden / "x.txt").exists()

    @pytest.mark.asyncio
    async def test_a_confined_server_can_write_its_listed_path_and_the_temporary_directory(
        self, allowed: Path, forbidden: Path, tmp_path: Path,
    ) -> None:
        config = _config(sandbox="require", write_paths=[str(allowed)])

        listed,  _ = await _attempt(config, "write", path=str(allowed / "x.txt"))
        scratch, _ = await _attempt(config, "write", path=str(tmp_path / "x.txt"))
        other,   _ = await _attempt(config, "write", path=str(forbidden / "x.txt"))

        assert (listed, scratch) == ("ok", "ok")
        assert other.startswith("denied")

    @pytest.mark.asyncio
    async def test_the_manager_reports_which_servers_are_confined(self, forbidden: Path) -> None:
        configs = {
            "locked": McpServerConfig(name="locked", command=sys.executable, args=[RAW_SERVER, "writer"], sandbox="auto"),
            "open":   McpServerConfig(name="open", command=sys.executable, args=[RAW_SERVER, "writer"]),
        }
        manager = McpManager(configs)
        await manager.connect_all()
        try:
            report = manager.get_confinement()
        finally:
            await manager.disconnect_all()

        assert report["locked"].startswith("confined")
        assert report["open"] == "unconfined (sandbox off)"

    @needs_network_rules
    @pytest.mark.asyncio
    async def test_a_server_with_network_false_cannot_connect(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            refused, _ = await _attempt(_config(sandbox="auto", network=False), "connect", port=port)
            allowed, _ = await _attempt(_config(sandbox="auto", network=True), "connect", port=port)

        assert refused.startswith("denied")
        assert allowed == "ok"

    @pytest.mark.asyncio
    async def test_require_without_landlock_stops_the_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)

        with pytest.raises(RuntimeError, match="required but unavailable"):
            await McpClient(_config(sandbox="require")).connect()
