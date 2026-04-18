"""ExternalProjectRegistry CRUD + YAML round-trip tests — Phase H.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nerdvana_cli.core.external_projects import ExternalProject, ExternalProjectRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_registry(tmp_path: Path) -> ExternalProjectRegistry:
    """Return a registry backed by a temporary file."""
    return ExternalProjectRegistry(registry_path=tmp_path / "external_projects.yml")


@pytest.fixture()
def sample_project(tmp_path: Path) -> ExternalProject:
    return ExternalProject(
        name          = "mylib",
        path          = str(tmp_path / "mylib"),
        languages     = ["python"],
        registered_at = "2026-04-18T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# ExternalProject model tests
# ---------------------------------------------------------------------------

def test_external_project_to_dict(sample_project: ExternalProject) -> None:
    d = sample_project.to_dict()
    assert d["name"]          == "mylib"
    assert d["languages"]     == ["python"]
    assert d["registered_at"] == "2026-04-18T00:00:00+00:00"


def test_external_project_from_dict_roundtrip(sample_project: ExternalProject) -> None:
    restored = ExternalProject.from_dict(sample_project.to_dict())
    assert restored.name      == sample_project.name
    assert restored.path      == sample_project.path
    assert restored.languages == sample_project.languages


def test_external_project_default_registered_at() -> None:
    p = ExternalProject(name="x", path="/tmp")
    assert p.registered_at.startswith("20")  # ISO 8601


def test_external_project_equality() -> None:
    a = ExternalProject(name="a", path="/foo")
    b = ExternalProject(name="a", path="/foo")
    c = ExternalProject(name="a", path="/bar")
    assert a == b
    assert a != c


# ---------------------------------------------------------------------------
# Registry CRUD tests
# ---------------------------------------------------------------------------

def test_registry_add_and_get(tmp_registry: ExternalProjectRegistry, tmp_path: Path) -> None:
    proj = ExternalProject(name="reactdom", path=str(tmp_path))
    tmp_registry.add(proj)
    retrieved = tmp_registry.get("reactdom")
    assert retrieved is not None
    assert retrieved.path == str(tmp_path)


def test_registry_get_missing_returns_none(tmp_registry: ExternalProjectRegistry) -> None:
    assert tmp_registry.get("ghost") is None


def test_registry_remove(tmp_registry: ExternalProjectRegistry, tmp_path: Path) -> None:
    proj = ExternalProject(name="removeme", path=str(tmp_path))
    tmp_registry.add(proj)
    assert "removeme" in tmp_registry
    result = tmp_registry.remove("removeme")
    assert result is True
    assert "removeme" not in tmp_registry


def test_registry_remove_nonexistent(tmp_registry: ExternalProjectRegistry) -> None:
    assert tmp_registry.remove("ghost") is False


def test_registry_list_all_sorted(tmp_registry: ExternalProjectRegistry, tmp_path: Path) -> None:
    for name in ("zebra", "alpha", "middle"):
        tmp_registry.add(ExternalProject(name=name, path=str(tmp_path)))
    names = [p.name for p in tmp_registry.list_all()]
    assert names == sorted(names)


def test_registry_overwrite_same_name(tmp_registry: ExternalProjectRegistry, tmp_path: Path) -> None:
    tmp_registry.add(ExternalProject(name="dup", path="/old"))
    tmp_registry.add(ExternalProject(name="dup", path=str(tmp_path)))
    assert tmp_registry.get("dup").path == str(tmp_path)  # type: ignore[union-attr]
    assert len(tmp_registry) == 1


# ---------------------------------------------------------------------------
# YAML persistence tests
# ---------------------------------------------------------------------------

def test_registry_yaml_round_trip(tmp_path: Path) -> None:
    """Data persisted to YAML is correctly reloaded by a second registry instance."""
    path = tmp_path / "ep.yml"
    reg1 = ExternalProjectRegistry(registry_path=path)
    reg1.add(ExternalProject(
        name="react",
        path="/home/user/react",
        languages=["typescript", "javascript"],
        registered_at="2026-04-18T00:00:00+00:00",
    ))

    reg2 = ExternalProjectRegistry(registry_path=path)
    proj = reg2.get("react")
    assert proj is not None
    assert proj.languages == ["typescript", "javascript"]


def test_registry_empty_yaml_no_crash(tmp_path: Path) -> None:
    """An empty YAML file should load without error."""
    path = tmp_path / "ep.yml"
    path.write_text("", encoding="utf-8")
    reg = ExternalProjectRegistry(registry_path=path)
    assert len(reg) == 0


def test_registry_missing_file_no_crash(tmp_path: Path) -> None:
    """A missing registry file should load cleanly (empty registry)."""
    reg = ExternalProjectRegistry(registry_path=tmp_path / "nonexistent.yml")
    assert len(reg) == 0
