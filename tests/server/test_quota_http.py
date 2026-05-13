"""HTTP-level smoke test — QuotaExceeded over Streamable-HTTP transport.

Boots NerdvanaMcpServer in transport="http" mode on an ephemeral port,
configures rpm=1 for a known tenant, issues two requests, and asserts the
second response carries a 429 with Retry-After.

Known limitation (mcp==1.27.0): the MCP lowlevel server catches all tool
exceptions (``server.py`` line ~583: ``except Exception as e: return
self._make_error_result(str(e))``) and converts them to an MCP error
result with HTTP 200.  ``QuotaExceeded`` is therefore serialised as
``{"isError":true}`` in the response body before it can reach the ASGI
middleware layer.  The second request receives HTTP 200 with ``isError:true``
instead of HTTP 429.

The test is marked ``xfail`` for this reason.  When a future ``mcp`` release
surfaces ``raise_exceptions=True`` in the Streamable-HTTP path this marker
should be removed and the assertion updated.

See ``docs/mcp-quota.md`` — "Known limitation" section.

작성자: 최진호
작성일: 2026-05-13
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from nerdvana_cli.server.acl import ACLManager
from nerdvana_cli.server.audit import AuditLogger
from nerdvana_cli.server.auth import AuthManager
from nerdvana_cli.server.mcp_server import NerdvanaMcpServer
from nerdvana_cli.server.quota import QuotaPolicy, QuotaPolicyResolver, QuotaStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


# ---------------------------------------------------------------------------
# Fixture: server on ephemeral port
# ---------------------------------------------------------------------------


@pytest.fixture
def http_server_ctx(tmp_path):
    """Start NerdvanaMcpServer(transport='http') in a background thread.

    Yields (base_url, bearer_token) after the server loop is running.
    Stops the server after the test completes.
    """
    pytest.importorskip("httpx")
    pytest.importorskip("uvicorn")

    port         = _find_free_port()
    bearer_token = "test-secret-12345"
    acl          = _make_permissive_acl(tmp_path)
    auth         = _make_auth_manager(tmp_path, bearer_token)
    audit        = AuditLogger(db_path=tmp_path / "audit.sqlite")
    audit.open()
    resolver = QuotaPolicyResolver(
        per_tenant={"test-tenant": QuotaPolicy(rpm=1)},
    )
    store  = QuotaStore()
    server = NerdvanaMcpServer(
        transport      = "http",
        host           = "127.0.0.1",
        port           = port,
        auth_manager   = auth,
        acl_manager    = acl,
        audit_logger   = audit,
        quota_resolver = resolver,
        quota_store    = store,
        project_path   = tmp_path,
    )

    loop_holder: list[asyncio.AbstractEventLoop] = []
    started      = threading.Event()
    stopped      = threading.Event()

    def _run() -> None:
        loop = asyncio.new_event_loop()
        loop_holder.append(loop)

        async def _serve() -> None:
            # Signal ready after the first iteration so httpx can connect.
            loop.call_soon(started.set)
            await server.run()

        try:
            loop.run_until_complete(_serve())
        except Exception:
            pass
        finally:
            loop.close()
            stopped.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    started.wait(timeout=5)

    # Give uvicorn a moment to bind the port.
    import time
    time.sleep(0.3)

    yield f"http://127.0.0.1:{port}", bearer_token

    # Teardown: cancel all tasks in the loop so uvicorn shuts down.
    if loop_holder:
        loop = loop_holder[0]
        for task in asyncio.all_tasks(loop):
            loop.call_soon_threadsafe(task.cancel)
    thread.join(timeout=3)
    audit.close()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "mcp==1.27.0 converts QuotaExceeded to isError:true/HTTP-200 inside "
        "the lowlevel call_tool handler (server.py ~line 583: "
        "'except Exception as e: return self._make_error_result(str(e))'). "
        "The second request receives 200 instead of 429. "
        "Remove this marker when mcp exposes raise_exceptions=True in the "
        "Streamable-HTTP path. See docs/mcp-quota.md#known-limitation."
    ),
    strict=False,
)
def test_http_quota_rpm1_second_request_is_429(http_server_ctx) -> None:
    """Second request from same tenant must yield 429 with Retry-After when rpm=1.

    If mcp swallows QuotaExceeded (current behaviour with 1.27.0) the second
    request returns HTTP 200 with ``isError:true`` in the body — the xfail
    marker documents this and allows CI to pass while tracking the issue.
    """
    import httpx

    base_url, token = http_server_ctx
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "tools/call",
        "params":  {"name": "ListMemories", "arguments": {"topic": ""}},
    }

    with httpx.Client(timeout=5) as client:
        r1 = client.post(f"{base_url}/mcp", json=payload, headers=headers)
        assert r1.status_code == 200, f"First request failed: {r1.status_code} {r1.text}"

        r2 = client.post(f"{base_url}/mcp", json=payload, headers=headers)
        assert r2.status_code == 429, (
            f"Expected 429 for rate-limited request but got {r2.status_code}: {r2.text}"
        )
        assert "Retry-After" in r2.headers, "Retry-After header missing from 429 response"
        assert int(r2.headers["Retry-After"]) >= 1
