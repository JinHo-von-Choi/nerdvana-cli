"""Landlock confinement of shell commands: planning, the launcher and the Bash tool.

The confinement tests run a real child process and are skipped on a kernel without
Landlock.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from nerdvana_cli.core import sandbox
from nerdvana_cli.core.sandbox import SETUP_FAILED, SandboxPolicy, plan_launch, wrap_command, writable_paths
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool

needs_landlock = pytest.mark.skipif(sandbox.landlock_abi() < 1, reason="the kernel has no Landlock")
needs_network_rules = pytest.mark.skipif(sandbox.landlock_abi() < 4, reason="needs Landlock ABI 4")


def _run(command: str, writable: list[Path], network: bool = True) -> subprocess.CompletedProcess[str]:
    argv = wrap_command(command, [str(p) for p in writable], network)
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_off_and_missing_policies_start_the_plain_shell() -> None:
    assert plan_launch(None, "ls", "/work").argv is None
    assert plan_launch(SandboxPolicy("off"), "ls", "/work").argv is None


def test_the_project_and_scratch_space_are_writable_and_extras_are_added(tmp_path: Path) -> None:
    extra   = tmp_path / "cache"
    extra.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    paths = writable_paths(SandboxPolicy("auto", True, (str(extra), str(tmp_path / "missing"))), str(project))
    assert str(project.resolve()) in paths
    assert str(extra.resolve()) in paths
    assert "/dev" in paths
    assert str((tmp_path / "missing").resolve()) not in paths
    assert len(paths) == len(set(paths))


def test_auto_runs_unconfined_with_a_notice_when_landlock_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    launch = plan_launch(SandboxPolicy("auto"), "ls", "/work")
    assert launch.argv is None and not launch.refused
    assert "without confinement" in launch.notice


def test_require_refuses_when_landlock_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    launch = plan_launch(SandboxPolicy("require"), "ls", "/work")
    assert launch.refused and launch.argv is None


def test_refusing_the_network_needs_abi_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 3)
    assert plan_launch(SandboxPolicy("require", network=False), "ls", "/work").refused
    assert plan_launch(SandboxPolicy("require", network=True), "ls", "/work").argv is not None


def test_the_launcher_command_keeps_the_command_as_one_argument() -> None:
    argv = wrap_command("echo 'a b' && ls", ["/work"], False)
    assert argv[-2:] == ["--", "echo 'a b' && ls"]
    assert "--no-network" in argv
    assert argv[argv.index("--write") + 1] == "/work"


# ---------------------------------------------------------------------------
# The launcher, against the real kernel
# ---------------------------------------------------------------------------


@needs_landlock
def test_a_command_can_write_in_an_allowed_directory_and_not_elsewhere(tmp_path: Path) -> None:
    allowed, other = tmp_path / "allowed", tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    done = _run(f"echo yes > {allowed}/a.txt; echo no > {other}/b.txt; echo status=$?", [allowed])
    assert (allowed / "a.txt").read_text().strip() == "yes"
    assert not (other / "b.txt").exists()
    assert "status=1" in done.stdout or "status=2" in done.stdout
    assert "Permission denied" in done.stderr


@needs_landlock
def test_deleting_renaming_and_creating_outside_the_scope_are_refused(tmp_path: Path) -> None:
    allowed, other = tmp_path / "allowed", tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    (other / "keep.txt").write_text("keep")
    _run(f"rm {other}/keep.txt; mv {other}/keep.txt {other}/moved.txt; mkdir {other}/sub; ln -s x {other}/link", [allowed])
    assert (other / "keep.txt").read_text() == "keep"
    assert sorted(p.name for p in other.iterdir()) == ["keep.txt"]


@needs_landlock
def test_reading_stays_possible_everywhere(tmp_path: Path) -> None:
    allowed, other = tmp_path / "allowed", tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    (other / "doc.txt").write_text("readable")
    assert _run(f"cat {other}/doc.txt", [allowed]).stdout.strip() == "readable"


@needs_landlock
def test_children_of_the_command_inherit_the_confinement(tmp_path: Path) -> None:
    allowed, other = tmp_path / "allowed", tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    _run(f"{sys.executable} -c \"open('{other}/py.txt', 'w').write('x')\" 2>/dev/null", [allowed])
    assert not (other / "py.txt").exists()


@needs_landlock
def test_the_exit_status_of_the_command_is_preserved(tmp_path: Path) -> None:
    assert _run("exit 7", [tmp_path]).returncode == 7


def test_a_setup_failure_exits_without_running_the_command(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(Path(sandbox.__file__)), "--bogus", "--", "echo ran"],
        capture_output=True, text=True, check=False,
    )
    assert done.returncode == SETUP_FAILED
    assert "ran" not in done.stdout


def _listening_port() -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server, int(server.getsockname()[1])


CONNECT = "import socket,sys; socket.create_connection(('127.0.0.1', {port}), timeout=3); print('connected')"


@needs_network_rules
def test_tcp_connections_are_refused_only_when_the_network_is_off(tmp_path: Path) -> None:
    server, port = _listening_port()
    try:
        command = f'{sys.executable} -c "{CONNECT.format(port=port)}"'
        assert "connected" in _run(command, [tmp_path], network=True).stdout
        blocked = _run(command + " 2>&1", [tmp_path], network=False)
        assert "connected" not in blocked.stdout.splitlines()
        assert "Permission denied" in blocked.stdout
    finally:
        server.close()


# ---------------------------------------------------------------------------
# Through the Bash tool and the configuration
# ---------------------------------------------------------------------------


def _context(tmp_path: Path, policy: SandboxPolicy | None) -> ToolContext:
    context = ToolContext(cwd=str(tmp_path))
    if policy is not None:
        context.state["sandbox"] = policy
    return context


@needs_landlock
async def test_the_bash_tool_confines_a_command_when_the_policy_asks(tmp_path: Path) -> None:
    # tmp_path lives in /tmp, which a confined command may always write, so the forbidden
    # directory has to be somewhere else the current user can write.
    outside = Path(__file__).parent / f".sandbox-outside-{tmp_path.name}"
    outside.mkdir()
    try:
        project = tmp_path / "project"
        project.mkdir()
        result = await BashTool().call(
            BashArgs(f"echo a > inside.txt; echo b > {outside}/out.txt"),
            _context(project, SandboxPolicy("require")),
        )
        assert (project / "inside.txt").read_text().strip() == "a"
        assert not (outside / "out.txt").exists()
        assert "Permission denied" in result.content
    finally:
        for leftover in outside.iterdir():
            leftover.unlink()
        outside.rmdir()


async def test_the_bash_tool_refuses_when_required_and_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    result = await BashTool().call(BashArgs("touch made.txt"), _context(tmp_path, SandboxPolicy("require")))
    assert result.is_error
    assert "sandbox required" in result.content
    assert not (tmp_path / "made.txt").exists()


async def test_the_bash_tool_without_a_policy_runs_the_plain_shell(tmp_path: Path) -> None:
    result = await BashTool().call(BashArgs("echo plain"), _context(tmp_path, None))
    assert result.content.strip() == "plain"


def test_the_sandbox_section_loads_and_a_bad_mode_stops_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core.settings import SettingsLoadError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    good = tmp_path / "good.yml"
    good.write_text("sandbox:\n  mode: auto\n  network: false\n  write_paths: [~/.cache]\n", encoding="utf-8")
    settings = NerdvanaSettings.load(str(good))
    assert (settings.sandbox.mode, settings.sandbox.network, settings.sandbox.write_paths) == ("auto", False, ["~/.cache"])

    bad = tmp_path / "bad.yml"
    bad.write_text("sandbox:\n  mode: sometimes\n", encoding="utf-8")
    with pytest.raises(SettingsLoadError):
        NerdvanaSettings.load(str(bad))


def test_the_loop_hands_the_policy_to_every_tool_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.session import SessionStorage
    from nerdvana_cli.core.tool import ToolRegistry

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.sandbox.mode        = "require"
    settings.sandbox.write_paths = ["/srv/cache"]
    settings.sandbox.edit_scope  = ["tests"]
    loop    = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="s", storage_dir=str(tmp_path / "s")))
    context = loop._new_tool_context()
    assert context.state["sandbox"] == SandboxPolicy("require", True, ("/srv/cache",))
    assert context.state["edit_scope"] == ["tests"]
