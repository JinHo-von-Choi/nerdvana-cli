"""ACL admin mutations must reach disk, or fail loudly.

A revoke that only mutates process memory is worse than no revoke at all: the
operator is told the compromised key is gone while ``mcp_acl.yml`` still grants
it.  Every assertion here therefore round-trips through the filesystem with a
fresh ``ACLManager``, which is the only way to observe what a restarted server
would actually enforce.

Author: 최진호
Date:   2026-09-10
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nerdvana_cli.server.acl import ACLManager, ACLPersistenceError

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACL_CONTENT = (
    "roles:\n"
    "  read-only:\n"
    "    - symbol_overview\n"
    "  edit:\n"
    "    - replace_symbol_body\n"
    "clients:\n"
    "  claude-code-prod:\n"
    "    roles: [read-only]\n"
    "  cursor-dev:\n"
    "    roles: [read-only, edit]\n"
)


@pytest.fixture
def acl_file(tmp_path: Path) -> Path:
    path = tmp_path / "mcp_acl.yml"
    path.write_text(_ACL_CONTENT, encoding="utf-8")
    return path


def _reload(path: Path) -> ACLManager:
    """Return a brand-new manager reading *path* from scratch."""
    mgr = ACLManager(acl_path=path)
    mgr.load()
    return mgr


def _run_cli(args: list[str], tmp_path: Path) -> object:
    from nerdvana_cli.main import app

    runner = CliRunner()
    return runner.invoke(app, args, env={"NERDVANA_DATA_HOME": str(tmp_path / "data")})


def _tmp_leftovers(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.name.endswith(".tmp")]


# ---------------------------------------------------------------------------
# Persistence of mutations
# ---------------------------------------------------------------------------

class TestRevokePersists:
    def test_revoke_survives_reload(self, acl_file: Path) -> None:
        """Given a revoked client, when the ACL is re-read, it is gone."""
        mgr     = _reload(acl_file)
        removed = mgr.revoke("cursor")
        assert removed == ["cursor-dev"]

        fresh = _reload(acl_file)
        assert "cursor-dev" not in fresh.list_clients()
        assert fresh.check("cursor-dev", "replace_symbol_body").allowed is False
        # Untouched clients are preserved.
        assert "claude-code-prod" in fresh.list_clients()

    def test_revoke_via_cli_survives_reload(self, acl_file: Path, tmp_path: Path) -> None:
        result = _run_cli(
            ["admin", "acl", "revoke", "cursor", "--acl-file", str(acl_file)],
            tmp_path,
        )
        assert result.exit_code == 0                     # type: ignore[attr-defined]
        assert "Revoked" in result.output                # type: ignore[attr-defined]
        assert "Restart" in result.output                # type: ignore[attr-defined]
        assert "cursor-dev" not in _reload(acl_file).list_clients()

    def test_role_definitions_are_not_dropped(self, acl_file: Path) -> None:
        """Rewriting the clients section must not discard role overrides."""
        _reload(acl_file).revoke("cursor")

        document = yaml.safe_load(acl_file.read_text(encoding="utf-8"))
        assert document["roles"]["read-only"] == ["symbol_overview"]
        assert document["roles"]["edit"]      == ["replace_symbol_body"]


class TestAddClientPersists:
    def test_add_client_survives_reload(self, acl_file: Path) -> None:
        _reload(acl_file).add_client("new-bot", ["read-only", "edit"])

        fresh = _reload(acl_file)
        assert fresh.list_clients()["new-bot"] == ["read-only", "edit"]
        assert fresh.check("new-bot", "replace_symbol_body").allowed is True

    def test_add_client_creates_missing_file(self, tmp_path: Path) -> None:
        """A first-time assignment creates the file and its parent directory."""
        path = tmp_path / "nested" / "mcp_acl.yml"
        _reload(path).add_client("new-bot", ["read-only"])

        assert path.exists()
        assert _reload(path).list_clients() == {"new-bot": ["read-only"]}

    def test_add_client_via_cli_survives_reload(self, acl_file: Path, tmp_path: Path) -> None:
        result = _run_cli(
            ["admin", "acl", "add", "new-bot", "read-only,edit", "--acl-file", str(acl_file)],
            tmp_path,
        )
        assert result.exit_code == 0                     # type: ignore[attr-defined]
        assert "Restart" in result.output                # type: ignore[attr-defined]
        assert _reload(acl_file).list_clients()["new-bot"] == ["read-only", "edit"]


# ---------------------------------------------------------------------------
# Failure must not be reported as success
# ---------------------------------------------------------------------------

class TestWriteFailureIsLoud:
    @staticmethod
    def _break_replace(monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("nerdvana_cli.server.acl.os.replace", _boom)

    def test_revoke_raises_and_leaves_file_intact(
        self,
        acl_file:    Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = acl_file.read_bytes()
        mgr      = _reload(acl_file)
        self._break_replace(monkeypatch)

        with pytest.raises(ACLPersistenceError):
            mgr.revoke("cursor")

        assert acl_file.read_bytes() == original
        # In-memory state rolls back so it keeps matching what the server enforces.
        assert "cursor-dev" in mgr.list_clients()
        assert _tmp_leftovers(acl_file.parent) == []

    def test_add_client_raises_and_leaves_file_intact(
        self,
        acl_file:    Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = acl_file.read_bytes()
        mgr      = _reload(acl_file)
        self._break_replace(monkeypatch)

        with pytest.raises(ACLPersistenceError):
            mgr.add_client("new-bot", ["admin"])

        assert acl_file.read_bytes() == original
        assert "new-bot" not in mgr.list_clients()

    def test_cli_revoke_failure_exits_nonzero_without_success_message(
        self,
        acl_file:    Path,
        tmp_path:    Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._break_replace(monkeypatch)
        result = _run_cli(
            ["admin", "acl", "revoke", "cursor", "--acl-file", str(acl_file)],
            tmp_path,
        )

        assert result.exit_code != 0                     # type: ignore[attr-defined]
        assert "Revoked" not in result.output            # type: ignore[attr-defined]
        assert "cursor-dev" in _reload(acl_file).list_clients()

    def test_cli_add_failure_exits_nonzero_without_success_message(
        self,
        acl_file:    Path,
        tmp_path:    Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._break_replace(monkeypatch)
        result = _run_cli(
            ["admin", "acl", "add", "new-bot", "admin", "--acl-file", str(acl_file)],
            tmp_path,
        )

        assert result.exit_code != 0                     # type: ignore[attr-defined]
        assert "Updated" not in result.output            # type: ignore[attr-defined]
        assert "new-bot" not in _reload(acl_file).list_clients()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
    def test_read_only_directory_fails_loudly(self, acl_file: Path, tmp_path: Path) -> None:
        mgr = _reload(acl_file)
        acl_file.parent.chmod(0o500)
        try:
            result = _run_cli(
                ["admin", "acl", "revoke", "cursor", "--acl-file", str(acl_file)],
                tmp_path / "elsewhere",
            )
            assert result.exit_code != 0                 # type: ignore[attr-defined]
            assert "Revoked" not in result.output        # type: ignore[attr-defined]
        finally:
            acl_file.parent.chmod(0o700)

        assert "cursor-dev" in _reload(acl_file).list_clients()
        assert "cursor-dev" in mgr.list_clients()


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------

class TestBoundaries:
    def test_unknown_prefix_leaves_file_byte_identical(self, acl_file: Path) -> None:
        original = acl_file.read_bytes()
        assert _reload(acl_file).revoke("nonexistent-") == []
        assert acl_file.read_bytes() == original

    def test_unknown_prefix_via_cli_reports_no_match(self, acl_file: Path, tmp_path: Path) -> None:
        original = acl_file.read_bytes()
        result   = _run_cli(
            ["admin", "acl", "revoke", "nonexistent-", "--acl-file", str(acl_file)],
            tmp_path,
        )

        assert result.exit_code == 0                     # type: ignore[attr-defined]
        assert "Revoked" not in result.output            # type: ignore[attr-defined]
        assert acl_file.read_bytes() == original

    def test_missing_file_revoke_is_a_no_op(self, tmp_path: Path) -> None:
        path = tmp_path / "absent.yml"
        assert _reload(path).revoke("anything") == []
        assert not path.exists()

    def test_empty_clients_section(self, tmp_path: Path) -> None:
        """An empty ``clients:`` section is a valid starting point."""
        path = tmp_path / "mcp_acl.yml"
        path.write_text("roles:\n  read-only: [symbol_overview]\nclients:\n", encoding="utf-8")

        mgr = _reload(path)
        assert mgr.list_clients() == {}
        mgr.add_client("first-bot", ["read-only"])

        assert _reload(path).list_clients() == {"first-bot": ["read-only"]}

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "mcp_acl.yml"
        path.write_text("", encoding="utf-8")

        mgr = _reload(path)
        assert mgr.revoke("anything") == []
        mgr.add_client("first-bot", ["read-only"])

        assert _reload(path).list_clients() == {"first-bot": ["read-only"]}
