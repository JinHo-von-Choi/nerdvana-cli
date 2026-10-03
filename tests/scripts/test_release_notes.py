"""Release helper: CHANGELOG extraction and tag/version matching."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT   = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_notes.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_notes = _load()

SAMPLE = """# Changelog

## [1.2.0] - 2026-02-01

### Added

- Newer feature.

## [1.1.0] - 2026-01-01

### Fixed

- Older fix.

## [1.0.0] - 2025-12-01

- First.

[1.2.0]: https://example.invalid/compare/v1.1.0...v1.2.0
"""


def test_extracts_the_newest_section_without_its_heading() -> None:
    body = release_notes.extract_section(SAMPLE, "1.2.0")
    assert body is not None
    assert body.startswith("### Added")
    assert "Newer feature." in body
    assert "Older fix." not in body
    assert "## [1.2.0]" not in body


def test_extracts_a_middle_section_bounded_by_the_next_heading() -> None:
    body = release_notes.extract_section(SAMPLE, "1.1.0")
    assert body == "### Fixed\n\n- Older fix."


def test_last_section_stops_before_link_references() -> None:
    body = release_notes.extract_section(SAMPLE + "\n", "1.0.0")
    assert body == "- First."


def test_missing_version_returns_none() -> None:
    assert release_notes.extract_section(SAMPLE, "9.9.9") is None


def test_empty_section_returns_none() -> None:
    assert release_notes.extract_section("## [2.0.0] - 2026-03-01\n\n## [1.0.0]\n- x\n", "2.0.0") is None


def test_version_prefix_is_not_a_partial_match() -> None:
    assert release_notes.extract_section(SAMPLE, "1.2") is None


def test_tag_must_be_v_plus_version() -> None:
    assert release_notes.tag_matches("v1.5.0", "1.5.0")
    assert not release_notes.tag_matches("1.5.0", "1.5.0")
    assert not release_notes.tag_matches("v1.5.1", "1.5.0")


def test_project_version_matches_the_package() -> None:
    from nerdvana_cli import __version__

    assert release_notes.project_version() == __version__


def test_real_changelog_has_a_section_for_the_current_version() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_notes.extract_section(text, release_notes.project_version()) is not None
