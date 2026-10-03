"""The memory tools registered on the MCP server pass their arguments on as the tools declare them.

The wrappers in the catalogue once sent a keyword the tool does not accept (EditMemory) and a
scope value the tool rejects (WriteMemory), so neither call could succeed. The round trip below
runs through the registered tools in-process and through the real HTTP handshake.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer

TOKEN   = "memory-secret"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
MEMORY  = "wrappers/roundtrip"
ACL     = (
    "roles:\n"
    "  r:\n"
    "    - ReadMemory\n"
    "    - ListMemories\n"
    "    - WriteMemory\n"
    "    - EditMemory\n"
    "    - DeleteMemory\n"
    "clients:\n"
    "  t:\n"
    "    roles: [r]\n"
)


@pytest.fixture
def server(tmp_path: Path) -> NerdvanaMcpServer:
    (tmp_path / "acl.yml").write_text(ACL, encoding="utf-8")
    acl = ACLManager(acl_path=tmp_path / "acl.yml")
    acl.load()
    digest = "sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest()
    (tmp_path / "keys.yml").write_text(f'keys:\n  - key_hash: "{digest}"\n    client_name: t\n    roles: [r]\n', encoding="utf-8")
    auth = AuthManager(keys_path=tmp_path / "keys.yml")
    auth.load()
    audit = AuditLogger(db_path=tmp_path / "audit.sqlite")
    audit.open()
    project = tmp_path / "project"
    project.mkdir()
    return NerdvanaMcpServer(
        allow_write=True, transport="http", auth_manager=auth, acl_manager=acl, audit_logger=audit, project_path=project,
    )


def _declared(server: NerdvanaMcpServer, name: str) -> tuple[set[str], set[str]]:
    """Declared and required argument names of a registered MCP tool."""
    schema = server.fmcp._tool_manager.get_tool(name).parameters
    return set(schema["properties"]), set(schema.get("required", []))


def test_the_wrapper_signatures_cover_the_tool_arguments(server: NerdvanaMcpServer) -> None:
    for name in ("WriteMemory", "EditMemory"):
        declared, _ = _declared(server, name)
        assert set(server._tool_map[name].input_schema["properties"]) <= declared, name
    assert "scope" in _declared(server, "WriteMemory")[1]
    assert "new_content" not in _declared(server, "EditMemory")[0]


@pytest.mark.asyncio
async def test_round_trip_in_process(server: NerdvanaMcpServer) -> None:
    server.transport       = "stdio"
    server._stdio_identity = "t"
    call = server.fmcp.call_tool

    written = await call("WriteMemory", {"name": MEMORY, "content": "alpha beta", "scope": "project_knowledge", "confirm": True})
    assert not written.is_error, written
    assert MEMORY in (await call("ListMemories", {"topic": ""})).content[0].text
    assert "alpha beta" in (await call("ReadMemory", {"name": MEMORY})).content[0].text

    edited = await call("EditMemory", {"name": MEMORY, "needle": "beta", "repl": "gamma", "confirm": True})
    assert not edited.is_error, edited
    assert "alpha gamma" in (await call("ReadMemory", {"name": MEMORY})).content[0].text

    deleted = await call("DeleteMemory", {"name": MEMORY, "confirm": True})
    assert not deleted.is_error, deleted
    assert MEMORY not in (await call("ListMemories", {"topic": ""})).content[0].text


@pytest.mark.asyncio
async def test_an_invalid_scope_is_reported_with_the_valid_values(server: NerdvanaMcpServer) -> None:
    server.transport       = "stdio"
    server._stdio_identity = "t"
    result = await server.fmcp.call_tool("WriteMemory", {"name": MEMORY, "content": "x", "scope": "local", "confirm": True})
    assert "project_knowledge" in result.content[0].text


def _rpc(method: str, request_id: int | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        body["id"] = request_id
    return body


def _json_body(text: str) -> dict[str, Any]:
    """The JSON-RPC message of a response that is plain JSON or one server-sent event."""
    data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(data[0] if data else text)


def test_round_trip_over_the_http_handshake(server: NerdvanaMcpServer) -> None:
    headers = {**HEADERS, "Authorization": f"Bearer {TOKEN}"}
    init    = _rpc("initialize", 1, {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    with TestClient(server._http_app(), base_url="http://127.0.0.1:10830") as client:
        opened  = client.post("/mcp", headers=headers, json=init)
        session = {"Mcp-Session-Id": opened.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-03-26"}
        client.post("/mcp", headers={**headers, **session}, json=_rpc("notifications/initialized"))
        counter = iter(range(2, 100))

        def call(name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
            body   = _rpc("tools/call", next(counter), {"name": name, "arguments": arguments})
            result = _json_body(client.post("/mcp", headers={**headers, **session}, json=body).text)["result"]
            return bool(result.get("isError")), result["content"][0]["text"]

        assert call("WriteMemory", {"name": MEMORY, "content": "alpha beta", "scope": "project_knowledge", "confirm": True})[0] is False
        assert MEMORY in call("ListMemories", {})[1]
        assert "alpha beta" in call("ReadMemory", {"name": MEMORY})[1]
        assert call("EditMemory", {"name": MEMORY, "needle": "beta", "repl": "gamma", "confirm": True})[0] is False
        assert "alpha gamma" in call("ReadMemory", {"name": MEMORY})[1]
        assert call("DeleteMemory", {"name": MEMORY, "confirm": True})[0] is False
        assert MEMORY not in call("ListMemories", {})[1]
