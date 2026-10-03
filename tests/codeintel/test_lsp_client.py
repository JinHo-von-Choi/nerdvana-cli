"""Tests for LspClient — uses a mock language server process."""
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.codeintel.lsp_client import LspClient, LspError
from nerdvana_cli.tools.lsp import create_lsp_tools


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
async def test_create_lsp_tools_returns_names():
    """create_lsp_tools() returns tool objects for installed servers."""
    client = LspClient()
    with patch("shutil.which", side_effect=lambda b: "/usr/bin/pyright" if b == "pyright-langserver" else None):
        tools = create_lsp_tools(client)
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


class _FakeStdin:
    def __init__(self) -> None:
        self.frames: list[str] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data.decode())

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
async def test_changed_file_is_resent_with_did_change(tmp_path) -> None:
    """A file edited after didOpen is resent in full under the next version."""
    import json as _json
    from types import SimpleNamespace

    target = tmp_path / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")
    stdin  = _FakeStdin()
    client = LspClient(project_root=str(tmp_path))

    async def _proc(ext: str):  # noqa: ANN202
        return SimpleNamespace(stdin=stdin)

    client._get_proc = _proc  # type: ignore[method-assign]

    await client._ensure_open(".py", str(target))
    await client._ensure_open(".py", str(target))
    target.write_text("x = 2\n", encoding="utf-8")
    await client._ensure_open(".py", str(target))

    bodies = [_json.loads(frame.split("\r\n\r\n", 1)[1]) for frame in stdin.frames]
    assert [b["method"] for b in bodies] == ["textDocument/didOpen", "textDocument/didChange"]
    assert bodies[1]["params"]["textDocument"]["version"] == 2
    assert bodies[1]["params"]["contentChanges"] == [{"text": "x = 2\n"}]


@pytest.mark.asyncio
async def test_servers_are_started_in_stdio_mode_with_their_own_arguments(tmp_path) -> None:
    """pyright runs as pyright-langserver --stdio; pylsp takes no stdio flag."""
    from types import SimpleNamespace

    started: list[tuple[str, ...]] = []

    async def _exec(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        started.append(args)
        return SimpleNamespace(pid=1, returncode=None)

    for available, expected in (("pyright-langserver", ("pyright-langserver", "--stdio")), ("pylsp", ("pylsp",))):
        client = LspClient(project_root=str(tmp_path))
        with patch("shutil.which", side_effect=lambda b, a=available: f"/usr/bin/{b}" if b == a else None), \
             patch("asyncio.create_subprocess_exec", side_effect=_exec), \
             patch("nerdvana_cli.codeintel.lsp_client._track_proc"):
            await client._start_server(".py")
        assert started[-1] == expected
        assert client._binaries[".py"] == available
