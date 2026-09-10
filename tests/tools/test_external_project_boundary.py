"""External project registration boundary and enable switch.

Reproduces two escapes:

- ``RegisterExternalProjectTool._safe_resolve`` accepted any existing
  directory, the filesystem root included, and followed a symlinked target
  out of the allowed area even though the tool description claimed the
  opposite.
- ``external_projects_enabled`` was read from settings but never declared on
  the settings model, so pydantic dropped it and the tool family could not be
  turned off.

작성자: 최진호
작성일: 2026-09-10
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.external_projects import ExternalProjectRegistry
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.external_project_tools import (
    BOUNDARY_ROOT_ENV_VAR,
    RegisterExternalProjectArgs,
    RegisterExternalProjectTool,
)
from nerdvana_cli.tools.registry import create_tool_registry

pytestmark = pytest.mark.security

EXTERNAL_TOOL_NAMES = (
    "ListQueryableProjects",
    "RegisterExternalProject",
    "QueryExternalProject",
)


@pytest.fixture()
def registry(tmp_path: Path) -> ExternalProjectRegistry:
    return ExternalProjectRegistry(registry_path=tmp_path / "ep.yml")


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext()


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Allowed containment root with one real project directory inside it."""
    allowed = tmp_path / "allowed"
    (allowed / "proj").mkdir(parents=True)
    return allowed


# ---------------------------------------------------------------------------
# Outside the boundary is rejected
# ---------------------------------------------------------------------------

def test_filesystem_root_is_rejected() -> None:
    """The old resolver returned PosixPath('/') because '/' merely exists."""
    with pytest.raises(ValueError, match="filesystem root"):
        RegisterExternalProjectTool._safe_resolve("/")


def test_path_outside_configured_root_is_rejected(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside the allowed external project root"):
        RegisterExternalProjectTool._safe_resolve(str(outside), root)


def test_parent_traversal_out_of_root_is_rejected(root: Path) -> None:
    escape = root / "proj" / ".." / ".." / "outside_via_dotdot"
    escape.resolve().mkdir()

    with pytest.raises(ValueError, match="outside the allowed external project root"):
        RegisterExternalProjectTool._safe_resolve(str(escape), root)


def test_configured_root_itself_is_rejected(root: Path) -> None:
    with pytest.raises(ValueError, match="root itself cannot be registered"):
        RegisterExternalProjectTool._safe_resolve(str(root), root)


async def test_call_reports_boundary_error(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    tmp_path: Path,
    root:     Path,
) -> None:
    outside = tmp_path / "outside_call"
    outside.mkdir()

    tool   = RegisterExternalProjectTool(registry=registry, allowed_root=root)
    result = await tool.call(
        RegisterExternalProjectArgs(name="escape", path=str(outside)), ctx, can_use_tool=None,
    )

    assert result.is_error is True
    assert "outside the allowed external project root" in result.content
    assert registry.get("escape") is None


# ---------------------------------------------------------------------------
# Inside the boundary still works
# ---------------------------------------------------------------------------

def test_path_inside_configured_root_resolves(root: Path) -> None:
    resolved = RegisterExternalProjectTool._safe_resolve(str(root / "proj"), root)
    assert resolved == Path(str(root.resolve() / "proj"))


async def test_registration_inside_root_succeeds(
    registry: ExternalProjectRegistry,
    ctx:      ToolContext,
    root:     Path,
) -> None:
    tool   = RegisterExternalProjectTool(registry=registry, allowed_root=root)
    result = await tool.call(
        RegisterExternalProjectArgs(name="inside", path=str(root / "proj"), languages=["python"]),
        ctx,
        can_use_tool=None,
    )

    assert result.is_error is False
    project = registry.get("inside")
    assert project is not None
    assert project.path == str(root.resolve() / "proj")


def test_env_var_supplies_the_root(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Production wiring: the tool is built with no arguments in the registry."""
    monkeypatch.setenv(BOUNDARY_ROOT_ENV_VAR, str(root))

    assert RegisterExternalProjectTool._safe_resolve(str(root / "proj")) == Path(
        str(root.resolve() / "proj")
    )
    with pytest.raises(ValueError, match="outside the allowed external project root"):
        RegisterExternalProjectTool._safe_resolve(str(root.parent))


# ---------------------------------------------------------------------------
# Symlinks out of the boundary are rejected
# ---------------------------------------------------------------------------

def test_symlink_out_of_configured_root_is_rejected(tmp_path: Path, root: Path) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    link = root / "link"
    link.symlink_to(secret, target_is_directory=True)

    with pytest.raises(ValueError, match="outside the allowed external project root"):
        RegisterExternalProjectTool._safe_resolve(str(link), root)


def test_symlinked_target_is_rejected_without_a_root(tmp_path: Path) -> None:
    """The default posture must not follow a symlinked project directory."""
    secret = tmp_path / "secret_no_root"
    secret.mkdir()
    link = tmp_path / "link_no_root"
    link.symlink_to(secret, target_is_directory=True)

    with pytest.raises(ValueError, match="Symlinked path component"):
        RegisterExternalProjectTool._safe_resolve(str(link))


def test_symlinked_intermediate_component_is_rejected(tmp_path: Path, root: Path) -> None:
    secret = tmp_path / "secret_tree"
    (secret / "inner").mkdir(parents=True)
    (root / "hop").symlink_to(secret, target_is_directory=True)

    with pytest.raises(ValueError, match="outside the allowed external project root"):
        RegisterExternalProjectTool._safe_resolve(str(root / "hop" / "inner"), root)


# ---------------------------------------------------------------------------
# The settings switch actually switches
# ---------------------------------------------------------------------------

def test_default_is_disabled() -> None:
    settings = NerdvanaSettings(_env_file=None)
    assert settings.external_projects_enabled is False


def test_disabled_settings_keep_the_tools_out_of_the_registry() -> None:
    settings = NerdvanaSettings(_env_file=None)
    registry = create_tool_registry(settings=settings)

    for name in EXTERNAL_TOOL_NAMES:
        assert registry.get(name) is None, f"{name} must not be registered when disabled"


def test_yaml_can_enable_the_tools(tmp_path: Path) -> None:
    config = tmp_path / "nerdvana.yml"
    config.write_text("external_projects_enabled: true\n", encoding="utf-8")

    settings = NerdvanaSettings.load(str(config))
    assert settings.external_projects_enabled is True

    registry = create_tool_registry(settings=settings)
    for name in EXTERNAL_TOOL_NAMES:
        assert registry.get(name) is not None, f"{name} must be registered when enabled"


def test_yaml_can_disable_the_tools(tmp_path: Path) -> None:
    config = tmp_path / "nerdvana.yml"
    config.write_text("external_projects_enabled: false\n", encoding="utf-8")

    settings = NerdvanaSettings.load(str(config))
    assert settings.external_projects_enabled is False

    registry = create_tool_registry(settings=settings)
    for name in EXTERNAL_TOOL_NAMES:
        assert registry.get(name) is None
