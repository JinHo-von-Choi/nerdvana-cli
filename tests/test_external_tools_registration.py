"""Regression tests — external project tools are registered in create_tool_registry.

Verifies M-1 fix: ListQueryableProjectsTool, RegisterExternalProjectTool,
and QueryExternalProjectTool must appear in the registry returned by
create_tool_registry().  Prior to the fix all three were absent.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import pytest

from nerdvana_cli.tools.registry import create_tool_registry


@pytest.fixture()
def registry():
    return create_tool_registry()


# ---------------------------------------------------------------------------
# Presence checks
# ---------------------------------------------------------------------------

def test_list_queryable_projects_registered(registry) -> None:
    tool = registry.get("ListQueryableProjects")
    assert tool is not None, "ListQueryableProjects must be registered in create_tool_registry()"


def test_register_external_project_registered(registry) -> None:
    tool = registry.get("RegisterExternalProject")
    assert tool is not None, "RegisterExternalProject must be registered in create_tool_registry()"


def test_query_external_project_registered(registry) -> None:
    tool = registry.get("QueryExternalProject")
    assert tool is not None, "QueryExternalProject must be registered in create_tool_registry()"


# ---------------------------------------------------------------------------
# Class identity checks — ensure correct types are returned
# ---------------------------------------------------------------------------

def test_list_queryable_projects_type(registry) -> None:
    from nerdvana_cli.tools.external_project_tools import ListQueryableProjectsTool
    assert isinstance(registry.get("ListQueryableProjects"), ListQueryableProjectsTool)


def test_register_external_project_type(registry) -> None:
    from nerdvana_cli.tools.external_project_tools import RegisterExternalProjectTool
    assert isinstance(registry.get("RegisterExternalProject"), RegisterExternalProjectTool)


def test_query_external_project_type(registry) -> None:
    from nerdvana_cli.tools.external_project_tools import QueryExternalProjectTool
    assert isinstance(registry.get("QueryExternalProject"), QueryExternalProjectTool)


# ---------------------------------------------------------------------------
# Tool schema sanity
# ---------------------------------------------------------------------------

def test_list_tool_schema_is_empty_object(registry) -> None:
    tool = registry.get("ListQueryableProjects")
    assert tool.input_schema == {"type": "object", "properties": {}, "required": []}


def test_register_tool_schema_has_name_and_path(registry) -> None:
    tool   = registry.get("RegisterExternalProject")
    props  = tool.input_schema.get("properties", {})
    req    = tool.input_schema.get("required", [])
    assert "name" in props
    assert "path" in props
    assert "name" in req
    assert "path" in req


def test_query_tool_schema_has_name_and_question(registry) -> None:
    tool  = registry.get("QueryExternalProject")
    props = tool.input_schema.get("properties", {})
    req   = tool.input_schema.get("required", [])
    assert "name"     in props
    assert "question" in props
    assert "name"     in req
    assert "question" in req


# ---------------------------------------------------------------------------
# Disabled via settings flag
# ---------------------------------------------------------------------------

def test_tools_absent_when_external_projects_disabled() -> None:
    """settings.external_projects_enabled=False must keep 3 tools out of registry."""

    class _FakeSettings:
        external_projects_enabled = False

    reg = create_tool_registry(settings=_FakeSettings())
    assert reg.get("ListQueryableProjects")   is None
    assert reg.get("RegisterExternalProject") is None
    assert reg.get("QueryExternalProject")    is None


def test_tools_present_when_external_projects_enabled() -> None:
    """settings.external_projects_enabled=True (explicit) must include the tools."""

    class _FakeSettings:
        external_projects_enabled = True

    reg = create_tool_registry(settings=_FakeSettings())
    assert reg.get("ListQueryableProjects")   is not None
    assert reg.get("RegisterExternalProject") is not None
    assert reg.get("QueryExternalProject")    is not None
