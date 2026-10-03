"""Pure mappings of the ACP agent: tool call fields, prompt content, MCP servers and launch settings.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("acp")

from acp.exceptions import RequestError
from acp.schema import (
    AudioContentBlock,
    EmbeddedResourceContentBlock,
    EnvVariable,
    HttpHeader,
    HttpMcpServer,
    ImageContentBlock,
    McpServerStdio,
    ResourceContentBlock,
    SseMcpServer,
    TextContentBlock,
    TextResourceContents,
)

from nerdvana_cli.acp.launch import LaunchError, LaunchOptions, settings_for
from nerdvana_cli.acp.mcp_servers import session_mcp_configs
from nerdvana_cli.acp.prompt_content import available_commands, expand_command, prompt_parts
from nerdvana_cli.acp.tool_mapping import (
    MAX_RESULT_CHARS,
    approval_key,
    plan_entries,
    result_content,
    tool_diff,
    tool_kind,
    tool_locations,
    tool_title,
)
from nerdvana_cli.core.context.user_commands import UserCommandLoader
from nerdvana_cli.core.images import MAX_IMAGES
from tests.acp.support import isolate


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("FileRead", "read"), ("FileWrite", "edit"), ("FileEdit", "edit"), ("safe_delete_symbol", "delete"),
        ("Grep", "search"), ("Bash", "execute"), ("WebFetch", "fetch"), ("mcp__server__tool", "other"),
    ],
)
def test_tool_kinds(name: str, kind: str) -> None:
    assert tool_kind(name) == kind


def test_locations_are_absolute_and_only_for_file_kinds(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    assert [loc.path for loc in tool_locations("FileRead", {"path": "src/a.py"}, cwd)] == [str(tmp_path / "src" / "a.py")]
    assert [loc.path for loc in tool_locations("replace_symbol_body", {"relative_path": "b.py"}, cwd)] == [str(tmp_path / "b.py")]
    assert [loc.path for loc in tool_locations("FileEdit", {"path": "/etc/hosts"}, cwd)] == ["/etc/hosts"]
    assert tool_locations("Grep", {"pattern": "x", "path": "."}, cwd) == []
    assert tool_locations("Bash", {"command": "ls"}, cwd) == []


def test_titles_name_the_verb_and_the_subject(tmp_path: Path) -> None:
    cwd = str(tmp_path)
    assert tool_title("Bash", {"command": "git status"}, cwd) == "Run git status"
    assert tool_title("FileEdit", {"path": str(tmp_path / "a" / "b.py")}, cwd) == "Edit a/b.py"
    assert tool_title("WebFetch", {"url": "https://example.com"}, cwd) == "Fetch https://example.com"
    assert tool_title("mcp__x__y", {}, cwd) == "mcp__x__y"
    long = tool_title("Bash", {"command": "echo " + "x" * 500}, cwd)
    assert len(long) == 120
    assert long.endswith("...")
    assert tool_title("Bash", {"command": "first\nsecond"}, cwd) == "Run first"


def test_a_write_and_an_edit_with_old_and_new_text_have_diffs(tmp_path: Path) -> None:
    cwd    = str(tmp_path)
    target = tmp_path / "a.txt"
    new    = tool_diff("FileWrite", {"path": "a.txt", "content": "new"}, cwd)[0]
    assert (new.path, new.old_text, new.new_text) == (str(target), None, "new")
    target.write_text("old", encoding="utf-8")
    replaced = tool_diff("FileWrite", {"path": "a.txt", "content": "new"}, cwd)[0]
    assert replaced.old_text == "old"
    edit = tool_diff("FileEdit", {"path": "a.txt", "old_string": "o", "new_string": "n"}, cwd)[0]
    assert (edit.old_text, edit.new_text) == ("o", "n")
    assert tool_diff("FileEdit", {"path": "a.txt", "anchor_hash": "1#abcdef", "new_string": "n"}, cwd) == []
    assert tool_diff("Bash", {"command": "ls"}, cwd) == []


def test_results_are_cut_and_empty_results_have_no_content() -> None:
    assert result_content("") == []
    assert result_content("ok")[0].content.text == "ok"
    cut = result_content("x" * (MAX_RESULT_CHARS + 50))[0].content.text
    assert cut.endswith("[output cut]")
    assert len(cut) < MAX_RESULT_CHARS + 20


def test_a_todo_list_becomes_plan_entries() -> None:
    entries = plan_entries({"todos": [
        {"content": "one", "status": "completed", "activeForm": "doing one"},
        {"content": "two", "status": "weird", "activeForm": "doing two"},
        {"content": "  ", "status": "pending", "activeForm": "x"},
        "not a todo",
    ]})
    assert [(e.content, e.status) for e in entries] == [("one", "completed"), ("two", "pending")]


def test_the_approval_key_is_the_tool_and_its_normalised_main_argument() -> None:
    assert approval_key("Bash", {"command": "  printenv   -0 "}) == ("Bash", "printenv -0")
    assert approval_key("WebSearch", {"query": "x"}) == ("WebSearch", "")


def test_prompt_blocks_become_text_and_images() -> None:
    blocks = [
        TextContentBlock(type="text", text="look at this"),
        ImageContentBlock(type="image", data="QUJD", mime_type="image/png"),
        ResourceContentBlock(type="resource_link", name="spec", uri="file:///a/spec.md"),
        EmbeddedResourceContentBlock(type="resource", resource=TextResourceContents(uri="file:///a/b.py", text="print(1)")),
        AudioContentBlock(type="audio", data="AAAA", mime_type="audio/wav"),
    ]
    text, images = prompt_parts(blocks)
    assert images == [{"type": "image", "media_type": "image/png", "data": "QUJD"}]
    assert text.splitlines()[0] == "look at this"
    assert "[spec](file:///a/spec.md)" in text
    assert '<context uri="file:///a/b.py">\nprint(1)\n</context>' in text
    assert "[audio not attached]" in text


def test_too_many_images_are_refused() -> None:
    blocks = [ImageContentBlock(type="image", data="QUJD", mime_type="image/png")] * (MAX_IMAGES + 1)
    with pytest.raises(RequestError) as refused:
        prompt_parts(blocks)
    assert refused.value.code == -32602


def test_slash_commands_expand_and_everything_else_passes_through(tmp_path: Path) -> None:
    commands = tmp_path / ".nerdvana" / "commands"
    commands.mkdir(parents=True)
    (commands / "fix.md").write_text("---\ndescription: Fix it\n---\nFix: $ARGUMENTS", encoding="utf-8")
    loader = UserCommandLoader(project_dir=str(tmp_path), global_dir=str(tmp_path / "none"))
    assert expand_command("/fix the parser", loader) == "Fix: the parser"
    assert expand_command("/unknown a b", loader) == "/unknown a b"
    assert expand_command("plain text", loader) == "plain text"
    listed = available_commands(loader)
    assert [(c.name, c.description, c.input.root.hint) for c in listed] == [("fix", "Fix it", "arguments")]


def test_the_editors_mcp_servers_join_and_override_the_project_configuration(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "kept":     {"command": "kept-server"},
        "replaced": {"command": "old-server"},
    }}), encoding="utf-8")
    servers = [
        McpServerStdio(name="replaced", command="new-server", args=["--x"], env=[EnvVariable(name="A", value="1")]),
        HttpMcpServer(type="http", name="remote", url="https://example.com/mcp", headers=[HttpHeader(name="Authorization", value="Bearer t")]),
        SseMcpServer(type="sse", name="events", url="https://example.com/sse", headers=[]),
    ]
    configs = session_mcp_configs(str(tmp_path), servers)
    assert configs["kept"].command == "kept-server"
    assert (configs["replaced"].command, configs["replaced"].args, configs["replaced"].env) == ("new-server", ["--x"], {"A": "1"})
    assert (configs["remote"].transport, configs["remote"].url, configs["remote"].headers) == ("http", "https://example.com/mcp", {"Authorization": "Bearer t"})
    assert configs["events"].transport == "sse"


def test_settings_for_applies_the_options_to_the_project_in_the_session_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate(monkeypatch, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "nerdvana.yml").write_text("session:\n  max_turns: 7\n", encoding="utf-8")
    settings = settings_for(LaunchOptions(model="claude-sonnet-5-5", approval_mode="yolo", set_values=["session.max_cost_usd=3"]), str(project))
    assert settings.cwd == str(project)
    assert settings.session.max_turns == 7
    assert settings.session.max_cost_usd == 3
    assert settings.session.default_mode == "one-shot"
    assert settings.model.model == "claude-sonnet-5-5"


def test_settings_for_refuses_a_bad_override_and_a_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate(monkeypatch, tmp_path)
    with pytest.raises(LaunchError) as bad:
        settings_for(LaunchOptions(set_values=["nonsense"]), str(tmp_path))
    assert not bad.value.auth_required
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(LaunchError) as missing:
        settings_for(LaunchOptions(), str(tmp_path))
    assert missing.value.auth_required
