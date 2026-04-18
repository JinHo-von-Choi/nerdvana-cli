"""External project tools — schema and behaviour tests — Phase H.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.core.external_projects import ExternalProject, ExternalProjectRegistry
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.server.external_worker import ExternalWorker
from nerdvana_cli.tools.external_project_tools import (
    ListQueryableProjectsTool,
    QueryExternalProjectTool,
    RegisterExternalProjectTool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry(tmp_path: Path) -> ExternalProjectRegistry:
    return ExternalProjectRegistry(registry_path=tmp_path / "ep.yml")


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext()


# ---------------------------------------------------------------------------
# ListQueryableProjects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_empty(registry: ExternalProjectRegistry, ctx: ToolContext) -> None:
    tool   = ListQueryableProjectsTool(registry=registry)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert "No external projects" in result.content


@pytest.mark.asyncio
async def test_list_shows_registered(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
) -> None:
    registry.add(ExternalProject(name="mylib", path=str(tmp_path), languages=["python"]))
    tool   = ListQueryableProjectsTool(registry=registry)
    result = await tool.call(None, ctx, can_use_tool=None)
    assert "mylib" in result.content
    assert str(tmp_path) in result.content


def test_list_schema() -> None:
    tool = ListQueryableProjectsTool()
    assert tool.input_schema == {"type": "object", "properties": {}, "required": []}


# ---------------------------------------------------------------------------
# RegisterExternalProject
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_valid_path(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
) -> None:
    tool = RegisterExternalProjectTool(registry=registry)
    from nerdvana_cli.tools.external_project_tools import RegisterExternalProjectArgs
    args   = RegisterExternalProjectArgs(name="newproj", path=str(tmp_path), languages=["go"])
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is False
    assert "newproj" in result.content
    assert registry.get("newproj") is not None


@pytest.mark.asyncio
async def test_register_nonexistent_path(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
) -> None:
    tool = RegisterExternalProjectTool(registry=registry)
    from nerdvana_cli.tools.external_project_tools import RegisterExternalProjectArgs
    args   = RegisterExternalProjectArgs(name="bad", path="/this/does/not/exist/ever")
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is True
    assert "does not exist" in result.content


@pytest.mark.asyncio
async def test_register_file_not_dir(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
) -> None:
    file = tmp_path / "afile.txt"
    file.write_text("x")
    tool = RegisterExternalProjectTool(registry=registry)
    from nerdvana_cli.tools.external_project_tools import RegisterExternalProjectArgs
    args   = RegisterExternalProjectArgs(name="badfile", path=str(file))
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is True
    assert "not a directory" in result.content


# ---------------------------------------------------------------------------
# QueryExternalProject
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_unregistered_project(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
) -> None:
    tool = QueryExternalProjectTool(registry=registry, worker=ExternalWorker())
    from nerdvana_cli.tools.external_project_tools import QueryExternalProjectArgs
    args   = QueryExternalProjectArgs(name="ghost", question="what?")
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is True
    assert "ghost" in result.content


@pytest.mark.asyncio
async def test_query_returns_answer(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
) -> None:
    registry.add(ExternalProject(name="myproj", path=str(tmp_path)))

    worker = MagicMock(spec=ExternalWorker)
    worker.send_query = AsyncMock(return_value="AuthService handles login flows.")

    tool = QueryExternalProjectTool(registry=registry, worker=worker)
    from nerdvana_cli.tools.external_project_tools import QueryExternalProjectArgs
    args   = QueryExternalProjectArgs(name="myproj", question="What does AuthService do?")
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is False
    assert "AuthService" in result.content


@pytest.mark.asyncio
async def test_query_subprocess_error_is_isolated(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
) -> None:
    """Subprocess crash must not propagate to the main loop — only is_error=True."""
    registry.add(ExternalProject(name="crasher", path=str(tmp_path)))

    worker = MagicMock(spec=ExternalWorker)
    worker.send_query = AsyncMock(side_effect=RuntimeError("subprocess died"))

    tool = QueryExternalProjectTool(registry=registry, worker=worker)
    from nerdvana_cli.tools.external_project_tools import QueryExternalProjectArgs
    args   = QueryExternalProjectArgs(name="crasher", question="anything")
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is True
    assert "subprocess died" in result.content


@pytest.mark.asyncio
async def test_query_timeout_is_isolated(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
) -> None:
    registry.add(ExternalProject(name="slow", path=str(tmp_path)))

    worker = MagicMock(spec=ExternalWorker)
    worker.send_query = AsyncMock(side_effect=asyncio.TimeoutError())

    tool = QueryExternalProjectTool(registry=registry, worker=worker)
    from nerdvana_cli.tools.external_project_tools import QueryExternalProjectArgs
    args   = QueryExternalProjectArgs(name="slow", question="why so slow?")
    result = await tool.call(args, ctx, can_use_tool=None)
    assert result.is_error is True
    assert "timed out" in result.content


def test_query_schema_has_required_fields() -> None:
    tool   = QueryExternalProjectTool()
    schema = tool.input_schema
    assert "name"     in schema["required"]
    assert "question" in schema["required"]
