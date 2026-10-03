"""The MCP server as an edit backend: anchored reads, edits that need a read, a ledger per client.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer
from nerdvana_cli.tools import read_ledger


@pytest.fixture()
def server(tmp_path: Path) -> NerdvanaMcpServer:
    acl_file = tmp_path / "mcp_acl.yml"
    acl_file.write_text(
        "roles:\n  read-only: [FileRead]\n  edit: [FileEdit]\n"
        "clients:\n  alice:\n    roles: [read-only, edit]\n  bob:\n    roles: [read-only, edit]\n  reader:\n    roles: [read-only]\n",
        encoding="utf-8",
    )
    acl = ACLManager(acl_path=acl_file)
    acl.load()
    audit = AuditLogger(db_path=tmp_path / "audit.sqlite")
    audit.open()
    project = tmp_path / "project"
    project.mkdir()
    (project / "mod.py").write_text("def f():\n    return 1\n")
    read_ledger.clear()
    instance = NerdvanaMcpServer(allow_write=True, transport="stdio", acl_manager=acl, audit_logger=audit, project_path=project)
    yield instance
    audit.close()
    read_ledger.clear()


def _anchor(text: str, line: int) -> str:
    return next(row.split()[0] for row in text.splitlines() if row.startswith(f"{line}#"))


async def _call(server: NerdvanaMcpServer, who: str, tool: str, **args: Any) -> str:
    return await server._dispatch(tool, args, client_identity=who)


async def test_a_file_is_read_with_anchors_and_edited_by_anchor(server: NerdvanaMcpServer) -> None:
    text = await _call(server, "alice", "FileRead", path="mod.py")
    assert "1#" in text and "return 1" in text
    result = await _call(server, "alice", "FileEdit", path="mod.py", anchor_hash=_anchor(text, 2), new_string="    return 2\n")
    assert "Replaced anchor line 2" in result
    assert (server.project_path / "mod.py").read_text() == "def f():\n    return 2\n"


async def test_an_edit_without_a_read_is_refused(server: NerdvanaMcpServer) -> None:
    result = json.loads(await _call(server, "alice", "FileEdit", path="mod.py", old_string="return 1", new_string="return 9"))
    assert "has not been read" in result["error"]
    assert "return 1" in (server.project_path / "mod.py").read_text()


async def test_one_clients_read_does_not_vouch_for_another_clients_edit(server: NerdvanaMcpServer) -> None:
    await _call(server, "alice", "FileRead", path="mod.py")
    refused = json.loads(await _call(server, "bob", "FileEdit", path="mod.py", old_string="return 1", new_string="return 9"))
    assert "has not been read" in refused["error"]
    await _call(server, "bob", "FileRead", path="mod.py")
    assert "Replaced 1" in await _call(server, "bob", "FileEdit", path="mod.py", old_string="return 1", new_string="return 9")


async def test_a_file_changed_after_a_clients_read_is_refused(server: NerdvanaMcpServer) -> None:
    await _call(server, "alice", "FileRead", path="mod.py")
    await _call(server, "bob", "FileRead", path="mod.py")
    await _call(server, "bob", "FileEdit", path="mod.py", old_string="return 1", new_string="return 5")
    stale = json.loads(await _call(server, "alice", "FileEdit", path="mod.py", old_string="return 5", new_string="return 6"))
    assert "changed since it was last read" in stale["error"]


async def test_a_read_only_client_cannot_edit(server: NerdvanaMcpServer) -> None:
    await _call(server, "reader", "FileRead", path="mod.py")
    with pytest.raises(PermissionError):
        await _call(server, "reader", "FileEdit", path="mod.py", old_string="return 1", new_string="x")


async def test_paths_outside_the_project_are_refused(server: NerdvanaMcpServer) -> None:
    result = json.loads(await _call(server, "alice", "FileRead", path="../outside.txt"))
    assert "Path traversal blocked" in result["error"]


async def test_new_language_server_errors_are_reported_after_an_edit(server: NerdvanaMcpServer) -> None:
    class _Lsp:
        def __init__(self) -> None:
            self.calls = 0

        async def diagnostics(self, path: str) -> list[dict[str, Any]]:
            self.calls += 1
            return [{"severity": "error", "message": "old problem"}] + ([{"severity": "error", "message": "undefined name x"}] if self.calls > 1 else [])

    server._lsp = _Lsp()
    text = await _call(server, "alice", "FileRead", path="mod.py")
    result = await _call(server, "alice", "FileEdit", path="mod.py", anchor_hash=_anchor(text, 2), new_string="    return x\n")
    assert "New errors reported by the language server" in result and "undefined name x" in result and "old problem" not in result


async def test_a_broken_language_server_does_not_fail_the_edit(server: NerdvanaMcpServer) -> None:
    class _Broken:
        async def diagnostics(self, path: str) -> list[dict[str, Any]]:
            raise RuntimeError("server died")

    server._lsp = _Broken()
    text = await _call(server, "alice", "FileRead", path="mod.py")
    assert "Replaced anchor line 2" in await _call(server, "alice", "FileEdit", path="mod.py", anchor_hash=_anchor(text, 2), new_string="    return 3\n")


def test_the_default_roles_list_the_new_tools() -> None:
    from nerdvana_cli.server.acl import _DEFAULT_ROLE_TOOLS

    assert "FileRead" in _DEFAULT_ROLE_TOOLS["read-only"] and "FileEdit" in _DEFAULT_ROLE_TOOLS["edit"]
