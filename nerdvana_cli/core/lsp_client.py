"""Minimal LSP client using stdio JSON-RPC 2.0.

Spawns language server processes on demand and communicates via stdin/stdout.
Requires no external LSP library; pure Python + asyncio.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from nerdvana_cli.utils.path import safe_open_fd

_EXT_SERVERS: dict[str, list[str]] = {
    ".py":  ["pyright", "pylsp"],
    ".ts":  ["typescript-language-server"],
    ".tsx": ["typescript-language-server"],
    ".js":  ["typescript-language-server"],
    ".jsx": ["typescript-language-server"],
    ".go":  ["gopls"],
    ".rs":  ["rust-analyzer"],
}

# Per-server initialize timeouts (seconds); keyed by binary name.
LSP_INIT_TIMEOUTS: dict[str, float] = {
    "pyright":                    10.0,
    "pyright-langserver":         10.0,
    "typescript-language-server": 10.0,
    "gopls":                      10.0,
    "rust-analyzer":              30.0,
    "jdtls":                      45.0,
    "clangd":                     15.0,
}
DEFAULT_LSP_INIT_TIMEOUT: float = 15.0
DEFAULT_REQUEST_TIMEOUT:  float = 30.0

# Minimal capabilities: diagnostics, symbols, references, definition, rename.
_CAPABILITIES: dict[str, Any] = {
    "textDocument": {
        "synchronization":  {"didSave": True,  "dynamicRegistration": False},
        "documentSymbol":   {"hierarchicalDocumentSymbolSupport": True},
        "references":       {"dynamicRegistration": False},
        "definition":       {"dynamicRegistration": False},
        "rename":           {"prepareSupport": True, "dynamicRegistration": False},
    },
    "workspace": {"workspaceEdit": {"documentChanges": True}},
}


class LspError(Exception):
    """Raised when an LSP server interaction fails unrecoverably."""


class LspClient:
    """Manages per-extension language server processes.

    Lazily started; restarted once on crash then disabled.
    ``project_root`` is forwarded as ``rootUri`` in the LSP initialize
    handshake; defaults to cwd at construction time.
    """

    def __init__(self, project_root: str | None = None) -> None:
        self._project_root: str       = project_root or os.getcwd()
        self._procs:        dict[str, asyncio.subprocess.Process] = {}
        self._req_id:       int       = 0
        self._disabled:     set[str]  = set()
        self._open_files:   dict[str, int] = {}  # abs_path -> version

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
        await self._ensure_open(ext, file_path)
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
        await self._ensure_open(ext, file_path)
        col = self._find_symbol_col(file_path, line, symbol)
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
        await self._ensure_open(ext, file_path)
        col = self._find_symbol_col(file_path, line, symbol)
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
        await self._ensure_open(ext, file_path)
        col = self._find_symbol_col(file_path, line, symbol)
        result = await self._request(
            ext,
            method="textDocument/rename",
            params={
                "textDocument": {"uri": uri},
                "position":     {"line": line - 1, "character": col},
                "newName":      new_name,
            },
        )
        return _apply_workspace_edit(result, cwd=self._project_root)

    async def shutdown_server(self, ext: str) -> None:
        """shutdown request → exit notification → 2 s grace → SIGKILL."""
        proc = self._procs.pop(ext, None)
        if proc is None or proc.returncode is not None:
            return
        if proc.stdin is None or proc.stdin.is_closing():
            proc.kill()
            return

        try:  # 1. shutdown request
            self._req_id += 1
            rid = self._req_id
            m   = json.dumps({"jsonrpc": "2.0", "id": rid, "method": "shutdown", "params": None})
            proc.stdin.write((f"Content-Length: {len(m)}\r\n\r\n" + m).encode())
            await proc.stdin.drain()
            await asyncio.wait_for(self._read_response(proc, rid, timeout=5.0), timeout=5.0)
        except Exception:
            pass
        try:  # 2. exit notification
            e = json.dumps({"jsonrpc": "2.0", "method": "exit", "params": None})
            proc.stdin.write((f"Content-Length: {len(e)}\r\n\r\n" + e).encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            pass
        try:  # 3. 2 s grace, then SIGKILL
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:
            proc.kill()

    async def close(self) -> None:
        """Shut down all running language server processes."""
        for ext in list(self._procs.keys()):
            await self.shutdown_server(ext)

    async def __aenter__(self) -> LspClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # -- Internal --

    async def _ensure_open(self, ext: str, file_path: str) -> None:
        """Send textDocument/didOpen once; subsequent calls are no-ops."""
        abs_path = str(Path(file_path).resolve())
        if abs_path in self._open_files:
            return
        try:
            with open(abs_path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return

        lang_id = _ext_to_language_id(Path(file_path).suffix)
        uri     = Path(abs_path).as_uri()
        version = 1
        self._open_files[abs_path] = version

        notif = json.dumps({
            "jsonrpc": "2.0",
            "method":  "textDocument/didOpen",
            "params":  {
                "textDocument": {
                    "uri":        uri,
                    "languageId": lang_id,
                    "version":    version,
                    "text":       text,
                }
            },
        })
        nh = f"Content-Length: {len(notif)}\r\n\r\n"
        try:
            proc = await self._get_proc(ext)
            assert proc.stdin is not None
            proc.stdin.write((nh + notif).encode())
            await proc.stdin.drain()
        except LspError:
            self._open_files.pop(abs_path, None)

    async def _request(
        self, ext: str, method: str, params: dict[str, Any],
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> Any:
        """Send a JSON-RPC request and await the response."""
        if ext in self._disabled:
            raise LspError(f"LSP server for {ext!r} is disabled (previous crash)")

        proc = await self._get_proc(ext)
        self._req_id += 1
        req_id = self._req_id

        msg  = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        body = json.dumps(msg)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        assert proc.stdin is not None
        proc.stdin.write((header + body).encode())
        await proc.stdin.drain()

        response = await self._read_response(proc, req_id, timeout=timeout)
        if "error" in response:
            raise LspError(f"LSP error: {response['error']}")
        return response.get("result")

    async def _read_response(
        self,
        proc:    asyncio.subprocess.Process,
        req_id:  int,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Read next LSP message from stdout, skipping notifications."""
        assert proc.stdout is not None
        while True:
            header_line = await asyncio.wait_for(
                proc.stdout.readline(), timeout=timeout
            )
            if not header_line:
                raise LspError("Language server closed stdout unexpectedly")
            if not header_line.startswith(b"Content-Length:"):
                continue
            length = int(header_line.split(b":")[1].strip())
            await proc.stdout.readline()  # blank line
            raw = await asyncio.wait_for(
                proc.stdout.readexactly(length), timeout=timeout
            )
            msg = json.loads(raw)
            if msg.get("method"):
                continue  # notification, skip
            if msg.get("id") == req_id:
                return dict(msg)

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
            await self._initialize(proc, ext)
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

    async def _initialize(self, proc: asyncio.subprocess.Process, ext: str) -> None:
        """Send LSP initialize + initialized handshake."""
        binary   = (_EXT_SERVERS.get(ext) or [""])[0]
        timeout  = _init_timeout_for(binary)
        root_uri = Path(self._project_root).resolve().as_uri()

        self._req_id += 1
        req = {
            "jsonrpc": "2.0",
            "id":      self._req_id,
            "method":  "initialize",
            "params":  {
                "processId":    os.getpid(),
                "rootUri":      root_uri,
                "capabilities": _CAPABILITIES,
            },
        }
        body   = json.dumps(req)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        assert proc.stdin is not None
        proc.stdin.write((header + body).encode())
        await proc.stdin.drain()
        await self._read_response(proc, self._req_id, timeout=timeout)

        notif = json.dumps(
            {"jsonrpc": "2.0", "method": "initialized", "params": {}}
        )
        nh = f"Content-Length: {len(notif)}\r\n\r\n"
        proc.stdin.write((nh + notif).encode())
        await proc.stdin.drain()

    def _find_symbol_col(self, file_path: str, line: int, symbol: str) -> int:
        """Return column of first occurrence of symbol on the given line."""
        try:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
            target = lines[line - 1] if line <= len(lines) else ""
            return max(target.find(symbol), 0)
        except OSError:
            return 0


