"""The HTTP transport completes an MCP handshake behind the bearer-auth middleware.

The session manager starts in the lifespan of the inner app, so a wrapper that does not
hand the lifespan on answers every MCP request with a 500. Driving the real app through a
client that runs the lifespan is what catches that.

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

TOKEN   = "handshake-secret"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def server(tmp_path: Path) -> NerdvanaMcpServer:
    (tmp_path / "acl.yml").write_text("roles:\n  r:\n    - FileRead\nclients:\n  t:\n    roles: [r]\n", encoding="utf-8")
    acl = ACLManager(acl_path=tmp_path / "acl.yml")
    acl.load()
    digest = "sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest()
    (tmp_path / "keys.yml").write_text(f'keys:\n  - key_hash: "{digest}"\n    client_name: t\n    roles: [r]\n', encoding="utf-8")
    auth = AuthManager(keys_path=tmp_path / "keys.yml")
    auth.load()
    audit = AuditLogger(db_path=tmp_path / "audit.sqlite")
    audit.open()
    return NerdvanaMcpServer(transport="http", auth_manager=auth, acl_manager=acl, audit_logger=audit, project_path=tmp_path)


def _rpc(method: str, request_id: int | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        body["id"] = request_id
    return body


def _json_body(text: str) -> dict[str, Any]:
    """The JSON-RPC message of a response that is plain JSON or one server-sent event."""
    data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(data[0] if data else text)


def test_a_request_without_a_token_is_refused(server: NerdvanaMcpServer) -> None:
    with TestClient(server._http_app(), base_url="http://127.0.0.1:10830") as client:
        assert client.post("/mcp", headers=HEADERS, json=_rpc("initialize", 1)).status_code == 401


def test_initialize_and_list_tools_work_with_a_valid_token(server: NerdvanaMcpServer) -> None:
    headers = {**HEADERS, "Authorization": f"Bearer {TOKEN}"}
    init    = _rpc("initialize", 1, {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    with TestClient(server._http_app(), base_url="http://127.0.0.1:10830") as client:
        opened = client.post("/mcp", headers=headers, json=init)
        assert opened.status_code == 200
        assert _json_body(opened.text)["result"]["serverInfo"]["name"] == "nerdvana"
        session = {"Mcp-Session-Id": opened.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-03-26"}
        client.post("/mcp", headers={**headers, **session}, json=_rpc("notifications/initialized"))
        listed = client.post("/mcp", headers={**headers, **session}, json=_rpc("tools/list", 2))
        assert listed.status_code == 200
        assert "FileRead" in {t["name"] for t in _json_body(listed.text)["result"]["tools"]}
