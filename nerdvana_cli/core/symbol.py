"""Symbol resolution and LSP-backed symbol retrieval for Phase D.

Provides:
- NamePathResolver   — parse/match ``Module/Class.method`` paths.
- SymbolDictGrouper  — group LSP DocumentSymbol dicts by SymbolKind.
- LanguageServerSymbol — lightweight dataclass wrapping LSP symbol data.
- LanguageServerSymbolRetriever — high-level API over LspClient.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nerdvana_cli.core.lsp_client import LspClient

# ---------------------------------------------------------------------------
# SymbolKind mapping (LSP spec §3.16.0)
# ---------------------------------------------------------------------------

_KIND_NAMES: dict[int, str] = {
    1:  "File",       2:  "Module",    3:  "Namespace",   4:  "Package",
    5:  "Class",      6:  "Method",    7:  "Property",    8:  "Field",
    9:  "Constructor", 10: "Enum",     11: "Interface",   12: "Function",
    13: "Variable",   14: "Constant",  15: "String",      16: "Number",
    17: "Boolean",    18: "Array",     19: "Object",      20: "Key",
    21: "Null",       22: "EnumMember", 23: "Struct",     24: "Event",
    25: "Operator",   26: "TypeParameter",
}

_CONTAINER_KINDS: frozenset[int] = frozenset({1, 2, 3, 4, 5, 10, 11, 23})  # File/Module/Class/Enum/Interface/Struct


# ---------------------------------------------------------------------------
# NamePathResolver
# ---------------------------------------------------------------------------


class NamePathResolver:
    """Parse and match ``Foo/bar.baz`` or ``Foo/bar/baz`` name-paths.

    Grammar (informal):
        name_path ::= segment ('/' segment)*
        segment   ::= identifier ('.' identifier)*
        identifier ::= [A-Za-z_][A-Za-z0-9_]*

    Examples:
        ``MyModule``               — top-level symbol "MyModule"
        ``MyClass/method``         — "method" inside "MyClass"
        ``pkg.sub/Class/helper``   — dotted module path + symbol hierarchy
    """

    _SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

    def __init__(self, name_path: str) -> None:
        self.raw       = name_path
        self.segments  = [s for s in name_path.split("/") if s]
        if not self.segments:
            raise ValueError(f"Empty name_path: {name_path!r}")
        for seg in self.segments:
            if not self._SEGMENT_RE.match(seg):
                raise ValueError(
                    f"Invalid segment {seg!r} in name_path {name_path!r}. "
                    "Segments must match [A-Za-z_][A-Za-z0-9_.]+"
                )

    # -- public --

    @property
    def depth(self) -> int:
        """Number of segments in the path."""
        return len(self.segments)

    @property
    def leaf(self) -> str:
        """Last segment (the symbol to match)."""
        return self.segments[-1]

    @property
    def parent_segments(self) -> list[str]:
        """All segments except the leaf."""
        return self.segments[:-1]

    def matches_name(self, name: str, substring: bool = False) -> bool:
        """Return True if ``name`` matches the leaf segment.

        Parameters
        ----------
        name:
            The symbol name returned by the language server.
        substring:
            When True, accept ``leaf`` as a case-insensitive substring of ``name``.
        """
        if substring:
            return self.leaf.lower() in name.lower()
        return name == self.leaf

    def matches_name_path(self, candidate: str) -> bool:
        """Return True if the full candidate path matches *self* exactly.

        ``candidate`` may use ``/`` as separator, e.g. ``"MyClass/method"``.
        """
        return self.raw == candidate or self.segments == [
            s for s in candidate.split("/") if s
        ]

    def is_child_of(self, ancestor_segments: list[str]) -> bool:
        """Return True when *self* is nested under *ancestor_segments*."""
        if len(ancestor_segments) >= self.depth:
            return False
        return self.segments[: len(ancestor_segments)] == ancestor_segments

    # -- dunder --

    def __repr__(self) -> str:
        return f"NamePathResolver({self.raw!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NamePathResolver):
            return self.segments == other.segments
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(self.segments))


# ---------------------------------------------------------------------------
# SymbolDictGrouper
# ---------------------------------------------------------------------------


class SymbolDictGrouper:
    """Group flat or nested LSP DocumentSymbol dicts by SymbolKind.

    Input format (LSP DocumentSymbol):
        {"name": "...", "kind": <int>, "range": {...}, "children": [...]}

    Output: compact JSON-friendly dict keyed by kind-name.
    """

    def group(
        self,
        symbols: list[dict[str, Any]],
        *,
        max_depth: int = 1,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return dict mapping kind-name → list of simplified symbol dicts.

        Parameters
        ----------
        symbols:
            Raw LSP DocumentSymbol list.
        max_depth:
            How deep to recurse into children. 0 = only top-level.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        self._collect(symbols, result, current_depth=0, max_depth=max_depth)
        return result

    def _collect(
        self,
        symbols: list[dict[str, Any]],
        result:  dict[str, list[dict[str, Any]]],
        current_depth: int,
        max_depth:     int,
    ) -> None:
        for sym in symbols:
            kind_int  = sym.get("kind", 0)
            kind_name = _KIND_NAMES.get(kind_int, f"Unknown({kind_int})")
            simplified = {
                "name":      sym.get("name", ""),
                "kind":      kind_name,
                "kind_int":  kind_int,
                "range_start": {
                    "line":      sym.get("range", {}).get("start", {}).get("line", 0),
                    "character": sym.get("range", {}).get("start", {}).get("character", 0),
                },
            }
            result.setdefault(kind_name, []).append(simplified)

            children = sym.get("children") or []
            if children and current_depth < max_depth:
                self._collect(children, result, current_depth + 1, max_depth)

    def to_compact(
        self,
        symbols: list[dict[str, Any]],
        *,
        max_depth: int = 1,
    ) -> dict[str, list[str]]:
        """Return compact dict: kind-name → list of symbol names (strings)."""
        grouped = self.group(symbols, max_depth=max_depth)
        return {kind: [s["name"] for s in syms] for kind, syms in grouped.items()}


# ---------------------------------------------------------------------------
# LanguageServerSymbol
# ---------------------------------------------------------------------------


@dataclass
class Location:
    """Simplified LSP Location (file path + line/character)."""

    file_path:  str
    line:       int    # 1-based
    character:  int    # 0-based column


@dataclass
class LanguageServerSymbol:
    """Lightweight wrapper around a single LSP DocumentSymbol entry.

    Attributes
    ----------
    name:
        Symbol name as returned by the LSP server.
    name_path:
        Full ``Parent/Child`` path (built during tree traversal).
    kind:
        Human-readable kind string, e.g. ``"Class"``, ``"Method"``.
    kind_int:
        Raw LSP SymbolKind integer.
    location:
        Definition location.
    children:
        Nested child symbols (depth-limited by retriever).
    detail:
        Optional signature detail string from LSP.
    """

    name:      str
    name_path: str
    kind:      str
    kind_int:  int
    location:  Location
    children:  list[LanguageServerSymbol]  = field(default_factory=list)
    detail:    str                         = ""

    # -- helpers --

    def to_dict(self, *, include_children: bool = True) -> dict[str, Any]:
        """Convert to JSON-serialisable dict."""
        d: dict[str, Any] = {
            "name":       self.name,
            "name_path":  self.name_path,
            "kind":       self.kind,
            "location":   {
                "file":      self.location.file_path,
                "line":      self.location.line,
                "character": self.location.character,
            },
        }
        if self.detail:
            d["detail"] = self.detail
        if include_children and self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sym_from_dict(
    raw:       dict[str, Any],
    file_path: str,
    parent_path: str,
    depth:       int,
    max_depth:   int,
) -> LanguageServerSymbol:
    """Recursively build LanguageServerSymbol from raw LSP DocumentSymbol dict."""
    name      = raw.get("name", "")
    kind_int  = raw.get("kind", 0)
    kind_name = _KIND_NAMES.get(kind_int, f"Unknown({kind_int})")
    detail    = raw.get("detail") or ""

    path_sep   = "/" if parent_path else ""
    name_path  = parent_path + path_sep + name

    start      = (raw.get("range") or {}).get("start") or {}
    location   = Location(
        file_path = file_path,
        line      = (start.get("line") or 0) + 1,   # convert to 1-based
        character = start.get("character") or 0,
    )

    children: list[LanguageServerSymbol] = []
    if depth < max_depth:
        for child_raw in (raw.get("children") or []):
            children.append(
                _sym_from_dict(child_raw, file_path, name_path, depth + 1, max_depth)
            )

    return LanguageServerSymbol(
        name      = name,
        name_path = name_path,
        kind      = kind_name,
        kind_int  = kind_int,
        location  = location,
        children  = children,
        detail    = detail,
    )


def _flatten(symbols: list[LanguageServerSymbol]) -> list[LanguageServerSymbol]:
    """Yield all symbols and their descendants in DFS order."""
    result: list[LanguageServerSymbol] = []
    stack = list(symbols)
    while stack:
        sym = stack.pop(0)
        result.append(sym)
        stack[:0] = sym.children   # prepend children (BFS-ish order)
    return result


# ---------------------------------------------------------------------------
# LanguageServerSymbolRetriever
# ---------------------------------------------------------------------------


class LanguageServerSymbolRetriever:
    """High-level LSP symbol query API.

    Depends on :class:`~nerdvana_cli.core.lsp_client.LspClient` for I/O.

    Parameters
    ----------
    client:
        An initialised ``LspClient`` instance.
    project_root:
        Workspace root; used for resolving relative paths.
    """

    def __init__(self, client: LspClient, project_root: str | None = None) -> None:
        self._client       = client
        self._project_root = project_root or ""

    # -- public --

    async def get_overview(
        self,
        relative_path: str,
        depth:         int = 0,
    ) -> list[LanguageServerSymbol]:
        """Return top-level (plus depth-limited) symbols for a file.

        Parameters
        ----------
        relative_path:
            File path relative to project root (or absolute).
        depth:
            0 = top-level only, 1 = +one level of children, etc.
        """
        abs_path  = self._resolve(relative_path)
        raw_syms  = await self._request_document_symbols(abs_path)
        return [
            _sym_from_dict(r, abs_path, "", 0, depth)
            for r in (raw_syms or [])
        ]

    async def find(
        self,
        name_path:  str,
        substring:  bool       = False,
        within:     str | None = None,
    ) -> list[LanguageServerSymbol]:
        """Find symbols matching *name_path* across the workspace.

        Parameters
        ----------
        name_path:
            ``Foo/bar.baz`` path expression. Only the *leaf* segment is used
            for matching (parent segments filter by containment when
            multi-segment path provided).
        substring:
            Case-insensitive substring match on leaf.
        within:
            Optional relative file path; restrict search to that file only.
        """
        resolver = NamePathResolver(name_path)

        if within is not None:
            abs_path  = self._resolve(within)
            raw_syms  = await self._request_document_symbols(abs_path)
            all_syms  = [_sym_from_dict(r, abs_path, "", 0, 99) for r in (raw_syms or [])]
        else:
            # Workspace-level: use textDocument/documentSymbol on each open file.
            # For non-integration contexts the retriever works file-by-file.
            # In the absence of workspace/symbol support we raise clearly.
            raise LspSymbolError(
                "workspace-wide find requires within= to be specified "
                "(workspace/symbol not implemented in this client)"
            )

        flat = _flatten(all_syms)

        matched: list[LanguageServerSymbol] = []
        for sym in flat:
            if not resolver.matches_name(sym.name, substring=substring):
                continue
            # Multi-segment: check parent path containment
            if resolver.depth > 1:
                parts = [s for s in sym.name_path.split("/") if s]
                # parent segments of sym must include all of resolver.parent_segments
                if parts[:-1] != resolver.parent_segments:
                    continue
            matched.append(sym)
        return matched

    async def find_references(
        self,
        symbol: LanguageServerSymbol,
    ) -> list[Location]:
        """Return all reference locations for *symbol*.

        Delegates to ``textDocument/references`` via ``LspClient.find_references``.
        """
        loc       = symbol.location
        file_path = loc.file_path
        line      = loc.line
        name      = symbol.name

        raw_refs  = await self._client.find_references(file_path, line, name)
        return [
            Location(
                file_path = r["file"],
                line      = r["line"],
                character = r["col"],
            )
            for r in raw_refs
        ]

    # -- internal --

    def _resolve(self, path: str) -> str:
        """Resolve relative path against project_root if not absolute."""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        if self._project_root:
            return str(Path(self._project_root) / p)
        return str(p.resolve())

    async def _request_document_symbols(
        self,
        abs_path: str,
    ) -> list[dict[str, Any]]:
        """Send textDocument/documentSymbol and return raw result list."""
        ext = Path(abs_path).suffix
        uri = Path(abs_path).resolve().as_uri()
        await self._client._ensure_open(ext, abs_path)   # noqa: SLF001 — internal helper
        result = await self._client._request(            # noqa: SLF001
            ext,
            method = "textDocument/documentSymbol",
            params = {"textDocument": {"uri": uri}},
        )
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return []


# ---------------------------------------------------------------------------
# Domain error
# ---------------------------------------------------------------------------


class LspSymbolError(Exception):
    """Raised when a symbol query cannot be completed."""
