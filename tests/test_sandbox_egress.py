"""A confined command with ``network: allowlist``, against the real kernel.

The command can reach the egress proxy and, through it, an allowed local fake host; a direct
connection to any other local port fails. Skipped on a kernel without Landlock ABI 4.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nerdvana_cli.core.safety import egress_proxy, sandbox
from nerdvana_cli.core.safety.sandbox import SandboxPolicy
from nerdvana_cli.core.signals import EGRESS_DENIED, classify_result
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool

pytestmark = pytest.mark.skipif(sandbox.landlock_abi() < 4, reason="needs Landlock ABI 4")

FETCH = """
import socket, sys, urllib.error, urllib.request

def fetch(url):
    try:
        return urllib.request.urlopen(url, timeout=10).read().decode()
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"

print("via proxy:", fetch(sys.argv[1]))
print("denied:", fetch(sys.argv[2]))
try:
    socket.create_connection(("127.0.0.1", int(sys.argv[3])), timeout=3)
    print("direct: connected")
except OSError as exc:
    print("direct: refused", type(exc).__name__)
"""


@pytest.fixture(autouse=True)
async def _stop_proxies() -> AsyncIterator[None]:
    yield
    await egress_proxy.close_proxies()


async def _serve_ok(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.readuntil(b"\r\n\r\n")
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
    await writer.drain()
    writer.close()


async def test_a_confined_command_reaches_only_the_proxy_and_the_hosts_it_allows(tmp_path: Path) -> None:
    allowed = await asyncio.start_server(_serve_ok, "127.0.0.1", 0)
    other   = socket.socket()
    other.bind(("127.0.0.1", 0))
    other.listen()
    allowed_port = allowed.sockets[0].getsockname()[1]
    other_port   = other.getsockname()[1]
    script       = tmp_path / "fetch.py"
    script.write_text(FETCH, encoding="utf-8")
    context = ToolContext(cwd=str(tmp_path))
    context.state["sandbox"] = SandboxPolicy("require", False, allowed_domains=("127.0.0.1",))
    try:
        command = f"{sys.executable} {script} http://127.0.0.1:{allowed_port}/ http://127.0.0.2:{allowed_port}/ {other_port}"
        result  = await BashTool().call(BashArgs(command), context)
    finally:
        allowed.close()
        other.close()
    assert "via proxy: hello" in result.content
    assert "denied: HTTP 403" in result.content
    assert "direct: refused" in result.content and "direct: connected" not in result.content
    assert f"[egress denied: 1 connection(s) to 127.0.0.2:{allowed_port}" in result.content
    assert classify_result(result.content, False) == [EGRESS_DENIED]


async def test_the_command_environment_names_the_proxy_and_holds_no_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPSTREAM_TOKEN", "must-not-reach-the-command")
    context = ToolContext(cwd=str(tmp_path))
    context.state["sandbox"] = SandboxPolicy(
        "require", False, allowed_domains=("api.example",), credentials=(("api.example", "UPSTREAM_TOKEN"),),
    )
    result = await BashTool().call(BashArgs("env"), context)
    lines  = dict(line.split("=", 1) for line in result.content.splitlines() if "=" in line)
    assert lines["HTTP_PROXY"].startswith("http://nerdvana:") and lines["HTTP_PROXY"] == lines["HTTPS_PROXY"] == lines["ALL_PROXY"]
    assert lines["NO_PROXY"] == ""
    assert "must-not-reach-the-command" not in result.content


async def test_a_program_that_ignores_the_proxy_variables_cannot_connect_directly(tmp_path: Path) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port    = server.getsockname()[1]
    context = ToolContext(cwd=str(tmp_path))
    context.state["sandbox"] = SandboxPolicy("require", False, allowed_domains=("127.0.0.1",))
    try:
        command = f"{sys.executable} -c \"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=3); print('connected')\" 2>&1"
        result  = await BashTool().call(BashArgs(command), context)
    finally:
        server.close()
    assert "connected" not in result.content.splitlines() and "Permission denied" in result.content
