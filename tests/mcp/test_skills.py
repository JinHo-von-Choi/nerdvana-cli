"""Skills over MCP: listing, verified loading, approval and limits.

Most tests drive the library against a stub server that serves a manifest and file contents, so each check
can bend one thing (a digest, a size, a field). A few run the whole path against tests/mcp/fake_server.py,
a real subprocess that declares the extension, through the manager, the skill loader and ActivateSkill.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from nerdvana_cli.core.skills import SkillLoader
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.manager import McpManager
from nerdvana_cli.mcp.skills import (
    MAX_SKILL_FILES,
    MAX_SKILL_TOTAL,
    McpSkillFileTool,
    McpSkillLibrary,
    SkillFile,
    parse_entry,
    split_skill_md,
    verify_bytes,
)
from nerdvana_cli.tools.skill_tool import ActivateSkillTool

SKILL_URI = "skill://code-review/SKILL.md"
SUPPORT   = "skill://code-review/references/checklist.md"
SKILL_MD  = "---\nname: code-review\ndescription: Review code.\n---\n\n# Code review\n\nRead the checklist.\n"
CHECKLIST = "# Checklist\n\nCheck tests.\n"
HERE      = Path(__file__).parent


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _file(uri: str, text: str) -> dict[str, Any]:
    return {"uri": uri, "digest": _digest(text), "size": len(text.encode())}


def _raw_entry(skill_md: str = SKILL_MD, **frontmatter: Any) -> dict[str, Any]:
    return {
        "uri":         SKILL_URI,
        "frontmatter": {"name": "code-review", "description": "Review code.", **frontmatter},
        "resources":   [_file(SKILL_URI, skill_md), _file(SUPPORT, CHECKLIST)],
    }


class StubClient:
    """What the library needs of a connected client: ``request`` and ``read_resource``."""

    def __init__(self, entries: list[dict[str, Any]], served: dict[str, str] | None = None) -> None:
        self.entries = entries
        self.served  = served if served is not None else {SKILL_URI: SKILL_MD, SUPPORT: CHECKLIST}
        self.reads: list[str]    = []
        self.requests: list[str] = []

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(method)
        if method == "skills/list":
            return {"skills": self.entries}
        return {"skill": self.entries[0]}

    async def read_resource(self, uri: str) -> list[dict[str, Any]]:
        self.reads.append(uri)
        return [{"uri": uri, "mimeType": "text/markdown", "text": self.served[uri]}]


async def _library(client: StubClient, server: str = "docs") -> McpSkillLibrary:
    library = McpSkillLibrary()
    await library.discover(server, client)  # type: ignore[arg-type]
    return library


async def _activate(library: McpSkillLibrary, context: ToolContext | None = None) -> Any:
    loader = SkillLoader(project_dir="/nonexistent", global_dir="/nonexistent", agents_global_dir="/nonexistent")
    loader.add_skills(library.skills())
    tool = ActivateSkillTool(loader)
    return await tool.call(tool.args_class(name=loader.model_skills()[0].name), context or ToolContext(), None)


class TestManifestParsing:
    def test_a_well_formed_entry_is_parsed(self) -> None:
        entry = parse_entry("docs", _raw_entry())

        assert entry.uri == SKILL_URI
        assert [f.uri for f in entry.files] == [SKILL_URI, SUPPORT]
        assert entry.root == "skill://code-review/"

    @pytest.mark.parametrize("mutate", [
        lambda e: e.update(resources="dynamic"),
        lambda e: e["resources"].pop(0),
        lambda e: e["resources"][1].update(digest="md5:abc"),
        lambda e: e["resources"][1].update(size=-1),
        lambda e: e["frontmatter"].pop("description"),
        lambda e: e.pop("frontmatter"),
    ])
    def test_a_malformed_entry_is_refused(self, mutate: Any) -> None:
        raw = _raw_entry()
        mutate(raw)

        with pytest.raises(ValueError):
            parse_entry("docs", raw)

    def test_more_files_than_the_limit_is_refused(self) -> None:
        raw = _raw_entry()
        raw["resources"] += [_file(f"skill://code-review/f{i}.md", str(i)) for i in range(MAX_SKILL_FILES)]

        with pytest.raises(ValueError, match="512"):
            parse_entry("docs", raw)

    def test_a_skill_over_the_size_limit_is_refused(self) -> None:
        raw = _raw_entry()
        raw["resources"][1]["size"] = MAX_SKILL_TOTAL

        with pytest.raises(ValueError):
            parse_entry("docs", raw)

    def test_a_skill_exactly_at_the_limits_is_accepted(self) -> None:
        raw = _raw_entry()
        raw["resources"] = [_file(SKILL_URI, SKILL_MD)]
        raw["resources"] += [{**_file(f"skill://code-review/f{i}.md", str(i)), "size": 0} for i in range(MAX_SKILL_FILES - 1)]
        raw["resources"][1]["size"] = MAX_SKILL_TOTAL - len(SKILL_MD.encode())

        assert len(parse_entry("docs", raw).files) == MAX_SKILL_FILES


class TestVerification:
    def test_size_and_digest_must_both_match(self) -> None:
        file = SkillFile(SUPPORT, _digest(CHECKLIST), len(CHECKLIST.encode()))

        verify_bytes(file, CHECKLIST.encode())
        with pytest.raises(RuntimeError, match="manifest says"):
            verify_bytes(file, CHECKLIST.encode() + b" ")
        with pytest.raises(RuntimeError, match="digest"):
            verify_bytes(file, b"x" * len(CHECKLIST.encode()))

    def test_frontmatter_and_body_are_split(self) -> None:
        frontmatter, body = split_skill_md(SKILL_MD)

        assert frontmatter == {"name": "code-review", "description": "Review code."}
        assert body.startswith("# Code review")

    def test_text_without_frontmatter_is_refused(self) -> None:
        with pytest.raises(RuntimeError, match="no frontmatter"):
            split_skill_md("# just text")


class TestCatalog:
    @pytest.mark.asyncio
    async def test_listing_reads_no_file(self) -> None:
        client  = StubClient([_raw_entry()])
        library = await _library(client)

        assert [s.name for s in library.skills()] == ["docs:code-review"]
        assert client.reads == []

    @pytest.mark.asyncio
    async def test_the_catalog_entry_names_its_server_and_slash_command(self) -> None:
        skill = (await _library(StubClient([_raw_entry()]))).skills()[0]

        assert skill.name == "docs:code-review"
        assert skill.trigger == "/docs:code-review"
        assert skill.description == "Review code."
        assert "ActivateSkill" in skill.body

    @pytest.mark.asyncio
    async def test_the_same_skill_name_on_two_servers_keeps_both(self) -> None:
        library = McpSkillLibrary()
        await library.discover("a", StubClient([_raw_entry()]))  # type: ignore[arg-type]
        await library.discover("b", StubClient([_raw_entry()]))  # type: ignore[arg-type]

        assert [s.name for s in library.skills()] == ["a:code-review", "b:code-review"]

    @pytest.mark.asyncio
    async def test_a_repeated_name_on_one_server_is_numbered(self) -> None:
        library = await _library(StubClient([_raw_entry(), _raw_entry()]))

        assert [s.name for s in library.skills()] == ["docs:code-review", "docs:code-review#2"]

    @pytest.mark.asyncio
    async def test_a_dynamic_or_malformed_entry_is_left_out(self) -> None:
        dynamic = {**_raw_entry(), "resources": "dynamic"}
        library = await _library(StubClient([dynamic, {"nonsense": True}, _raw_entry()]))

        assert [s.name for s in library.skills()] == ["docs:code-review"]

    @pytest.mark.asyncio
    async def test_a_local_skill_with_the_same_name_is_not_replaced(self) -> None:
        loader = SkillLoader(project_dir="/nonexistent", global_dir="/nonexistent", agents_global_dir="/nonexistent")
        library = await _library(StubClient([_raw_entry()]))
        loader.add_skills(library.skills())
        loader.add_skills(library.skills())

        assert [s.name for s in loader.list_skills()] == ["docs:code-review"]


class TestActivation:
    @pytest.mark.asyncio
    async def test_activation_fetches_only_skill_md_and_lists_the_supporting_files(self) -> None:
        client = StubClient([_raw_entry()])

        result = await _activate(await _library(client))

        assert result.is_error is False
        assert client.reads == [SKILL_URI]
        assert '<skill_content name="docs:code-review">' in result.content
        assert "# Code review" in result.content
        assert "mcp__skill_files__read" in result.content
        assert f"references/checklist.md ({len(CHECKLIST.encode())} bytes)" in result.content

    @pytest.mark.asyncio
    async def test_content_with_other_bytes_than_the_manifest_lists_is_not_used(self) -> None:
        client = StubClient([_raw_entry()], served={SKILL_URI: SKILL_MD.replace("Review", "Wreck!"), SUPPORT: CHECKLIST})

        result = await _activate(await _library(client))

        assert result.is_error is True
        assert "digest" in result.content
        assert "Wreck!" not in result.content
        assert client.requests == ["skills/list", "skills/get"]

    @pytest.mark.asyncio
    async def test_a_failed_load_can_be_retried(self) -> None:
        client  = StubClient([_raw_entry()], served={SKILL_URI: "tampered", SUPPORT: CHECKLIST})
        library = await _library(client)
        loader  = SkillLoader(project_dir="/nonexistent", global_dir="/nonexistent", agents_global_dir="/nonexistent")
        loader.add_skills(library.skills())
        tool = ActivateSkillTool(loader)

        first = await tool.call(tool.args_class(name="docs:code-review"), ToolContext(), None)
        client.served[SKILL_URI] = SKILL_MD
        second = await tool.call(tool.args_class(name="docs:code-review"), ToolContext(), None)

        assert first.is_error is True
        assert second.is_error is False

    @pytest.mark.asyncio
    async def test_frontmatter_that_differs_from_the_listing_is_refused(self) -> None:
        other = SKILL_MD.replace("Review code.", "Do something else.")
        raw   = _raw_entry()
        raw["resources"][0] = _file(SKILL_URI, other)
        client = StubClient([raw], served={SKILL_URI: other, SUPPORT: CHECKLIST})

        result = await _activate(await _library(client))

        assert result.is_error is True
        assert "differs" in result.content

    @pytest.mark.asyncio
    async def test_a_name_that_does_not_match_the_directory_is_refused(self) -> None:
        raw = _raw_entry()
        raw["uri"] = "skill://other-name/SKILL.md"
        raw["resources"][0]["uri"] = raw["uri"]
        client = StubClient([raw], served={raw["uri"]: SKILL_MD, SUPPORT: CHECKLIST})

        result = await _activate(await _library(client))

        assert result.is_error is True
        assert "directory" in result.content

    @pytest.mark.asyncio
    async def test_a_skill_md_over_the_size_cap_is_not_fetched(self) -> None:
        raw = _raw_entry()
        raw["resources"][0]["size"] = 1024 * 1024
        client = StubClient([raw])

        result = await _activate(await _library(client))

        assert result.is_error is True
        assert client.reads == []


class TestApproval:
    ALLOWED = {"allowed-tools": "Bash(git:*)"}

    @pytest.mark.asyncio
    async def test_a_skill_with_allowed_tools_is_refused_when_no_user_can_be_asked(self) -> None:
        client = StubClient([_raw_entry(**self.ALLOWED)])
        client.served[SKILL_URI] = SKILL_MD.replace("description: Review code.", "description: Review code.\nallowed-tools: Bash(git:*)")

        result = await _activate(await _library(client))

        assert result.is_error is True
        assert "no user is available" in result.content
        assert client.reads == []

    @pytest.mark.asyncio
    async def test_a_denied_skill_is_not_fetched(self) -> None:
        client  = StubClient([_raw_entry(**self.ALLOWED)])
        asked: list[str] = []

        async def ask(question: str, options: list[str]) -> str | None:
            asked.append(question)
            return "Deny"

        result = await _activate(await _library(client), ToolContext(ask_user=ask))

        assert result.is_error is True
        assert "did not approve" in result.content
        assert "docs" in asked[0] and "Bash(git:*)" in asked[0]
        assert client.reads == []

    @pytest.mark.asyncio
    async def test_an_approved_skill_loads_and_reports_its_allowed_tools(self) -> None:
        text = SKILL_MD.replace("description: Review code.", "description: Review code.\nallowed-tools: Bash(git:*)")
        raw  = _raw_entry(text, **self.ALLOWED)
        client = StubClient([raw], served={SKILL_URI: text, SUPPORT: CHECKLIST})

        async def ask(question: str, options: list[str]) -> str | None:
            return "Approve"

        result = await _activate(await _library(client), ToolContext(ask_user=ask))

        assert result.is_error is False
        assert "Bash(git:*)" in result.content

    @pytest.mark.asyncio
    async def test_a_skill_without_allowed_tools_asks_nothing(self) -> None:
        asked: list[str] = []

        async def ask(question: str, options: list[str]) -> str | None:
            asked.append(question)
            return "Approve"

        result = await _activate(await _library(StubClient([_raw_entry()])), ToolContext(ask_user=ask))

        assert result.is_error is False
        assert asked == []


class TestSupportingFiles:
    async def _loaded(self, client: StubClient) -> tuple[McpSkillLibrary, McpSkillFileTool]:
        library = await _library(client)
        await _activate(library)
        return library, McpSkillFileTool(library)

    async def _read(self, tool: McpSkillFileTool, path: str, skill: str = "docs:code-review") -> Any:
        return await tool.call(tool.args_class(skill=skill, path=path), ToolContext(), None)

    @pytest.mark.asyncio
    async def test_a_file_cannot_be_read_before_the_skill_is_activated(self) -> None:
        client = StubClient([_raw_entry()])
        tool   = McpSkillFileTool(await _library(client))

        result = await self._read(tool, "references/checklist.md")

        assert result.is_error is True
        assert "ActivateSkill" in result.content
        assert client.reads == []

    @pytest.mark.asyncio
    async def test_a_manifest_file_is_read_verified_and_tagged_with_its_origin(self) -> None:
        client = StubClient([_raw_entry()])
        _, tool = await self._loaded(client)

        result = await self._read(tool, "references/checklist.md")

        assert result.is_error is False
        assert result.content.startswith('<mcp_skill_file server="docs" skill="docs:code-review" path="references/checklist.md">')
        assert "Check tests." in result.content
        assert client.reads == [SKILL_URI, SUPPORT]

    @pytest.mark.asyncio
    async def test_the_full_uri_of_a_manifest_file_is_accepted(self) -> None:
        _, tool = await self._loaded(StubClient([_raw_entry()]))

        assert (await self._read(tool, SUPPORT)).is_error is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["../other/SKILL.md", "references/missing.md", "skill://other/SKILL.md", "/etc/passwd"])
    async def test_a_path_outside_the_manifest_is_refused_without_a_request(self, path: str) -> None:
        client = StubClient([_raw_entry()])
        _, tool = await self._loaded(client)

        result = await self._read(tool, path)

        assert result.is_error is True
        assert client.reads == [SKILL_URI]

    @pytest.mark.asyncio
    async def test_a_file_with_other_bytes_than_the_manifest_lists_is_refused(self) -> None:
        client = StubClient([_raw_entry()])
        _, tool = await self._loaded(client)
        client.served[SUPPORT] = "# Checklist\n\nCheck nothing\n"

        result = await self._read(tool, "references/checklist.md")

        assert result.is_error is True
        assert "Check nothing" not in result.content

    @pytest.mark.asyncio
    async def test_an_unknown_skill_is_refused(self) -> None:
        _, tool = await self._loaded(StubClient([_raw_entry()]))

        assert (await self._read(tool, "references/checklist.md", skill="docs:nope")).is_error is True


@pytest_asyncio.fixture
async def fake_manager(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[McpManager]:
    config  = McpServerConfig(name="fake", transport="stdio", command=sys.executable, args=[str(HERE / "fake_server.py")])
    manager = McpManager({"fake": config})
    status  = await manager.connect_all()
    assert status["fake"] == "connected (4 tools, 1 skills)"
    yield manager
    await manager.disconnect_all()


class TestThroughTheManager:
    @pytest.mark.asyncio
    async def test_the_skill_reaches_the_catalog_and_loads_from_the_real_server(self, fake_manager: McpManager) -> None:
        tools  = fake_manager.get_all_tools()
        reader = next(t for t in tools if isinstance(t, McpSkillFileTool))
        loader = SkillLoader(project_dir="/nonexistent", global_dir="/nonexistent", agents_global_dir="/nonexistent")
        loader.add_skills(reader.library.skills())
        activate = ActivateSkillTool(loader)

        loaded = await activate.call(activate.args_class(name="fake:code-review"), ToolContext(), None)
        file   = await reader.call(reader.args_class(skill="fake:code-review", path="references/checklist.md"), ToolContext(), None)

        assert activate.catalog_lines() == ["- fake:code-review: Review code using the team's checklist."]
        assert loaded.is_error is False and "Read `references/checklist.md`" in loaded.content
        assert file.is_error is False and "Check correctness, tests, and compatibility." in file.content

    @pytest.mark.asyncio
    async def test_the_reader_tool_is_registered_only_with_the_mcp_tools_of_a_server_that_has_skills(self) -> None:
        config  = McpServerConfig(name="raw", transport="stdio", command=sys.executable, args=[str(HERE / "raw_server.py"), "ok"])
        manager = McpManager({"raw": config})
        await manager.connect_all()
        try:
            assert not any(isinstance(t, McpSkillFileTool) for t in manager.get_all_tools())
        finally:
            await manager.disconnect_all()


class TestWholeRegistry:
    @pytest.mark.asyncio
    async def test_activate_skill_is_registered_for_mcp_skills_alone(self, fake_manager: McpManager, tmp_path: Path) -> None:
        from nerdvana_cli.core.settings import NerdvanaSettings
        from nerdvana_cli.tools.registry import create_tool_registry

        settings     = NerdvanaSettings()
        settings.cwd = str(tmp_path)
        registry     = create_tool_registry(mcp_tools=fake_manager.get_all_tools(), settings=settings)

        activate = registry.get("ActivateSkill")
        assert isinstance(activate, ActivateSkillTool)
        assert "fake:code-review" in [skill.name for skill in activate.loader.model_skills()]
        assert registry.get("mcp__skill_files__read") is not None


@pytest.mark.asyncio
async def test_tampered_content_from_a_real_server_is_not_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_TAMPER", SKILL_URI)
    config = McpServerConfig(name="fake", transport="stdio", command=sys.executable, args=[str(HERE / "fake_server.py")])
    client = McpClient(config)
    await client.connect()
    try:
        library = McpSkillLibrary()
        await library.discover("fake", client)
        result = await _activate(library)
    finally:
        await client.disconnect()

    assert result.is_error is True
    assert "tampered" not in result.content
