"""Workspace-edit application, URI decoding, stdio serialisation and teardown.

No language server is spawned: the edit and URI helpers are called directly and
the JSON-RPC transport is driven through an in-memory ``StreamReader``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from nerdvana_cli.core.lsp_client import (
    _LIVE_PROCS,
    LspClient,
    _apply_workspace_edit,
    _reap_live_procs,
    _track_proc,
    _uri_to_path,
)


def _text_edit(
    start_line: int, start_char: int, end_line: int, end_char: int, new_text: str
) -> dict[str, Any]:
    return {
        "range": {
            "start": {"line": start_line, "character": start_char},
            "end":   {"line": end_line,   "character": end_char},
        },
        "newText": new_text,
    }


def _edit_for(path: Path, edits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "documentChanges": [
            {"textDocument": {"uri": path.as_uri(), "version": 1}, "edits": edits}
        ]
    }


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def _parse_frames(raw: bytes) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while True:
        idx = raw.find(b"Content-Length:")
        if idx == -1:
            return messages
        sep    = raw.index(b"\r\n\r\n", idx)
        length = int(raw[idx:sep].split(b":")[1].strip())
        start  = sep + 4
        messages.append(json.loads(raw[start:start + length]))
        raw = raw[start + length:]


class _FakeStdin:
    """Collects everything the client writes, mimicking a StreamWriter."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False

    def close(self) -> None:
        return None


class _FakeProc:
    """Stand-in for a language server process backed by an in-memory pipe."""

    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdin  = _FakeStdin()
        self.stdout = stdout
        self.returncode: int | None = None


# -- Edit application --


def test_multi_line_edit_keeps_the_tail_of_the_end_line(tmp_path: Path) -> None:
    """A range ending mid-line must keep what follows the end character."""
    target = tmp_path / "multi.txt"
    target.write_text("AAAA\nBBBB\nCCCC\n", encoding="utf-8")

    result = _apply_workspace_edit(
        _edit_for(target, [_text_edit(0, 1, 1, 2, "X")]),
        cwd=os.path.realpath(tmp_path),
    )

    assert target.read_text(encoding="utf-8") == "AXBB\nCCCC\n"
    assert result["skipped_files"] == []
    assert len(result["changed_files"]) == 1


def test_single_line_edit_still_replaces_exactly_the_range(tmp_path: Path) -> None:
    """The single-line path must remain a plain prefix + newText + suffix splice."""
    target = tmp_path / "single.txt"
    target.write_text("alpha beta\ngamma\n", encoding="utf-8")

    _apply_workspace_edit(
        _edit_for(target, [_text_edit(0, 0, 0, 5, "ALPHA")]),
        cwd=os.path.realpath(tmp_path),
    )

    assert target.read_text(encoding="utf-8") == "ALPHA beta\ngamma\n"


@pytest.mark.parametrize("name", ["my file.txt", "한글 파일.txt"])
def test_percent_encoded_uris_resolve_back_to_the_real_file(
    tmp_path: Path, name: str
) -> None:
    """as_uri() escapes spaces and non-ASCII names; the client must undo that."""
    target = tmp_path / name
    target.write_text("old\n", encoding="utf-8")
    uri = target.as_uri()

    assert _uri_to_path(uri) == str(target)

    result = _apply_workspace_edit(
        _edit_for(target, [_text_edit(0, 0, 0, 3, "new")]),
        cwd=os.path.realpath(tmp_path),
    )

    assert target.read_text(encoding="utf-8") == "new\n"
    assert result["changed_files"] == [str(target)]


def test_unreadable_target_is_reported_not_silently_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file that cannot be opened must leave a warning and a skipped entry."""
    missing = tmp_path / "gone.txt"

    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.core.lsp_client"):
        result = _apply_workspace_edit(
            _edit_for(missing, [_text_edit(0, 0, 0, 1, "x")]),
            cwd=os.path.realpath(tmp_path),
        )

    assert result["changed_files"] == []
    assert result["skipped_files"] == [str(missing)]
    assert any("gone.txt" in record.getMessage() for record in caplog.records)


# -- Transport serialisation --


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_reader_without_collision() -> None:
    """Two in-flight requests must not await the same StreamReader at once.

    Overlapping readline() calls raise RuntimeError from asyncio itself, and the
    response that arrives first here belongs to the second request, so each
    caller must still be handed its own reply.
    """
    reader = asyncio.StreamReader()
    proc   = _FakeProc(reader)
    client = LspClient()
    client._procs[".py"] = cast(Any, proc)

    async def feed() -> None:
        await asyncio.sleep(0.05)
        reader.feed_data(_frame({"jsonrpc": "2.0", "id": 2, "result": {"echo": 2}}))
        reader.feed_data(_frame({"jsonrpc": "2.0", "id": 1, "result": {"echo": 1}}))

    feeder = asyncio.create_task(feed())
    try:
        definition, references = await asyncio.wait_for(
            asyncio.gather(
                client._request(".py", "textDocument/definition", {"n": 1}),
                client._request(".py", "textDocument/references", {"n": 2}),
            ),
            timeout=5.0,
        )
    finally:
        await feeder

    sent      = _parse_frames(b"".join(proc.stdin.chunks))
    by_method = {m["method"]: m["id"] for m in sent}
    assert set(by_method) == {"textDocument/definition", "textDocument/references"}
    assert definition["echo"] == by_method["textDocument/definition"]
    assert references["echo"] == by_method["textDocument/references"]


# -- Teardown --


@pytest.mark.asyncio
async def test_close_reclaims_the_server_process() -> None:
    """close() must terminate the process and drop it from every registry."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _track_proc(proc)
    client = LspClient()
    client._procs[".py"] = proc
    assert proc.returncode is None

    await client.close()
    await asyncio.wait_for(proc.wait(), timeout=5.0)

    assert proc.returncode is not None
    assert client._procs == {}
    assert proc not in _LIVE_PROCS


@pytest.mark.asyncio
async def test_exit_hook_reclaims_a_process_nobody_closed() -> None:
    """The interpreter-exit net must kill servers whose owner never closed them."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _track_proc(proc)
    assert proc in _LIVE_PROCS

    _reap_live_procs()
    await asyncio.wait_for(proc.wait(), timeout=5.0)

    assert proc.returncode is not None
    assert proc not in _LIVE_PROCS
