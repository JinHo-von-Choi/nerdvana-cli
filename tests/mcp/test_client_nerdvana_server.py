"""The MCP client against the repository's own server (`nerdvana serve`), over stdio and over HTTP.

Both runs list the tools and call FileRead through the SDK-based client, which is the check that the client
and the server agree on a protocol revision without a mock in between. The stdio server is a real
subprocess; the HTTP server is the real app served by uvicorn on an ephemeral port behind the bearer key.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer

TOKEN     = "client-test-secret"
FILE_TEXT = "hello from the project\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "hello.txt").write_text(FILE_TEXT, encoding="utf-8")
    return root


@pytest.fixture
def http_server(tmp_path: Path, project: Path) -> Iterator[int]:
    """The app of `nerdvana serve --transport http`, listening on an ephemeral port; yields the port."""
    (tmp_path / "acl.yml").write_text("roles:\n  r:\n    - FileRead\nclients:\n  t:\n    roles: [r]\n", encoding="utf-8")
    acl = ACLManager(acl_path=tmp_path / "acl.yml")
    acl.load()
    digest = "sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest()
    (tmp_path / "keys.yml").write_text(f'keys:\n  - key_hash: "{digest}"\n    client_name: t\n    roles: [r]\n', encoding="utf-8")
    auth = AuthManager(keys_path=tmp_path / "keys.yml")
    auth.load()
    audit = AuditLogger(db_path=tmp_path / "audit.sqlite")
    audit.open()
    app = NerdvanaMcpServer(
        transport="http", auth_manager=auth, acl_manager=acl, audit_logger=audit, project_path=project,
    )._http_app()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run([sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the HTTP server did not start"
    try:
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def _text(result: dict) -> str:
    return "\n".join(item["text"] for item in result["content"])


@pytest.mark.asyncio
async def test_stdio_server_lists_and_calls_file_read(project: Path, tmp_path: Path) -> None:
    config = McpServerConfig(
        name="nerdvana-stdio",
        transport="stdio",
        command=sys.executable,
        args=["-c", "from nerdvana_cli.main import app; app()", "serve", "--project", str(project)],
        env={"NERDVANA_DATA_HOME": str(tmp_path / "data")},
    )
    client = McpClient(config)

    handshake = await client.connect()
    try:
        tools  = await client.list_tools()
        result = await client.call_tool("FileRead", {"path": "hello.txt"})
    finally:
        await client.disconnect()

    assert handshake["serverInfo"]["name"] == "nerdvana"
    assert "FileRead" in {tool["name"] for tool in tools}
    assert result["isError"] is False
    assert FILE_TEXT.strip() in _text(result)


@pytest.mark.asyncio
async def test_http_server_lists_and_calls_file_read_with_a_bearer_key(http_server: int, project: Path) -> None:
    config = McpServerConfig(
        name="nerdvana-http",
        transport="http",
        url=f"http://127.0.0.1:{http_server}/mcp",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    client = McpClient(config)

    handshake = await client.connect()
    try:
        tools  = await client.list_tools()
        result = await client.call_tool("FileRead", {"path": "hello.txt"})
    finally:
        await client.disconnect()

    assert handshake["serverInfo"]["name"] == "nerdvana"
    assert "FileRead" in {tool["name"] for tool in tools}
    assert result["isError"] is False
    assert FILE_TEXT.strip() in _text(result)


@pytest.mark.asyncio
async def test_http_server_refuses_a_wrong_bearer_key(http_server: int) -> None:
    config = McpServerConfig(
        name="nerdvana-http",
        transport="http",
        url=f"http://127.0.0.1:{http_server}/mcp",
        headers={"Authorization": "Bearer not-the-key"},
    )

    with pytest.raises(RuntimeError):
        await McpClient(config).connect()