# -- Helpers --


def _init_timeout_for(binary: str) -> float:
    return LSP_INIT_TIMEOUTS.get(binary, DEFAULT_LSP_INIT_TIMEOUT)


def _path_to_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def _uri_to_path(uri: str) -> str:
    from urllib.parse import urlparse
    return urlparse(uri).path


_LANG_IDS: dict[str, str] = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact", ".go": "go", ".rs": "rust",
}


def _ext_to_language_id(suffix: str) -> str:
    return _LANG_IDS.get(suffix, "plaintext")


def _simplify_diag(d: dict[str, Any]) -> dict[str, Any]:
    severity_map = {1: "error", 2: "warning", 3: "information", 4: "hint"}
    return {
        "line":     d["range"]["start"]["line"] + 1,
        "col":      d["range"]["start"]["character"],
        "severity": severity_map.get(d.get("severity", 2), "warning"),
        "message":  d.get("message", ""),
    }


def _apply_workspace_edit(
    edit: dict[str, Any] | None,
    cwd:  str | None = None,
) -> dict[str, Any]:
    """Apply WorkspaceEdit; prefers documentChanges over legacy changes map."""
    if not edit:
        return {"changed_files": [], "diffs": []}

    edit_pairs: list[tuple[str, list[dict[str, Any]]]] = []

    document_changes = edit.get("documentChanges")
    if document_changes:
        for entry in document_changes:
            uri        = entry["textDocument"]["uri"]
            text_edits = entry.get("edits", [])
            edit_pairs.append((uri, text_edits))
    else:
        for uri, text_edits in (edit.get("changes") or {}).items():
            edit_pairs.append((uri, text_edits))

    changed: list[str] = []
    for uri, text_edits in edit_pairs:
        path = _uri_to_path(uri)
        try:
            with open(path, encoding="utf-8") as f:
                original = f.readlines()
        except OSError:
            continue

        for te in sorted(
            text_edits,
            key=lambda e: (
                e["range"]["start"]["line"],
                e["range"]["start"]["character"],
            ),
            reverse=True,
        ):
            sl       = te["range"]["start"]["line"]
            el       = te["range"]["end"]["line"]
            sc       = te["range"]["start"]["character"]
            ec       = te["range"]["end"]["character"]
            new_text = te["newText"]
            if sl == el:
                line_text    = original[sl]
                original[sl] = line_text[:sc] + new_text + line_text[ec:]
            else:
                first    = original[sl][:sc] + new_text
                original = original[:sl] + [first] + original[el + 1:]

        content = "".join(original).encode("utf-8")
        _write_file(path, content, cwd=cwd)
        changed.append(path)

    return {"changed_files": changed, "diffs": []}


def _write_file(path: str, content: bytes, cwd: str | None) -> None:
    """Write via safe_open_fd when path is inside cwd; plain open otherwise."""
    if cwd:
        try:
            abs_path = os.path.realpath(path)
            abs_cwd  = os.path.realpath(cwd)
            if abs_path.startswith(abs_cwd + os.sep) or abs_path == abs_cwd:
                rel = os.path.relpath(abs_path, abs_cwd)
                fd  = safe_open_fd(
                    rel, abs_cwd, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                )
                with os.fdopen(fd, "wb") as fh:
                    fh.write(content)
                return
        except (PermissionError, OSError):
            pass

    with open(path, "wb") as fh:
        fh.write(content)
