"""Minimal LSP client using stdio JSON-RPC 2.0.

Spawns language server processes on demand and communicates via stdin/stdout.
Requires no external LSP library; pure Python + asyncio.
"""
from __future__ import annotations

import asyncio
import atexit
import contextlib
import hashlib
import json
import logging
import os
import shutil
import signal
from pathlib import Path
from typing import Any

from nerdvana_cli.codeintel.lsp_protocol import language_id_for, park_response, simplify_diagnostic, write_file
from nerdvana_cli.codeintel.lsp_workspace import MAX_REFERENCE_FILES, NoticedList, mentioning_files

logger = logging.getLogger(__name__)

_EXT_SERVERS: dict[str, list[str]] = {
    ".py":  ["pyright-langserver", "pylsp"],
    ".ts":  ["typescript-language-server"],
    ".tsx": ["typescript-language-server"],
    ".js":  ["typescript-language-server"],
    ".jsx": ["typescript-language-server"],
    ".go":  ["gopls"],
    ".rs":  ["rust-analyzer"],
}

# Arguments that put each server into stdio LSP mode. Servers not listed here
# (pylsp, gopls, rust-analyzer) speak LSP over stdio when run without arguments.
_SERVER_ARGS: dict[str, list[str]] = {
    "pyright-langserver":         ["--stdio"],
    "typescript-language-server": ["--stdio"],
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
    "workspace": {"workspaceEdit": {"documentChanges": True}, "workspaceFolders": True},
}


_KILL_SIGNAL: int = int(getattr(signal, "SIGKILL", signal.SIGTERM))

# Every spawned language server is tracked here so interpreter exit can reclaim
# the ones whose owner never called close().
_LIVE_PROCS: set[asyncio.subprocess.Process] = set()
_EXIT_HOOK_INSTALLED: bool = False


def _reap_live_procs() -> None:
    """Kill every language server still running at interpreter exit."""
    while _LIVE_PROCS:
        proc = _LIVE_PROCS.pop()
        if proc.returncode is not None:
            continue
        with contextlib.suppress(OSError):
            os.kill(proc.pid, _KILL_SIGNAL)


