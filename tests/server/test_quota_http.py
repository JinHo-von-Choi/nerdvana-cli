"""A tenant over its quota gets a readable refusal over the HTTP transport.

mcp reports a tool exception as a tool result with ``isError:true`` inside an HTTP 200 response,
so the quota refusal reaches the client as that result, with the reason, and not as an HTTP 429.
See ``docs/mcp-quota.md``.

작성자: 최진호
작성일: 2026-05-13
수정일: 2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer
from nerdvana_cli.server.quota import QuotaPolicy, QuotaPolicyResolver, QuotaStore

TOKEN = "test-secret-12345"
BASE  = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _make_permissive_acl(tmp_path: Path) -> ACLManager:
    acl_file = tmp_path / "acl.yml"
    acl_file.write_text(
        "roles:\n"
        "  read-only:\n"
        "    - ListMemories\n"
        "    - ReadMemory\n"
        "    - GetCurrentConfig\n"
        "clients:\n"
        "  test-tenant:\n"
        "    roles: [read-only]\n",
        encoding="utf-8",
    )
    mgr = ACLManager(acl_path=acl_file)
    mgr.load()
    return mgr


def _make_auth_manager(tmp_path: Path, bearer_token: str) -> AuthManager:
    import hashlib

    digest    = "sha256:" + hashlib.sha256(bearer_token.encode()).hexdigest()
    keys_file = tmp_path / "mcp_keys.yml"
    keys_file.write_text(
        f"keys:\n"
        f'  - key_hash: "{digest}"\n'
        f"    client_name: test-tenant\n"
        f"    roles: [read-only]\n",
        encoding="utf-8",
    )
    mgr = AuthManager(keys_path=keys_file)
    mgr.load()
    return mgr


def _rpc(method: str, request_id: int | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        body["id"] = request_id
    return body


def _message(text: str) -> dict[str, Any]:
    data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(data[0] if data else text)


def test_second_request_over_the_rate_limit_is_refused_with_its_reason(tmp_path: Path) -> None:
    audit = AuditLogger(db_path=tmp_path / "audit.sqlite")
    audit.open()
    server = NerdvanaMcpServer(
        transport      = "http",
        auth_manager   = _make_auth_manager(tmp_path, TOKEN),
        acl_manager    = _make_permissive_acl(tmp_path),
        audit_logger   = audit,
        quota_resolver = QuotaPolicyResolver(per_tenant={"test-tenant": QuotaPolicy(rpm=1)}),
        quota_store    = QuotaStore(),
        project_path   = tmp_path,
    )
    call = _rpc("tools/call", 2, {"name": "ListMemories", "arguments": {"topic": ""}})
    try:
        with TestClient(server._http_app(), base_url="http://127.0.0.1:10830") as client:
            opened  = client.post("/mcp", headers=BASE, json=_rpc("initialize", 1, {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}))
            session = {**BASE, "Mcp-Session-Id": opened.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-03-26"}
            client.post("/mcp", headers=session, json=_rpc("notifications/initialized"))
            first  = client.post("/mcp", headers=session, json=call)
            second = client.post("/mcp", headers=session, json={**call, "id": 3})
    finally:
        audit.close()

    assert _message(first.text)["result"].get("isError") is not True
    refused = _message(second.text)["result"]
    assert second.status_code == 200
    assert refused["isError"] is True
    assert "rpm" in refused["content"][0]["text"].lower()
