"""Tests for LSP client hardening (Phase 0B).

Covers: rootUri, capabilities, didOpen, documentChanges, shutdown sequence,
per-server init timeouts.  All tests use mocks — no real language server is
spawned.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from nerdvana_cli.core.lsp_client import (
    DEFAULT_LSP_INIT_TIMEOUT,
    LSP_INIT_TIMEOUTS,
    LspClient,
    LspError,
    _apply_workspace_edit,
    _init_timeout_for,
)


# ---------------------------------------------------------------------------
# 1. rootUri is a valid file:// URI derived from project_root
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_sends_file_uri_root():
    """_initialize() must set rootUri to a file:// URI matching project_root."""
    client = LspClient(project_root="/tmp/my_project")

    sent_params: dict = {}

    async def fake_read_response(proc, req_id, timeout=30.0):
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.is_closing.return_value = False
    written: list[bytes] = []
    proc.stdin.write.side_effect = lambda data: written.append(data)
    proc.stdin.drain = AsyncMock()

    with patch.object(client, "_read_response", side_effect=fake_read_response):
        await client._initialize(proc, ".py")

    # Parse the first message (initialize request) from the written bytes.
    # _initialize writes two messages; use Content-Length to read only the first.
    raw = b"".join(written)
    sep = raw.index(b"\r\n\r\n")
    length = int(raw[raw.index(b"Content-Length:"):sep].split(b":")[1].strip())
    msg = json.loads(raw[sep + 4: sep + 4 + length])
    assert msg["method"] == "initialize"
    root_uri = msg["params"]["rootUri"]
    assert root_uri.startswith("file://"), f"rootUri must be file:// URI, got {root_uri!r}"
    assert "my_project" in root_uri


# ---------------------------------------------------------------------------
# 2. capabilities are sent with expected keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_sends_required_capabilities():
    """initialize request must include textDocument and workspace capabilities."""
    client = LspClient(project_root="/tmp/proj")

    async def fake_read_response(proc, req_id, timeout=30.0):
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.is_closing.return_value = False
    written: list[bytes] = []
    proc.stdin.write.side_effect = lambda data: written.append(data)
    proc.stdin.drain = AsyncMock()

    with patch.object(client, "_read_response", side_effect=fake_read_response):
        await client._initialize(proc, ".py")

    raw = b"".join(written)
    sep = raw.index(b"\r\n\r\n")
    length = int(raw[raw.index(b"Content-Length:"):sep].split(b":")[1].strip())
    msg = json.loads(raw[sep + 4: sep + 4 + length])
    caps = msg["params"]["capabilities"]

    assert "textDocument" in caps
    assert "synchronization" in caps["textDocument"]
    assert caps["textDocument"]["synchronization"]["didSave"] is True
    assert "rename" in caps["textDocument"]
    assert caps["textDocument"]["rename"]["prepareSupport"] is True
    assert caps["workspace"]["workspaceEdit"]["documentChanges"] is True


# ---------------------------------------------------------------------------
# 3. _apply_workspace_edit prefers documentChanges over changes
# ---------------------------------------------------------------------------


def test_apply_workspace_edit_prefers_document_changes(tmp_path: Path):
    """documentChanges takes priority over the legacy changes map."""
    target = tmp_path / "hello.py"
    target.write_text("hello world\n", encoding="utf-8")
    uri = target.as_uri()

    edit = {
        # documentChanges contains the real edit
        "documentChanges": [
            {
                "textDocument": {"uri": uri, "version": 1},
                "edits": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end":   {"line": 0, "character": 5},
                        },
                        "newText": "goodbye",
                    }
                ],
            }
        ],
        # legacy changes map has a different (wrong) edit — must be ignored
        "changes": {
            uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end":   {"line": 0, "character": 5},
                    },
                    "newText": "WRONG",
                }
            ]
        },
    }

    result = _apply_workspace_edit(edit, cwd=str(tmp_path))
    assert str(target) in result["changed_files"]
    assert target.read_text(encoding="utf-8") == "goodbye world\n"


