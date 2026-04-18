"""Unit tests for nerdvana_cli.core.symbol.

Tests NamePathResolver, SymbolDictGrouper, LanguageServerSymbol.
No LSP process required.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import pytest

from nerdvana_cli.core.symbol import (
    LanguageServerSymbol,
    Location,
    LspSymbolError,
    NamePathResolver,
    SymbolDictGrouper,
    _sym_from_dict,
)


# ---------------------------------------------------------------------------
# NamePathResolver — 5 test cases
# ---------------------------------------------------------------------------


class TestNamePathResolver:
    def test_single_segment(self) -> None:
        r = NamePathResolver("MyClass")
        assert r.segments == ["MyClass"]
        assert r.depth == 1
        assert r.leaf == "MyClass"
        assert r.parent_segments == []

    def test_two_segment_slash(self) -> None:
        r = NamePathResolver("MyClass/method")
        assert r.segments == ["MyClass", "method"]
        assert r.depth == 2
        assert r.leaf == "method"
        assert r.parent_segments == ["MyClass"]

    def test_dotted_module_segment(self) -> None:
        r = NamePathResolver("pkg.sub/Class/helper")
        assert r.segments == ["pkg.sub", "Class", "helper"]
        assert r.leaf == "helper"
        assert r.parent_segments == ["pkg.sub", "Class"]

    def test_matches_name_exact(self) -> None:
        r = NamePathResolver("bar")
        assert r.matches_name("bar") is True
        assert r.matches_name("baz") is False
        assert r.matches_name("Bar") is False  # case-sensitive exact

    def test_matches_name_substring(self) -> None:
        r = NamePathResolver("bar")
        assert r.matches_name("fooBar", substring=True) is True
        assert r.matches_name("BAR",    substring=True) is True  # case-insensitive
        assert r.matches_name("baz",    substring=True) is False

    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty name_path"):
            NamePathResolver("")

    def test_invalid_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid segment"):
            NamePathResolver("123bad")

    def test_matches_name_path_full(self) -> None:
        r = NamePathResolver("A/B")
        assert r.matches_name_path("A/B") is True
        assert r.matches_name_path("A/C") is False

    def test_is_child_of(self) -> None:
        r = NamePathResolver("A/B/C")
        assert r.is_child_of(["A"]) is True
        assert r.is_child_of(["A", "B"]) is True
        assert r.is_child_of(["X"]) is False

    def test_equality_and_hash(self) -> None:
        r1 = NamePathResolver("A/B")
        r2 = NamePathResolver("A/B")
        r3 = NamePathResolver("A/C")
        assert r1 == r2
        assert r1 != r3
        assert hash(r1) == hash(r2)


# ---------------------------------------------------------------------------
# SymbolDictGrouper — 3 test cases
# ---------------------------------------------------------------------------

_RAW_SYMBOLS = [
    {
        "name": "MyClass",
        "kind": 5,   # Class
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 10, "character": 0}},
        "children": [
            {
                "name": "my_method",
                "kind": 6,   # Method
                "range": {"start": {"line": 2, "character": 4}, "end": {"line": 5, "character": 4}},
            }
        ],
    },
    {
        "name": "top_func",
        "kind": 12,  # Function
        "range": {"start": {"line": 12, "character": 0}, "end": {"line": 15, "character": 0}},
        "children": [],
    },
]


class TestSymbolDictGrouper:
    def test_group_by_kind_top_level(self) -> None:
        g = SymbolDictGrouper()
        result = g.group(_RAW_SYMBOLS, max_depth=0)
        assert "Class" in result
        assert "Function" in result
        assert len(result["Class"]) == 1
        assert result["Class"][0]["name"] == "MyClass"

    def test_group_includes_children_with_depth(self) -> None:
        g = SymbolDictGrouper()
        result = g.group(_RAW_SYMBOLS, max_depth=1)
        assert "Method" in result
        assert result["Method"][0]["name"] == "my_method"

    def test_to_compact_returns_name_lists(self) -> None:
        g = SymbolDictGrouper()
        compact = g.to_compact(_RAW_SYMBOLS, max_depth=1)
        assert compact["Class"] == ["MyClass"]
        assert compact["Function"] == ["top_func"]
        assert compact["Method"] == ["my_method"]

    def test_empty_input(self) -> None:
        g = SymbolDictGrouper()
        assert g.group([]) == {}
        assert g.to_compact([]) == {}


# ---------------------------------------------------------------------------
# LanguageServerSymbol — from _sym_from_dict
# ---------------------------------------------------------------------------


_RAW_CLASS = {
    "name": "Foo",
    "kind": 5,
    "detail": "class Foo",
    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 20, "character": 0}},
    "children": [
        {
            "name": "bar",
            "kind": 6,
            "range": {"start": {"line": 2, "character": 4}, "end": {"line": 5, "character": 4}},
        }
    ],
}


class TestLanguageServerSymbol:
    def test_basic_construction(self) -> None:
        sym = _sym_from_dict(_RAW_CLASS, "/proj/foo.py", "", 0, 0)
        assert sym.name == "Foo"
        assert sym.kind == "Class"
        assert sym.name_path == "Foo"
        assert sym.location.file_path == "/proj/foo.py"
        assert sym.location.line == 1  # 0-based → 1-based

    def test_children_populated_with_depth(self) -> None:
        sym = _sym_from_dict(_RAW_CLASS, "/proj/foo.py", "", 0, 1)
        assert len(sym.children) == 1
        child = sym.children[0]
        assert child.name == "bar"
        assert child.kind == "Method"
        assert child.name_path == "Foo/bar"

    def test_children_not_populated_at_depth_0(self) -> None:
        sym = _sym_from_dict(_RAW_CLASS, "/proj/foo.py", "", 0, 0)
        assert sym.children == []

    def test_to_dict_roundtrip(self) -> None:
        sym = _sym_from_dict(_RAW_CLASS, "/proj/foo.py", "", 0, 1)
        d   = sym.to_dict()
        assert d["name"] == "Foo"
        assert d["name_path"] == "Foo"
        assert d["kind"] == "Class"
        assert d["detail"] == "class Foo"
        assert len(d["children"]) == 1

    def test_detail_optional(self) -> None:
        raw = dict(_RAW_CLASS)
        del raw["detail"]
        sym = _sym_from_dict(raw, "/proj/foo.py", "", 0, 0)
        assert sym.detail == ""
        d = sym.to_dict()
        assert "detail" not in d
