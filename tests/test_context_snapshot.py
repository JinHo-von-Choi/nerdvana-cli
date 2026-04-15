"""Context snapshot collects project facts once per session."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nerdvana_cli.core.context_snapshot import collect_snapshot, format_snapshot


@pytest.fixture
def fake_py_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndescription = "A demo pkg"\n'
    )
    (tmp_path / "README.md").write_text("# Demo\n\n## Usage\n\n...\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("def main(): ...")
    (tmp_path / "tests").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


@pytest.mark.asyncio
async def test_snapshot_detects_python_project(fake_py_project: Path) -> None:
    snap = await collect_snapshot(str(fake_py_project))
    assert snap["project_type"] == "python"
    assert snap["project_name"] == "demo"


@pytest.mark.asyncio
async def test_snapshot_includes_tree(fake_py_project: Path) -> None:
    snap = await collect_snapshot(str(fake_py_project))
    assert "src" in snap["tree"]
    assert "tests" in snap["tree"]


@pytest.mark.asyncio
async def test_snapshot_includes_readme_headings(fake_py_project: Path) -> None:
    snap = await collect_snapshot(str(fake_py_project))
    assert "# Demo" in snap["readme_headings"]
    assert "## Usage" in snap["readme_headings"]


def test_format_snapshot_renders_markdown(fake_py_project: Path) -> None:
    snap = {
        "project_type": "python",
        "project_name": "demo",
        "tree": "src/\ntests/",
        "readme_headings": "# Demo",
        "entry_points": ["src/demo.py"],
    }
    text = format_snapshot(snap)
    assert "# Project Snapshot" in text
    assert "python" in text
    assert "demo" in text
    assert "src/" in text