# ---------------------------------------------------------------------------
# 4. shutdown sequence: shutdown → exit → wait → kill on timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_server_sequence():
    """shutdown_server must send shutdown, then exit, then kill on timeout."""
    client = LspClient()

    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdin.is_closing.return_value = False
    written: list[bytes] = []
    proc.stdin.write.side_effect = lambda data: written.append(data)
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.kill = MagicMock()

    # _read_response returns a valid shutdown response immediately
    async def fake_read_response(p, req_id, timeout=30.0):
        return {"jsonrpc": "2.0", "id": req_id, "result": None}

    # proc.wait() times out, triggering SIGKILL
    async def fake_wait():
        await asyncio.sleep(10)

    proc.wait = fake_wait
    client._procs[".py"] = proc

    with patch.object(client, "_read_response", side_effect=fake_read_response):
        await client.shutdown_server(".py")

    # Collect all JSON messages sent over stdin
    raw = b"".join(written)
    messages = []
    while True:
        idx = raw.find(b"Content-Length:")
        if idx == -1:
            break
        sep = raw.index(b"\r\n\r\n", idx)
        length = int(raw[idx:sep].split(b":")[1].strip())
        body_start = sep + 4
        messages.append(json.loads(raw[body_start: body_start + length]))
        raw = raw[body_start + length:]

    methods = [m.get("method") for m in messages]
    assert "shutdown" in methods, "shutdown request not sent"
    assert "exit" in methods, "exit notification not sent"

    # Process timed out → kill() must have been called
    proc.kill.assert_called_once()
    # The proc must have been removed from _procs
    assert ".py" not in client._procs


# ---------------------------------------------------------------------------
# 5. per-server init timeout table is applied correctly
# ---------------------------------------------------------------------------


def test_init_timeout_table():
    """LSP_INIT_TIMEOUTS must cover expected servers with correct values."""
    assert LSP_INIT_TIMEOUTS["rust-analyzer"] == 30.0
    assert LSP_INIT_TIMEOUTS["jdtls"] == 45.0
    assert LSP_INIT_TIMEOUTS["clangd"] == 15.0
    assert LSP_INIT_TIMEOUTS["pyright"] == 10.0
    assert LSP_INIT_TIMEOUTS["gopls"] == 10.0

    # Unknown server falls back to default
    assert _init_timeout_for("unknown-server") == DEFAULT_LSP_INIT_TIMEOUT
    assert _init_timeout_for("rust-analyzer") == 30.0


# ---------------------------------------------------------------------------
# 6. _ensure_open sends didOpen exactly once and tracks version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_open_sends_did_open_once(tmp_path: Path):
    """_ensure_open must send didOpen on first access and skip on repeat."""
    src = tmp_path / "mod.py"
    src.write_text("x = 1\n", encoding="utf-8")

    client = LspClient(project_root=str(tmp_path))

    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    written: list[bytes] = []
    proc.stdin.write.side_effect = lambda data: written.append(data)
    proc.stdin.drain = AsyncMock()

    with patch.object(client, "_get_proc", new_callable=AsyncMock, return_value=proc):
        await client._ensure_open(".py", str(src))
        await client._ensure_open(".py", str(src))  # second call must be no-op

    # Only one message should have been written
    raw = b"".join(written)
    sep = raw.index(b"\r\n\r\n")
    length = int(raw[raw.index(b"Content-Length:"):sep].split(b":")[1].strip())
    msg = json.loads(raw[sep + 4: sep + 4 + length])

    assert msg["method"] == "textDocument/didOpen"
    assert msg["params"]["textDocument"]["languageId"] == "python"
    assert msg["params"]["textDocument"]["version"] == 1

    # Remaining bytes should be empty (only one message)
    assert len(raw) == sep + 4 + length

    # abs_path must be tracked
    assert str(src.resolve()) in client._open_files


# ---------------------------------------------------------------------------
# 7. _apply_workspace_edit falls back to legacy changes map when no documentChanges
# ---------------------------------------------------------------------------


def test_apply_workspace_edit_legacy_changes_fallback(tmp_path: Path):
    """When documentChanges is absent, the legacy changes map is applied."""
    target = tmp_path / "old.py"
    target.write_text("foo bar\n", encoding="utf-8")
    uri = target.as_uri()

    edit = {
        "changes": {
            uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end":   {"line": 0, "character": 7},
                    },
                    "newText": "baz",
                }
            ]
        }
    }

    result = _apply_workspace_edit(edit, cwd=str(tmp_path))
    assert str(target) in result["changed_files"]
    assert target.read_text(encoding="utf-8") == "foo baz\n"


# ---------------------------------------------------------------------------
# 8. LspClient default project_root is cwd
# ---------------------------------------------------------------------------


def test_default_project_root_is_cwd():
    """LspClient() without project_root must use os.getcwd()."""
    with patch("os.getcwd", return_value="/some/dir"):
        client = LspClient()
    assert client._project_root == "/some/dir"
