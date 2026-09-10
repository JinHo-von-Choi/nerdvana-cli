"""Project-local hook execution must require explicit approval.

Cloning a repository places `<cwd>/.nerdvana/hooks/*.py` inside the
working tree. Importing such a file executes it, so the loader has to
refuse until the user opts in and the exact file contents are approved.
Global hooks under the user's own data directory keep running freely.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nerdvana_cli.core import paths, user_hooks
from nerdvana_cli.core.hooks import HookContext, HookEngine, HookEvent
from nerdvana_cli.core.user_hooks import load_user_hooks, trust_project_hook

pytestmark = pytest.mark.security


HOOK_SOURCE = textwrap.dedent(
    '''
    from pathlib import Path

    from nerdvana_cli.core.hooks import HookEvent, HookResult

    Path(__file__).with_name("{sentinel}").write_text("{marker}")


    def _handler(ctx):
        return HookResult(system_prompt_append="{marker}")


    def register(engine, settings):
        engine.register(HookEvent.SESSION_START, _handler)
    '''
)


class _StubHookConfig:
    def __init__(self, allow_project_hooks: bool) -> None:
        self.allow_project_hooks = allow_project_hooks


class _StubSettings:
    """Mirrors the shape read by the loader: `cwd` plus a hooks config."""

    def __init__(self, cwd: Path, allow_project_hooks: bool = False) -> None:
        self.cwd   = str(cwd)
        self.hooks = _StubHookConfig(allow_project_hooks)


class _LegacySettings:
    """Settings object predating the opt-in field."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = str(cwd)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global data root at a scratch directory."""
    data_home = tmp_path / "data-home"
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return data_home


def _write_hook(directory: Path, stem: str, marker: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    sentinel = f"{stem}.fired"
    hook     = directory / f"{stem}.py"
    hook.write_text(HOOK_SOURCE.format(sentinel=sentinel, marker=marker))
    return hook, directory / sentinel


def _project_hook(project: Path, stem: str, marker: str) -> tuple[Path, Path]:
    return _write_hook(project / ".nerdvana" / "hooks", stem, marker)


def test_untrusted_project_hook_is_not_executed(
    tmp_path: Path, isolated_home: Path
) -> None:
    """A freshly cloned repository's hook must not run at all."""
    project             = tmp_path / "cloned-repo"
    hook_file, sentinel = _project_hook(project, "payload", "pwned")

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project))

    assert loaded == []
    assert not sentinel.exists(), "hook module body executed without approval"
    assert not engine.has_handlers(HookEvent.SESSION_START)
    assert hook_file.exists()


def test_settings_without_optin_field_block_project_hooks(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Absence of the opt-in attribute is read as disabled, not enabled."""
    project      = tmp_path / "legacy-repo"
    _, sentinel  = _project_hook(project, "legacy_payload", "pwned")

    engine = HookEngine()
    loaded = load_user_hooks(engine, _LegacySettings(project))

    assert loaded == []
    assert not sentinel.exists()


def test_optin_without_recorded_digest_blocks_project_hook(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Opting in is not sufficient: the file itself must be approved."""
    project     = tmp_path / "optin-repo"
    _, sentinel = _project_hook(project, "unapproved", "pwned")

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project, allow_project_hooks=True))

    assert loaded == []
    assert not sentinel.exists()


def test_optin_with_recorded_digest_runs_project_hook(
    tmp_path: Path, isolated_home: Path
) -> None:
    """The approved path still works end to end."""
    project             = tmp_path / "trusted-repo"
    hook_file, sentinel = _project_hook(project, "approved", "from-project-hook")

    digest = trust_project_hook(hook_file)
    assert digest == user_hooks.hook_digest(hook_file)
    assert user_hooks.project_hook_trust_path() == isolated_home / "trusted_hooks.json"

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project, allow_project_hooks=True))

    assert loaded == [str(hook_file)]
    assert sentinel.read_text() == "from-project-hook"

    results = engine.fire(HookContext(event=HookEvent.SESSION_START))
    assert [r.system_prompt_append for r in results] == ["from-project-hook"]


def test_modified_project_hook_loses_its_approval(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Approval is bound to bytes: an edited file is refused again."""
    project             = tmp_path / "tampered-repo"
    hook_file, sentinel = _project_hook(project, "swapped", "original")
    trust_project_hook(hook_file)

    hook_file.write_text(HOOK_SOURCE.format(sentinel="swapped.fired", marker="tampered"))

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project, allow_project_hooks=True))

    assert loaded == []
    assert not sentinel.exists()
    assert not engine.has_handlers(HookEvent.SESSION_START)


def test_revoking_approval_blocks_a_previously_trusted_hook(
    tmp_path: Path, isolated_home: Path
) -> None:
    project             = tmp_path / "revoked-repo"
    hook_file, sentinel = _project_hook(project, "revoked", "gone")
    trust_project_hook(hook_file)

    assert user_hooks.revoke_project_hook(hook_file) is True
    assert user_hooks.revoke_project_hook(hook_file) is False

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project, allow_project_hooks=True))

    assert loaded == []
    assert not sentinel.exists()


def test_global_hooks_run_without_optin_or_digest(
    tmp_path: Path, isolated_home: Path
) -> None:
    """The gate covers project hooks only; the user's own hooks are untouched."""
    global_dir          = paths.user_hooks_dir()
    hook_file, sentinel = _write_hook(global_dir, "global_hook", "from-global-hook")

    project = tmp_path / "plain-repo"
    project.mkdir()

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project))

    assert loaded == [str(hook_file)]
    assert sentinel.read_text() == "from-global-hook"

    results = engine.fire(HookContext(event=HookEvent.SESSION_START))
    assert [r.system_prompt_append for r in results] == ["from-global-hook"]


def test_malformed_trust_record_approves_nothing(
    tmp_path: Path, isolated_home: Path
) -> None:
    project     = tmp_path / "broken-record-repo"
    _, sentinel = _project_hook(project, "broken", "pwned")

    record = user_hooks.project_hook_trust_path()
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("not json at all")

    engine = HookEngine()
    loaded = load_user_hooks(engine, _StubSettings(project, allow_project_hooks=True))

    assert loaded == []
    assert not sentinel.exists()
