"""Minimal LSP client using stdio JSON-RPC 2.0.

Spawns language server processes on demand and communicates via stdin/stdout.
Requires no external LSP library; pure Python + asyncio.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

# Map file extension -> preferred + fallback binary names
_EXT_SERVERS: dict[str, list[str]] = {
    ".py":   ["pyright", "pylsp"],
    ".ts":   ["typescript-language-server"],
    ".tsx":  ["typescript-language-server"],
    ".js":   ["typescript-language-server"],
    ".jsx":  ["typescript-language-server"],
    ".go":   ["gopls"],
    ".rs":   ["rust-analyzer"],
}


class LspError(Exception):
    """Raised when an LSP server interaction fails unrecoverably."""


class LspClient:
    """Manages per-extension language server processes.

    Processes are started lazily when a tool is first called for a given
    extension and are restarted once on crash before being disabled.
    """

    def __init__(self) -> None:
        self._procs:    dict[str, asyncio.subprocess.Process] = {}
        self._req_id:   int       = 0
        self._disabled: set[str]  = set()

    # -- Public API --

    def has_any_server(self) -> bool:
        """Return True if at least one language server binary is installed."""
        for binaries in _EXT_SERVERS.values():
            for b in binaries:
                if shutil.which(b):
                    return True
        return False

    def available_tools(self) -> list[Any]:
        """Return BaseTool instances for all 4 LSP operations."""
        from nerdvana_cli.tools.lsp import (
            LspDiagnosticsTool,
            LspFindReferencesTool,
            LspGotoDefinitionTool,
            LspRenameTool,
        )
        if not self.has_any_server():
            return []
        return [
            LspDiagnosticsTool(client=self),
            LspGotoDefinitionTool(client=self),
            LspFindReferencesTool(client=self),
            LspRenameTool(client=self),
        ]

    async def diagnostics(self, file_path: str) -> list[dict[str, Any]]:
        """Run textDocument/diagnostic and return a simplified list."""
        ext = Path(file_path).suffix
        uri = _path_to_uri(file_path)
        result = await self._request(
            ext,
            method="textDocument/diagnostic",
            params={"textDocument": {"uri": uri}},
        )
        raw = result.get("items") or result.get("diagnostics", [])
        return [_simplify_diag(d) for d in raw]

    async def goto_definition(
        self, file_path: str, line: int, symbol: str
    ) -> dict[str, Any] | None:
        """Return definition location or None if not found."""
        ext = Path(file_path).suffix
        uri = _path_to_uri(file_path)
        col = await self._find_symbol_col(file_path, line, symbol)
        result = await self._request(
            ext,
            method="textDocument/definition",
            params={
                "textDocument": {"uri": uri},
                "position":     {"line": line - 1, "character": col},
            },
        )
        if not result:
            return None
        loc = result[0] if isinstance(result, list) else result
        return {
            "file": _uri_to_path(loc["uri"]),
            "line": loc["range"]["start"]["line"] + 1,
            "col":  loc["range"]["start"]["character"],
        }

    async def find_references(
        self, file_path: str, line: int, symbol: str
    ) -> list[dict[str, Any]]:
        """Return all reference locations for symbol."""
        ext = Path(file_path).suffix
        uri = _path_to_uri(file_path)
        col = await self._find_symbol_col(file_path, line, symbol)
        result = await self._request(
            ext,
            method="textDocument/references",
            params={
                "textDocument": {"uri": uri},
                "position":     {"line": line - 1, "character": col},
                "context":      {"includeDeclaration": True},
            },
        )
        if not result:
            return []
        return [
            {
                "file": _uri_to_path(r["uri"]),
                "line": r["range"]["start"]["line"] + 1,
                "col":  r["range"]["start"]["character"],
            }
            for r in result
        ]

    async def rename(
        self, file_path: str, line: int, symbol: str, new_name: str
    ) -> dict[str, Any]:
        """Rename symbol across workspace; return changed files."""
        ext = Path(file_path).suffix
        uri = _path_to_uri(file_path)
        col = await self._find_symbol_col(file_path, line, symbol)
        result = await self._request(
            ext,
            method="textDocument/rename",
            params={
                "textDocument": {"uri": uri},
                "position":     {"line": line - 1, "character": col},
                "newName":      new_name,
            },
        )
        return _apply_workspace_edit(result)

    # -- Internal --

    async def _request(
        self, ext: str, method: str, params: dict[str, Any]
    ) -> Any:
        """Send a JSON-RPC request and await the response."""
        if ext in self._disabled:
            raise LspError(f"LSP server for {ext!r} is disabled (previous crash)")

        proc = await self._get_proc(ext)
        self._req_id += 1
        req_id = self._req_id

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        body = json.dumps(msg)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        proc.stdin.write((header + body).encode())
        await proc.stdin.drain()

        response = await self._read_response(proc, req_id)
        if "error" in response:
            raise LspError(f"LSP error: {response['error']}")
        return response.get("result")

    async def _read_response(
        self, proc: asyncio.subprocess.Process, req_id: int
    ) -> dict[str, Any]:
        """Read next LSP message from stdout, skipping notifications."""
        while True:
            header_line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
            if not header_line:
                raise LspError("Language server closed stdout unexpectedly")
            if not header_line.startswith(b"Content-Length:"):
                continue
            length = int(header_line.split(b":")[1].strip())
            await proc.stdout.readline()  # blank line
            raw = await asyncio.wait_for(proc.stdout.readexactly(length), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("method"):
                continue  # notification, skip
            if msg.get("id") == req_id:
                return msg

    async def _get_proc(self, ext: str) -> asyncio.subprocess.Process:
        """Return running process for ext, starting one if needed."""
        if ext in self._procs:
            proc = self._procs[ext]
            if proc.returncode is None:
                return proc
            del self._procs[ext]
        try:
            proc = await self._start_server(ext)
            self._procs[ext] = proc
            await self._initialize(proc)
            return proc
        except Exception as e:
            self._disabled.add(ext)
            raise LspError(f"Failed to start LSP server for {ext}: {e}") from e

    async def _start_server(self, ext: str) -> asyncio.subprocess.Process:
        binaries = _EXT_SERVERS.get(ext, [])
        for binary in binaries:
            if shutil.which(binary):
                args = [binary, "--stdio"]
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                return proc
        raise LspError(f"No LSP binary found for extension {ext!r}")

    async def _initialize(self, proc: asyncio.subprocess.Process) -> None:
        """Send LSP initialize + initialized handshake."""
        self._req_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "processId":    None,
                "rootUri":      None,
                "capabilities": {},
            },
        }
        body = json.dumps(req)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        proc.stdin.write((header + body).encode())
        await proc.stdin.drain()
        await self._read_response(proc, self._req_id)
        notif = json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        nh = f"Content-Length: {len(notif)}\r\n\r\n"
        proc.stdin.write((nh + notif).encode())
        await proc.stdin.drain()

    async def _find_symbol_col(
        self, file_path: str, line: int, symbol: str
    ) -> int:
        """Return column of first occurrence of symbol on the given line."""
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
            target = lines[line - 1] if line <= len(lines) else ""
            idx = target.find(symbol)
            return max(idx, 0)
        except OSError:
            return 0


# -- Helpers --


def _path_to_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def _uri_to_path(uri: str) -> str:
    from urllib.parse import urlparse
    return urlparse(uri).path


def _simplify_diag(d: dict[str, Any]) -> dict[str, Any]:
    severity_map = {1: "error", 2: "warning", 3: "information", 4: "hint"}
    return {
        "line":     d["range"]["start"]["line"] + 1,
        "col":      d["range"]["start"]["character"],
        "severity": severity_map.get(d.get("severity", 2), "warning"),
        "message":  d.get("message", ""),
    }


def _apply_workspace_edit(edit: dict[str, Any] | None) -> dict[str, Any]:
    """Apply a WorkspaceEdit in memory and return changed files."""
    if not edit:
        return {"changed_files": [], "diffs": []}
    changes = edit.get("changes") or {}
    changed: list[str] = []
    for uri, text_edits in changes.items():
        path = _uri_to_path(uri)
        try:
            with open(path, encoding="utf-8") as f:
                original = f.readlines()
        except OSError:
            continue
        for te in sorted(
            text_edits,
            key=lambda e: (e["range"]["start"]["line"], e["range"]["start"]["character"]),
            reverse=True,
        ):
            sl = te["range"]["start"]["line"]
            el = te["range"]["end"]["line"]
            sc = te["range"]["start"]["character"]
            ec = te["range"]["end"]["character"]
            new_text = te["newText"]
            if sl == el:
                line_text = original[sl]
                original[sl] = line_text[:sc] + new_text + line_text[ec:]
            else:
                first = original[sl][:sc] + new_text
                original = original[:sl] + [first] + original[el + 1:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(original)
        changed.append(path)
    return {"changed_files": changed, "diffs": []}
