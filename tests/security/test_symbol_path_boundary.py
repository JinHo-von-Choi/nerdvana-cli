"""Working-directory boundary checks for the LSP-backed symbol tools.

The symbol tools resolve caller-supplied paths themselves, so the containment
check lives in :meth:`LanguageServerSymbolRetriever._resolve` (reads) and in
``_write_file`` (writes). These tests exercise both decision points directly:
no language server is started, so the coverage does not evaporate on a machine
without pyright installed.

작성자: 최진호
작성일: 2026-09-10
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.lsp_client import _write_file
from nerdvana_cli.core.symbol import (
    LanguageServerSymbolRetriever,
    SymbolPathBoundaryError,
)

pytestmark = pytest.mark.security


class _UnreachableClient:
    """Stand-in client that fails loudly if a rejected path still reaches the LSP layer."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"LSP client was reached via {name!r} for a path that must be refused")


class _StubClient:
    """Minimal client returning one canned documentSymbol response."""

    def __init__(self, result: list[dict[str, Any]]) -> None:
        self.result = result
        self.opened: list[str] = []

    async def _ensure_open(self, ext: str, path: str) -> None:
        self.opened.append(path)

    async def _request(self, ext: str, method: str, params: dict[str, Any]) -> Any:
        return self.result


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text("class Widget:\n    pass\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_absolute_path_outside_root_is_refused_on_read(tmp_path: Path) -> None:
    root      = _make_project(tmp_path)
    outside   = tmp_path / "outside.py"
    outside.write_text("SECRET = 1\n", encoding="utf-8")
    retriever = LanguageServerSymbolRetriever(
        client=_UnreachableClient(), project_root=str(root),
    )

    with pytest.raises(SymbolPathBoundaryError):
        await retriever.get_overview(str(outside))

    with pytest.raises(SymbolPathBoundaryError):
        await retriever.get_overview("/etc/passwd")


async def test_parent_traversal_is_refused_on_read(tmp_path: Path) -> None:
    root      = _make_project(tmp_path)
    retriever = LanguageServerSymbolRetriever(
        client=_UnreachableClient(), project_root=str(root),
    )

    with pytest.raises(SymbolPathBoundaryError):
        await retriever.find(name_path="Widget", within="../../.ssh/authorized_keys")


async def test_path_inside_root_still_reads(tmp_path: Path) -> None:
    root   = _make_project(tmp_path)
    client = _StubClient(
        [{
            "name":  "Widget",
            "kind":  5,
            "range": {"start": {"line": 0, "character": 0},
                      "end":   {"line": 1, "character": 8}},
        }],
    )
    retriever = LanguageServerSymbolRetriever(client=client, project_root=str(root))

    relative = await retriever.get_overview("pkg/mod.py")
    absolute = await retriever.get_overview(str(root / "pkg" / "mod.py"))

    assert [s.name for s in relative] == ["Widget"]
    assert [s.name for s in absolute] == ["Widget"]
    assert relative[0].location.file_path == str((root / "pkg" / "mod.py").resolve())


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def test_parent_traversal_write_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    root    = _make_project(tmp_path)
    victim  = tmp_path / ".ssh" / "authorized_keys"
    victim.parent.mkdir()
    victim.write_text("ssh-ed25519 original\n", encoding="utf-8")
    created = tmp_path / "brand-new.txt"

    with pytest.raises(PermissionError):
        _write_file(str(root / ".." / ".ssh" / "authorized_keys"),
                    b"ssh-ed25519 injected\n", cwd=str(root))
    with pytest.raises(PermissionError):
        _write_file(str(root / ".." / "brand-new.txt"), b"injected\n", cwd=str(root))

    assert victim.read_text(encoding="utf-8") == "ssh-ed25519 original\n"
    assert not created.exists()


def test_absolute_path_outside_root_is_refused_on_write(tmp_path: Path) -> None:
    root   = _make_project(tmp_path)
    victim = tmp_path / "outside.txt"
    victim.write_text("original\n", encoding="utf-8")

    with pytest.raises(PermissionError):
        _write_file(str(victim), b"injected\n", cwd=str(root))

    assert victim.read_text(encoding="utf-8") == "original\n"


def test_path_inside_root_still_writes(tmp_path: Path) -> None:
    root   = _make_project(tmp_path)
    target = root / "pkg" / "mod.py"

    _write_file(str(target), b"class Widget:\n    x = 1\n", cwd=str(root))

    assert target.read_text(encoding="utf-8") == "class Widget:\n    x = 1\n"


# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------


def test_symlink_inside_root_pointing_outside_is_refused(tmp_path: Path) -> None:
    root   = _make_project(tmp_path)
    victim = tmp_path / "secret.txt"
    victim.write_text("secret\n", encoding="utf-8")
    link = root / "innocent.txt"
    link.symlink_to(victim)

    retriever = LanguageServerSymbolRetriever(
        client=_UnreachableClient(), project_root=str(root),
    )
    with pytest.raises(SymbolPathBoundaryError):
        retriever._resolve("innocent.txt")

    with pytest.raises(PermissionError):
        _write_file(str(link), b"injected\n", cwd=str(root))

    assert victim.read_text(encoding="utf-8") == "secret\n"


def test_symlinked_directory_inside_root_pointing_outside_is_refused(tmp_path: Path) -> None:
    root    = _make_project(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "mod.py").write_text("SECRET = 1\n", encoding="utf-8")
    (root / "shortcut").symlink_to(outside, target_is_directory=True)

    retriever = LanguageServerSymbolRetriever(
        client=_UnreachableClient(), project_root=str(root),
    )
    with pytest.raises(SymbolPathBoundaryError):
        retriever._resolve("shortcut/mod.py")

    with pytest.raises(PermissionError):
        _write_file(str(root / "shortcut" / "mod.py"), b"injected\n", cwd=str(root))

    assert (outside / "mod.py").read_text(encoding="utf-8") == "SECRET = 1\n"
