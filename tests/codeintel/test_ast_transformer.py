"""Unit tests for nerdvana_cli.codeintel.ast_transformer.

Covers call renaming, attribute call renaming, keyword argument renaming,
import replacement, and source preservation when no rule matches.

작성자: 최진호
작성일: 2026-10-05
"""
from __future__ import annotations

from nerdvana_cli.codeintel.ast_transformer import RewriteRule, transform_source


def test_function_call_renamed() -> None:
    source = "result = fetch_data(query='x')\n"
    new_source, count = transform_source(
        source,
        [RewriteRule(old_symbol="fetch_data", new_symbol="get_records")],
    )
    assert count == 1
    assert new_source == "result = get_records(query='x')"


def test_attribute_call_renamed() -> None:
    source = "client.fetch(query='x')\n"
    new_source, count = transform_source(
        source,
        [RewriteRule(old_symbol="client.fetch", new_symbol="client.records.get")],
    )
    assert count == 1
    assert new_source == "client.records.get(query='x')"


def test_keyword_argument_renamed() -> None:
    source = "client.fetch(query='x', limit=5)\n"
    new_source, count = transform_source(
        source,
        [
            RewriteRule(
                old_symbol="client.fetch",
                new_symbol="client.records.get",
                rename_args={"query": "filter"},
            )
        ],
    )
    assert count == 2
    assert new_source == "client.records.get(filter='x', limit=5)"


def test_import_replaced() -> None:
    source = "import acme.legacy\nfrom acme.legacy import helper\n"
    new_source, count = transform_source(
        source,
        [RewriteRule(old_symbol="", new_symbol="", old_import="acme.legacy", new_import="acme.v2")],
    )
    assert count == 2
    assert new_source == "import acme.v2\nfrom acme.v2 import helper"


def test_no_matching_rule_preserves_source() -> None:
    source = "value = other.thing(1)\n"
    new_source, count = transform_source(
        source,
        [RewriteRule(old_symbol="client.fetch", new_symbol="client.records.get")],
    )
    assert count == 0
    assert new_source == source