def _track_proc(proc: asyncio.subprocess.Process) -> None:
    """Register *proc* for exit-time reclamation, installing the hook once."""
    global _EXIT_HOOK_INSTALLED
    if not _EXIT_HOOK_INSTALLED:
        atexit.register(_reap_live_procs)
        _EXIT_HOOK_INSTALLED = True
    _LIVE_PROCS.add(proc)


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
        self._binaries:     dict[str, str] = {}  # ext -> server binary that was started
        self._open_files:   dict[str, int] = {}  # abs_path -> version
        self._open_digests: dict[str, str] = {}  # abs_path -> sha256 of the text last sent
        self._locks:        dict[str, asyncio.Lock] = {}   # ext -> stdio lock
        self._parked:       dict[
            asyncio.subprocess.Process, dict[int, dict[str, Any]]
        ] = {}

    # -- Public API --

    def has_any_server(self) -> bool:
        """Return True if at least one language server binary is installed."""
        for binaries in _EXT_SERVERS.values():
            for b in binaries:
                if shutil.which(b):
                    return True
        return False

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
        return [simplify_diagnostic(d) for d in raw]

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
        """Return all reference locations for symbol.

        The workspace files that mention *symbol* are opened first, because a
        server reports only the documents it has seen. The list carries a
        ``notice`` when files had to be left out.
        """
        ext    = Path(file_path).suffix
        uri    = _path_to_uri(file_path)
        notice = await self._open_workspace_files(ext, file_path, symbol)
        col    = self._find_symbol_col(file_path, line, symbol)
        result = await self._request(
            ext,
            method="textDocument/references",
            params={
                "textDocument": {"uri": uri},
                "position":     {"line": line - 1, "character": col},
                "context":      {"includeDeclaration": True},
            },
        )
        return NoticedList([
            {
                "file": _uri_to_path(r["uri"]),
                "line": r["range"]["start"]["line"] + 1,
                "col":  r["range"]["start"]["character"],
            }
            for r in result or []
        ], notice)

    async def rename(
        self, file_path: str, line: int, symbol: str, new_name: str
    ) -> dict[str, Any]:
        """Rename symbol across workspace; return changed files (and a ``notice`` when files were left out)."""
        ext    = Path(file_path).suffix
        uri    = _path_to_uri(file_path)
        notice = await self._open_workspace_files(ext, file_path, symbol)
        col    = self._find_symbol_col(file_path, line, symbol)
        result = await self._request(
            ext,
            method="textDocument/rename",
            params={
                "textDocument": {"uri": uri},
                "position":     {"line": line - 1, "character": col},
                "newName":      new_name,
            },
        )
        applied = await asyncio.to_thread(_apply_workspace_edit, result, cwd=self._project_root)
        return {**applied, "notice": notice} if notice else applied

    async def shutdown_server(self, ext: str) -> None:
        """shutdown request → exit notification → 2 s grace → SIGKILL."""
        async with self._lock_for(ext):
            proc = self._procs.pop(ext, None)
            if proc is None:
                return
            self._parked.pop(proc, None)
            _LIVE_PROCS.discard(proc)
            if proc.returncode is not None:
                return
            if proc.stdin is None or proc.stdin.is_closing():
                proc.kill()
                return

            try:  # 1. shutdown request
                self._req_id += 1
                rid = self._req_id
                m   = json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "method": "shutdown", "params": None}
                )
                proc.stdin.write((f"Content-Length: {len(m)}\r\n\r\n" + m).encode())
                await proc.stdin.drain()
                await asyncio.wait_for(
                    self._read_response(proc, rid, timeout=5.0), timeout=5.0
                )
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

    def _lock_for(self, ext: str) -> asyncio.Lock:
        """Return the stdio lock guarding the server that handles *ext*.

        One language server process serves one extension, and its stdin/stdout
        pair is a single ordered channel: a write must stay paired with the read
        that consumes its reply, and two coroutines may never await the same
        StreamReader at once.  Every path that touches the pipes takes this lock.
        """
        lock = self._locks.get(ext)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[ext] = lock
        return lock

    async def _ensure_open(self, ext: str, file_path: str) -> None:
        """Open the document on first use and resend it whenever it changed on disk.

        Edits happen through the file tools, not through the language server,
        so the server only learns about them here: a changed file is sent in
        full with ``textDocument/didChange`` under the next version number, and
        a file that no longer exists is closed.
        """
        abs_path = str(Path(file_path).resolve())
        uri      = Path(abs_path).as_uri()
        async with self._lock_for(ext):
            try:
                text = await asyncio.to_thread(
                    Path(abs_path).read_text, encoding="utf-8"
                )
            except OSError:
                if self._open_files.pop(abs_path, None) is not None:
                    self._open_digests.pop(abs_path, None)
                    await self._notify(ext, "textDocument/didClose", {"textDocument": {"uri": uri}})
                return

            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if abs_path in self._open_files:
                if self._open_digests.get(abs_path) == digest:
                    return
                version = self._open_files[abs_path] + 1
                method  = "textDocument/didChange"
                params  = {
                    "textDocument":   {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                }
            else:
                version = 1
                method  = "textDocument/didOpen"
                params  = {
                    "textDocument": {
                        "uri":        uri,
                        "languageId": language_id_for(Path(file_path).suffix),
                        "version":    version,
                        "text":       text,
                    }
                }
            self._open_files[abs_path]   = version
            self._open_digests[abs_path] = digest

            try:
                await self._notify(ext, method, params)
            except LspError:
                self._open_files.pop(abs_path, None)
                self._open_digests.pop(abs_path, None)

    async def _open_workspace_files(self, ext: str, file_path: str, symbol: str) -> str:
        """Show the server every file a cross-file answer on *symbol* depends on.

        A language server reports references and renames only from documents it
        has seen, so this opens *file_path* and each workspace file mentioning
        *symbol*, and resyncs the files that are open already (an edited one
        would otherwise answer from stale text). Returns a notice when the
        number of files to open hit ``MAX_REFERENCE_FILES``, else an empty string.
        """
        mentions = await asyncio.to_thread(mentioning_files, self._project_root, ext, symbol, file_path, MAX_REFERENCE_FILES)
        known    = [path for path in self._open_files if path.endswith(ext)]
        for path in dict.fromkeys([file_path, *mentions.files, *known]):
            await self._ensure_open(ext, path)
        if not mentions.omitted:
            return ""
        return (
            f"{mentions.omitted} more files mention {symbol!r} and were not opened "
            f"(limit {MAX_REFERENCE_FILES}), so the result may be missing their entries."
        )

    async def _notify(self, ext: str, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification; the caller holds the stdio lock."""
        proc = await self._get_proc(ext)
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        assert proc.stdin is not None
        proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n{body}".encode())
        await proc.stdin.drain()

    async def _request(
        self, ext: str, method: str, params: dict[str, Any],
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> Any:
        """Send a JSON-RPC request and await the response."""
        if ext in self._disabled:
            raise LspError(f"LSP server for {ext!r} is disabled (previous crash)")

        async with self._lock_for(ext):
            proc = await self._get_proc(ext)
            self._req_id += 1
            req_id = self._req_id

            msg  = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            body = json.dumps(msg)
            header = f"Content-Length: {len(body)}\r\n\r\n"
            assert proc.stdin is not None
            proc.stdin.write((header + body).encode())
            await proc.stdin.drain()

            try:
                response = await self._read_response(proc, req_id, timeout=timeout)
            except TimeoutError as exc:
                raise LspError(
                    f"{method} got no answer within {timeout:g}s; the language server may "
                    "still be indexing the workspace, try again shortly"
                ) from exc
        if "error" in response:
            raise LspError(f"LSP error: {response['error']}")
        return response.get("result")

    async def _read_response(
        self,
        proc:    asyncio.subprocess.Process,
        req_id:  int,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Read the response carrying *req_id*, parking the ones that are not.

        Callers hold the per-extension stdio lock, so only one coroutine ever
        awaits this StreamReader.  A response belonging to another id (a request
        that timed out earlier, for instance) is kept in a bounded buffer and
        handed to its own caller instead of being thrown away mid-stream.
        """
        assert proc.stdout is not None
        parked   = self._parked.setdefault(proc, {})
        buffered = parked.pop(req_id, None)
        if buffered is not None:
            return buffered

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
                continue  # notification or server-initiated request
            msg_id = msg.get("id")
            if msg_id == req_id:
                return dict(msg)
            if isinstance(msg_id, int):
                park_response(parked, msg_id, dict(msg))

    async def _get_proc(self, ext: str) -> asyncio.subprocess.Process:
        """Return running process for ext, starting one if needed."""
        if ext in self._procs:
            proc = self._procs[ext]
            if proc.returncode is None:
                return proc
            del self._procs[ext]
            self._parked.pop(proc, None)
            _LIVE_PROCS.discard(proc)
        try:
            proc = await self._start_server(ext)
            self._procs[ext] = proc
            await self._initialize(proc, ext)
            return proc
        except Exception as e:
            self._disabled.add(ext)
            raise LspError(f"Failed to start LSP server for {ext}: {e or type(e).__name__}") from e

    async def _start_server(self, ext: str) -> asyncio.subprocess.Process:
        binaries = _EXT_SERVERS.get(ext, [])
        for binary in binaries:
            if shutil.which(binary):
                args = [binary, *_SERVER_ARGS.get(binary, [])]
                self._binaries[ext] = binary
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                _track_proc(proc)
                return proc
        raise LspError(f"No LSP binary found for extension {ext!r}")

    async def _initialize(self, proc: asyncio.subprocess.Process, ext: str) -> None:
        """Send LSP initialize + initialized handshake."""
        binary   = self._binaries.get(ext, "")
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
                "workspaceFolders": [{"uri": root_uri, "name": Path(self._project_root).resolve().name}],
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
    """Convert a ``file://`` URI back into a filesystem path.

    ``Path.as_uri()`` percent-encodes every byte outside the unreserved set, so
    spaces and non-ASCII names come back as ``%20`` and ``%ED%95%9C`` escapes.
    Feeding those to ``open()`` looks for a file that does not exist, which is
    why the escapes have to be undone here.
    """
    from urllib.parse import unquote, urlparse
    return unquote(urlparse(uri).path)


def _apply_workspace_edit(
    edit: dict[str, Any] | None,
    cwd:  str | None = None,
) -> dict[str, Any]:
    """Apply WorkspaceEdit; prefers documentChanges over legacy changes map."""
    if not edit:
        return {"changed_files": [], "skipped_files": [], "diffs": []}

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
    skipped: list[str] = []
    for uri, text_edits in edit_pairs:
        path = _uri_to_path(uri)
        try:
            with open(path, encoding="utf-8") as f:
                original = f.readlines()
        except OSError as exc:
            logger.warning(
                "Workspace edit not applied to %s (from %s): %s", path, uri, exc
            )
            skipped.append(path)
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
                head     = original[sl][:sc]
                tail     = original[el][ec:] if el < len(original) else ""
                original = original[:sl] + [head + new_text + tail] + original[el + 1:]

        content = "".join(original).encode("utf-8")
        write_file(path, content, cwd=cwd)
        changed.append(path)

    return {"changed_files": changed, "skipped_files": skipped, "diffs": []}
