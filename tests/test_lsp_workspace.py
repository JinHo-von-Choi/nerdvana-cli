"""The LSP client shows the server the workspace before a cross-file question.

A fake server on in-memory pipes records every message the client sends, so these tests pin the request
sequence (initialize with the workspace folder, the documents opened, then the question) without a real
language server. The real one is exercised in ``tests/lsp/test_references_integration.py``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nerdvana_cli.core import lsp_client
from nerdvana_cli.core.lsp_client import LspClient, LspError
from nerdvana_cli.core.lsp_workspace import NoticedList, mentioning_files, notice_of
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.lsp import LspFindReferencesTool, LspRenameTool
from nerdvana_cli.tools.symbol_tools import FindReferencingSymbolsArgs, FindReferencingSymbolsTool

Answer = Callable[[dict[str, Any]], Any]


class _FakeServer:
    """A language server on in-memory pipes: it keeps every message it receives and answers requests."""

    def __init__(self, answers: dict[str, Answer] | None = None) -> None:
        self.messages: list[dict[str, Any]] = []
        self.stdout     = asyncio.StreamReader()
        self.stdin      = self
        self.returncode = None
        self._answers   = answers or {}

    def write(self, data: bytes) -> None:
        body    = json.loads(data.decode().split("\r\n\r\n", 1)[1])
        self.messages.append(body)
        if "id" not in body:
            return
        method = body["method"]
        if method == "initialize":
            result: Any = {"capabilities": {}}
        elif method == "shutdown":
            result = None
        elif method in self._answers:
            result = self._answers[method](body["params"])
        else:
            return
        reply = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
        self.stdout.feed_data(f"Content-Length: {len(reply)}\r\n\r\n{reply}".encode())

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False

    def close(self) -> None:
        return None

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return 0

    def methods(self) -> list[str]:
        return [m["method"] for m in self.messages]

    def opened(self) -> list[str]:
        return [Path(m["params"]["textDocument"]["uri"]).name for m in self.messages if m["method"] == "textDocument/didOpen"]


def _client(root: Path, server: _FakeServer) -> LspClient:
    client = LspClient(project_root=str(root))

    async def _start(ext: str) -> _FakeServer:
        client._binaries[ext] = "pyright-langserver"
        return server

    client._start_server = _start  # type: ignore[method-assign]
    return client


def _project(root: Path) -> Path:
    """A package whose ``target`` is used by ``b.py`` and ``c.py`` but not by ``d.py``."""
    (root / "pkg").mkdir()
    (root / "pkg" / "a.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("from pkg.a import target\n\ntarget()\n", encoding="utf-8")
    (root / "c.py").write_text("import pkg.a\n\npkg.a.target()\n", encoding="utf-8")
    (root / "d.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    return root / "pkg" / "a.py"


def _locations(*paths: Path) -> Answer:
    return lambda _params: [
        {"uri": path.resolve().as_uri(), "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 10}}}
        for path in paths
    ]


# -- Which files are shown --


def test_mentioning_files_matches_whole_words_in_files_of_the_suffix(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    (tmp_path / "e.py").write_text("targets = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("target\n", encoding="utf-8")

    found = mentioning_files(str(tmp_path), ".py", "target", str(origin))

    assert sorted(Path(p).name for p in found.files) == ["a.py", "b.py", "c.py"]
    assert found.omitted == 0


def test_mentioning_files_skips_hidden_dependency_and_oversized_files(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    for skipped in (".venv", "node_modules", "__pycache__", "site-packages"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "x.py").write_text("target()\n", encoding="utf-8")
    (tmp_path / "big.py").write_text("target\n" + "#" * 1_100_000, encoding="utf-8")

    found = mentioning_files(str(tmp_path), ".py", "target", str(origin))

    assert sorted(Path(p).name for p in found.files) == ["a.py", "b.py", "c.py"]


def test_mentioning_files_puts_the_files_nearest_to_the_origin_first_and_counts_the_rest(tmp_path: Path) -> None:
    origin = _project(tmp_path)

    found = mentioning_files(str(tmp_path), ".py", "target", str(origin), limit=2)

    assert [Path(p).name for p in found.files] == ["a.py", "b.py"]
    assert found.omitted == 1


def test_mentioning_files_treats_an_unreadable_file_as_not_mentioning(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    (tmp_path / "binary.py").write_bytes(b"\xff\xfe target \x00")

    found = mentioning_files(str(tmp_path), ".py", "target", str(origin))

    assert "binary.py" not in [Path(p).name for p in found.files]


# -- The request sequence --


async def test_initialize_names_the_workspace_folder(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    server = _FakeServer({"textDocument/references": _locations(origin)})
    client = _client(tmp_path, server)

    await client.find_references(str(origin), 1, "target")

    init = server.messages[0]["params"]
    uri  = tmp_path.resolve().as_uri()
    assert server.messages[0]["method"] == "initialize"
    assert init["rootUri"] == uri
    assert init["workspaceFolders"] == [{"uri": uri, "name": tmp_path.name}]
    assert init["capabilities"]["workspace"]["workspaceFolders"] is True


async def test_references_are_requested_after_every_file_mentioning_the_symbol_is_opened(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    server = _FakeServer({"textDocument/references": _locations(origin, tmp_path / "pkg" / "b.py", tmp_path / "c.py")})
    client = _client(tmp_path, server)

    refs = await client.find_references(str(origin), 1, "target")

    methods = server.methods()
    assert methods[:2] == ["initialize", "initialized"]
    assert sorted(server.opened()) == ["a.py", "b.py", "c.py"]
    assert methods.index("textDocument/references") > max(i for i, m in enumerate(methods) if m == "textDocument/didOpen")
    assert methods.count("textDocument/references") == 1
    assert sorted(Path(r["file"]).name for r in refs) == ["a.py", "b.py", "c.py"]
    assert notice_of(refs) == ""


async def test_a_second_search_resends_only_what_changed_and_closes_what_vanished(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    server = _FakeServer({"textDocument/references": _locations(origin)})
    client = _client(tmp_path, server)
    await client.find_references(str(origin), 1, "target")

    (tmp_path / "c.py").write_text("import pkg.a\n\npkg.a.target()\n# edited\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").unlink()
    server.messages.clear()
    await client.find_references(str(origin), 1, "target")

    sent = [(m["method"], Path(m["params"]["textDocument"]["uri"]).name) for m in server.messages if m["method"].startswith("textDocument/did")]
    assert sorted(sent) == [("textDocument/didChange", "c.py"), ("textDocument/didClose", "b.py")]


async def test_files_over_the_limit_are_reported_in_a_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    origin = _project(tmp_path)
    server = _FakeServer({"textDocument/references": _locations(origin)})
    client = _client(tmp_path, server)
    monkeypatch.setattr(lsp_client, "MAX_REFERENCE_FILES", 2)

    refs = await client.find_references(str(origin), 1, "target")

    assert sorted(server.opened()) == ["a.py", "b.py"]
    assert "1 more files mention 'target'" in notice_of(refs)
    assert "may be missing" in notice_of(refs)


async def test_rename_opens_the_same_files_and_passes_the_notice_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    origin = _project(tmp_path)
    server = _FakeServer({"textDocument/rename": lambda _params: None})
    client = _client(tmp_path, server)
    monkeypatch.setattr(lsp_client, "MAX_REFERENCE_FILES", 2)

    result = await client.rename(str(origin), 1, "target", "goal")

    assert sorted(server.opened()) == ["a.py", "b.py"]
    assert result["changed_files"] == []
    assert "1 more files mention 'target'" in result["notice"]


async def test_a_server_that_does_not_answer_in_time_gives_a_clear_error(tmp_path: Path) -> None:
    origin = _project(tmp_path)
    client = _client(tmp_path, _FakeServer())

    with pytest.raises(LspError) as raised:
        await client._request(".py", "textDocument/references", {"textDocument": {"uri": origin.as_uri()}}, timeout=0.05)

    assert "textDocument/references got no answer within 0.05s" in str(raised.value)
    assert "indexing" in str(raised.value)


# -- What the tools say --


async def test_find_referencing_symbols_tool_shows_the_notice() -> None:
    retriever = MagicMock()
    retriever.find = AsyncMock(return_value=[MagicMock()])
    retriever.find_references = AsyncMock(return_value=NoticedList([MagicMock(file_path="/p/a.py", line=3, character=4)], "result may be partial"))
    tool = FindReferencingSymbolsTool(retriever=retriever)

    result = await tool.call(FindReferencingSymbolsArgs(name_path="f", relative_path="a.py"), ToolContext(cwd="/p"))

    payload = json.loads(result.content)
    assert payload["notice"] == "result may be partial"
    assert payload["references"] == [{"file": "/p/a.py", "line": 3, "character": 4}]


async def test_find_referencing_symbols_tool_has_no_notice_key_for_a_complete_result() -> None:
    retriever = MagicMock()
    retriever.find = AsyncMock(return_value=[MagicMock()])
    retriever.find_references = AsyncMock(return_value=[MagicMock(file_path="/p/a.py", line=3, character=4)])
    tool = FindReferencingSymbolsTool(retriever=retriever)

    result = await tool.call(FindReferencingSymbolsArgs(name_path="f", relative_path="a.py"), ToolContext(cwd="/p"))

    assert "notice" not in json.loads(result.content)


async def test_lsp_find_references_and_rename_tools_show_the_notice() -> None:
    client = MagicMock()
    client.find_references = AsyncMock(return_value=NoticedList([{"file": "/p/a.py", "line": 3, "col": 4}], "partial"))
    client.rename = AsyncMock(return_value={"changed_files": ["/p/a.py"], "notice": "partial"})
    ctx = ToolContext(cwd="/p")

    refs   = LspFindReferencesTool(client=client)
    rename = LspRenameTool(client=client)

    found   = await refs.call(refs.args_class(file_path="/p/a.py", line=1, symbol="f"), ctx, None)
    renamed = await rename.call(rename.args_class(file_path="/p/a.py", line=1, symbol="f", new_name="g"), ctx, None)

    assert found.content.splitlines() == ["/p/a.py:3:4", "partial"]
    assert renamed.content.endswith("/p/a.py\npartial")
