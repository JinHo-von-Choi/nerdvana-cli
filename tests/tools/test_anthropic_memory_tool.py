"""Anthropic memory tool adapter: the six commands, path checks, size caps and registration.

Return strings follow the memory tool page of the Anthropic documentation.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from nerdvana_cli.core.config import paths as core_paths
from nerdvana_cli.core.config.settings import ModelConfig, NerdvanaSettings
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.providers.anthropic_features import declare_tools
from nerdvana_cli.tools import anthropic_memory_tool as memory
from nerdvana_cli.tools.anthropic_memory_tool import (
    AnthropicMemoryTool,
    MemoryStore,
    MemoryToolError,
    create_memory_tool,
)
from nerdvana_cli.tools.registry import create_tool_registry


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "store"


@pytest.fixture
def store(root: Path) -> MemoryStore:
    return MemoryStore(root)


def run(store: MemoryStore, **args: Any) -> str:
    return store.execute(args)


def refused(store: MemoryStore, **args: Any) -> str:
    with pytest.raises(MemoryToolError) as caught:
        store.execute(args)
    return str(caught.value)


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------


def test_viewing_an_empty_store_lists_the_directory_itself(store: MemoryStore) -> None:
    assert run(store, command="view", path="/memories") == (
        "Here're the files and directories up to 2 levels deep in /memories, excluding hidden items and node_modules:\n0B\t/memories"
    )


def test_the_listing_shows_sizes_two_levels_deep_and_leaves_out_hidden_items_and_node_modules(store: MemoryStore, root: Path) -> None:
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "notes.txt").write_text("x" * 2048)
    (root / "a" / "plan.md").write_text("hello")
    (root / "a" / "b" / "deep.md").write_text("deep")
    (root / "a" / "b" / "c" / "deeper.md").write_text("deeper")
    (root / ".hidden").write_text("h")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "m.js").write_text("m")
    (root / "a" / ".secret").write_text("s")

    lines = run(store, command="view", path="/memories").splitlines()

    assert lines[0].startswith("Here're the files and directories up to 2 levels deep in /memories")
    assert [line.split("\t")[1] for line in lines[1:]] == ["/memories", "/memories/a", "/memories/a/b", "/memories/a/plan.md", "/memories/notes.txt"]
    sizes = {line.split("\t")[1]: line.split("\t")[0] for line in lines[1:]}
    assert sizes["/memories/notes.txt"] == "2.0K"
    assert sizes["/memories/a/plan.md"] == "5B"


def test_viewing_a_file_numbers_its_lines_with_a_six_wide_column_and_a_tab(store: MemoryStore, root: Path) -> None:
    root.mkdir()
    (root / "n.txt").write_text("Hello World\nThis is line two\n")
    assert run(store, command="view", path="/memories/n.txt") == (
        "Here's the content of /memories/n.txt with line numbers:\n     1\tHello World\n     2\tThis is line two"
    )


def test_a_view_range_returns_those_lines_and_minus_one_means_the_end(store: MemoryStore, root: Path) -> None:
    root.mkdir()
    (root / "n.txt").write_text("\n".join(f"line {i}" for i in range(1, 11)))
    assert run(store, command="view", path="/memories/n.txt", view_range=[3, 4]).splitlines()[1:] == ["     3\tline 3", "     4\tline 4"]
    assert run(store, command="view", path="/memories/n.txt", view_range=[9, -1]).splitlines()[1:] == ["     9\tline 9", "    10\tline 10"]


@pytest.mark.parametrize("view_range", [[0, 3], [5, 2], [1], "1-3", [1, "x"], [20, 30]])
def test_a_bad_view_range_is_refused(store: MemoryStore, root: Path, view_range: Any) -> None:
    root.mkdir()
    (root / "n.txt").write_text("a\nb\nc")
    assert refused(store, command="view", path="/memories/n.txt", view_range=view_range).startswith("Error: ")


def test_a_long_file_is_cut_with_a_note_that_names_view_range(store: MemoryStore, root: Path) -> None:
    root.mkdir()
    (root / "big.txt").write_text("\n".join("x" * 80 for _ in range(1000)))
    text = run(store, command="view", path="/memories/big.txt")
    assert len(text) < memory.MAX_VIEW_CHARS + 200
    assert text.endswith("read the rest with view_range]")


def test_viewing_a_missing_path_is_the_documented_error(store: MemoryStore) -> None:
    assert refused(store, command="view", path="/memories/none.txt") == "The path /memories/none.txt does not exist. Please provide a valid path."


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_writes_the_file_and_the_directories_above_it(store: MemoryStore, root: Path) -> None:
    assert run(store, command="create", path="/memories/a/b/notes.txt", file_text="Meeting notes:\n- one\n") == "File created successfully at: /memories/a/b/notes.txt"
    assert (root / "a" / "b" / "notes.txt").read_text() == "Meeting notes:\n- one\n"


def test_create_does_not_overwrite(store: MemoryStore) -> None:
    run(store, command="create", path="/memories/n.txt", file_text="one")
    assert refused(store, command="create", path="/memories/n.txt", file_text="two") == "Error: File /memories/n.txt already exists"
    assert "one" in run(store, command="view", path="/memories/n.txt")


def test_create_cannot_replace_the_root(store: MemoryStore) -> None:
    assert refused(store, command="create", path="/memories", file_text="x") == "Error: File /memories already exists"


def test_a_missing_field_names_it(store: MemoryStore) -> None:
    assert refused(store, command="create", path="/memories/n.txt") == "Error: `file_text` is required for create"


# ---------------------------------------------------------------------------
# str_replace
# ---------------------------------------------------------------------------


def test_str_replace_edits_the_file_and_shows_a_numbered_snippet(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/p.txt", file_text="a\nFavorite color: blue\nz\n")
    out = run(store, command="str_replace", path="/memories/p.txt", old_str="Favorite color: blue", new_str="Favorite color: green")
    assert out.startswith("The memory file has been edited.")
    assert "     2\tFavorite color: green" in out
    assert (root / "p.txt").read_text() == "a\nFavorite color: green\nz\n"


def test_str_replace_without_new_str_deletes_the_text(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/p.txt", file_text="keep drop keep")
    run(store, command="str_replace", path="/memories/p.txt", old_str=" drop")
    assert (root / "p.txt").read_text() == "keep keep"


def test_str_replace_reports_text_that_is_not_there(store: MemoryStore) -> None:
    run(store, command="create", path="/memories/p.txt", file_text="abc")
    assert refused(store, command="str_replace", path="/memories/p.txt", old_str="xyz", new_str="q") == (
        "No replacement was performed, old_str `xyz` did not appear verbatim in /memories/p.txt."
    )


def test_str_replace_reports_every_line_of_an_ambiguous_match(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/p.txt", file_text="dup\nother\ndup\n")
    assert refused(store, command="str_replace", path="/memories/p.txt", old_str="dup", new_str="x") == (
        "No replacement was performed. Multiple occurrences of old_str `dup` in lines: 1, 3. Please ensure it is unique"
    )
    assert (root / "p.txt").read_text() == "dup\nother\ndup\n"


def test_str_replace_on_a_missing_file_or_a_directory_is_the_does_not_exist_error(store: MemoryStore, root: Path) -> None:
    (root / "d").mkdir(parents=True)
    for path in ("/memories/none.txt", "/memories/d"):
        assert refused(store, command="str_replace", path=path, old_str="a", new_str="b") == f"Error: The path {path} does not exist. Please provide a valid path."


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def test_insert_places_text_after_the_given_line(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/todo.txt", file_text="one\ntwo\nthree\n")
    assert run(store, command="insert", path="/memories/todo.txt", insert_line=2, insert_text="- inserted\n") == "The file /memories/todo.txt has been edited."
    assert (root / "todo.txt").read_text() == "one\ntwo\n- inserted\nthree\n"


def test_insert_line_zero_inserts_at_the_start_and_the_last_line_number_at_the_end(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/t.txt", file_text="a\nb\n")
    run(store, command="insert", path="/memories/t.txt", insert_line=0, insert_text="first")
    run(store, command="insert", path="/memories/t.txt", insert_line=3, insert_text="last")
    assert (root / "t.txt").read_text() == "first\na\nb\nlast\n"


def test_insert_into_an_empty_file(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/t.txt", file_text="")
    run(store, command="insert", path="/memories/t.txt", insert_line=0, insert_text="x")
    assert (root / "t.txt").read_text() == "x\n"


@pytest.mark.parametrize("line", [-1, 3, 99, "2", True])
def test_insert_refuses_a_line_outside_the_file(store: MemoryStore, line: Any) -> None:
    run(store, command="create", path="/memories/t.txt", file_text="a\nb\n")
    assert refused(store, command="insert", path="/memories/t.txt", insert_line=line, insert_text="x") == (
        f"Error: Invalid `insert_line` parameter: {line}. It should be within the range of lines of the file: [0, 2]"
    )


def test_insert_on_a_missing_file_is_the_documented_error(store: MemoryStore) -> None:
    assert refused(store, command="insert", path="/memories/none.txt", insert_line=0, insert_text="x") == "Error: The path /memories/none.txt does not exist"


# ---------------------------------------------------------------------------
# delete and rename
# ---------------------------------------------------------------------------


def test_delete_removes_a_file_and_a_directory_with_its_contents(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/old.txt", file_text="x")
    run(store, command="create", path="/memories/d/a.txt", file_text="x")
    assert run(store, command="delete", path="/memories/old.txt") == "Successfully deleted /memories/old.txt"
    assert run(store, command="delete", path="/memories/d") == "Successfully deleted /memories/d"
    assert list(root.iterdir()) == []


def test_delete_refuses_the_root_and_a_missing_path(store: MemoryStore) -> None:
    assert refused(store, command="delete", path="/memories") == "Error: The /memories directory itself cannot be deleted"
    assert refused(store, command="delete", path="/memories/none") == "Error: The path /memories/none does not exist"


def test_rename_moves_a_file_and_a_directory(store: MemoryStore, root: Path) -> None:
    run(store, command="create", path="/memories/draft.txt", file_text="x")
    assert run(store, command="rename", old_path="/memories/draft.txt", new_path="/memories/sub/final.txt") == (
        "Successfully renamed /memories/draft.txt to /memories/sub/final.txt"
    )
    assert (root / "sub" / "final.txt").read_text() == "x"
    run(store, command="rename", old_path="/memories/sub", new_path="/memories/moved")
    assert (root / "moved" / "final.txt").exists()


def test_rename_does_not_overwrite_and_needs_a_source(store: MemoryStore) -> None:
    run(store, command="create", path="/memories/a.txt", file_text="1")
    run(store, command="create", path="/memories/b.txt", file_text="2")
    assert refused(store, command="rename", old_path="/memories/a.txt", new_path="/memories/b.txt") == "Error: The destination /memories/b.txt already exists"
    assert refused(store, command="rename", old_path="/memories/none", new_path="/memories/c.txt") == "Error: The path /memories/none does not exist"


def test_rename_refuses_the_root_and_a_directory_into_itself(store: MemoryStore) -> None:
    run(store, command="create", path="/memories/d/a.txt", file_text="1")
    assert refused(store, command="rename", old_path="/memories", new_path="/memories/x") == "Error: The /memories directory itself cannot be renamed"
    assert refused(store, command="rename", old_path="/memories/d", new_path="/memories/d/inner").startswith("Error: rename failed")


def test_an_unknown_command_is_refused(store: MemoryStore) -> None:
    assert refused(store, command="chmod", path="/memories/a") == "Error: unknown command chmod"


# ---------------------------------------------------------------------------
# Paths that must not get out
# ---------------------------------------------------------------------------

ESCAPES = [
    "/memories/../outside.txt",
    "/memories/a/../../outside.txt",
    "/memories/..",
    "/memories/%2e%2e/outside.txt",
    "/memories/%2E%2E%2Foutside.txt",
    "/memories/..%2foutside.txt",
    "/memories/%252e%252e%252foutside.txt",
    "/memories/%25252e%25252e%25252foutside.txt",
    "/memories/..\\outside.txt",
    "/memories/a\\..\\..\\outside.txt",
    "/memories/%5c..%5coutside.txt",
    "/memories/a\x00b",
    "/memories/%00",
    "/memories-evil/x.txt",
    "/memoriesx",
    "/etc/passwd",
    "memories/x.txt",
    "/",
    "../outside.txt",
    "/memories/" + "a/" * 400,
]


@pytest.mark.parametrize("path", ESCAPES)
@pytest.mark.parametrize("command", ["view", "create", "str_replace", "insert", "delete"])
def test_a_path_that_leaves_memories_is_refused_by_every_command(store: MemoryStore, root: Path, command: str, path: str) -> None:
    args = {"command": command, "path": path, "file_text": "x", "old_str": "a", "new_str": "b", "insert_line": 0, "insert_text": "x"}
    message = refused(store, **args)
    assert "is not a valid path inside /memories" in message
    assert not (root.parent / "outside.txt").exists()


@pytest.mark.parametrize("path", ESCAPES[:6])
def test_rename_checks_both_ends(store: MemoryStore, root: Path, path: str) -> None:
    run(store, command="create", path="/memories/a.txt", file_text="1")
    assert "is not a valid path" in refused(store, command="rename", old_path="/memories/a.txt", new_path=path)
    assert "is not a valid path" in refused(store, command="rename", old_path=path, new_path="/memories/b.txt")
    assert (root / "a.txt").exists()


def test_a_symlink_that_points_out_of_the_root_is_not_followed(store: MemoryStore, root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "key.txt").write_text("TOP SECRET")
    root.mkdir()
    (root / "link").symlink_to(secret, target_is_directory=True)
    (root / "flink.txt").symlink_to(secret / "key.txt")
    for path in ("/memories/link/key.txt", "/memories/link", "/memories/flink.txt"):
        assert "is not a valid path" in refused(store, command="view", path=path)
    assert "is not a valid path" in refused(store, command="create", path="/memories/link/new.txt", file_text="x")
    assert "is not a valid path" in refused(store, command="delete", path="/memories/link")
    assert (secret / "key.txt").read_text() == "TOP SECRET"


def test_a_name_that_merely_contains_dots_or_a_percent_sign_is_allowed(store: MemoryStore) -> None:
    for name in ("/memories/v1..2.txt", "/memories/100%.txt", "/memories/a%20b.txt", "/memories/.config"):
        assert run(store, command="create", path=name, file_text="x") == f"File created successfully at: {name}"


# ---------------------------------------------------------------------------
# Size caps and secrets
# ---------------------------------------------------------------------------


def test_a_file_over_the_cap_is_refused(store: MemoryStore) -> None:
    message = refused(store, command="create", path="/memories/big.txt", file_text="x" * (memory.MAX_FILE_BYTES + 1))
    assert f"the limit is {memory.MAX_FILE_BYTES}" in message


def test_the_cap_counts_bytes_not_characters(store: MemoryStore) -> None:
    assert "the limit is" in refused(store, command="create", path="/memories/big.txt", file_text="가" * (memory.MAX_FILE_BYTES // 3 + 1))


def test_an_edit_that_grows_a_file_past_the_cap_is_refused_and_leaves_it_unchanged(store: MemoryStore, root: Path) -> None:
    text = "head " + "x" * (memory.MAX_FILE_BYTES - 10)
    run(store, command="create", path="/memories/n.txt", file_text=text)
    assert "the limit is" in refused(store, command="insert", path="/memories/n.txt", insert_line=0, insert_text="y" * 50)
    assert "the limit is" in refused(store, command="str_replace", path="/memories/n.txt", old_str="head", new_str="y" * 100)
    assert (root / "n.txt").read_text() == text


def test_the_store_as_a_whole_is_capped(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory, "MAX_TOTAL_BYTES", 100)
    run(store, command="create", path="/memories/a.txt", file_text="head " + "x" * 55)
    assert "the memory store is full" in refused(store, command="create", path="/memories/b.txt", file_text="x" * 60)
    run(store, command="str_replace", path="/memories/a.txt", old_str="head", new_str="y" * 30)


def test_the_number_of_files_is_capped(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory, "MAX_FILES", 2)
    run(store, command="create", path="/memories/a.txt", file_text="1")
    run(store, command="create", path="/memories/b.txt", file_text="1")
    assert "the memory store is full" in refused(store, command="create", path="/memories/c.txt", file_text="1")
    run(store, command="delete", path="/memories/a.txt")
    run(store, command="create", path="/memories/c.txt", file_text="1")


def test_content_that_looks_like_a_secret_is_not_stored(store: MemoryStore, root: Path) -> None:
    key = "sk-" + "a" * 40
    assert "looks like it holds a secret" in refused(store, command="create", path="/memories/k.txt", file_text=f"key {key}")
    run(store, command="create", path="/memories/k.txt", file_text="ok")
    assert "looks like it holds a secret" in refused(store, command="insert", path="/memories/k.txt", insert_line=0, insert_text=key)
    assert "looks like it holds a secret" in refused(store, command="str_replace", path="/memories/k.txt", old_str="ok", new_str=key)
    assert (root / "k.txt").read_text() == "ok"


def test_a_file_that_is_not_text_is_reported(store: MemoryStore, root: Path) -> None:
    root.mkdir()
    (root / "b.bin").write_bytes(b"\xff\xfe\x00bad")
    assert refused(store, command="view", path="/memories/b.bin") == "Error: /memories/b.bin is not a UTF-8 text file"


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


async def test_the_tool_returns_results_and_marks_refusals_as_errors(root: Path) -> None:
    tool = AnthropicMemoryTool(root)
    ok   = await tool.call({"command": "create", "path": "/memories/a.txt", "file_text": "x"}, ToolContext(), None)
    bad  = await tool.call({"command": "view", "path": "/memories/../x"}, ToolContext(), None)
    assert (ok.is_error, ok.content) == (False, "File created successfully at: /memories/a.txt")
    assert bad.is_error is True
    assert "is not a valid path inside /memories" in bad.content


def test_the_tool_is_a_filesystem_write_tool_named_memory(root: Path) -> None:
    tool = AnthropicMemoryTool(root)
    assert tool.name == "memory"
    assert tool.category.value == "write"
    assert tool.side_effects.value == "filesystem"
    assert tool.is_concurrency_safe is False
    assert set(tool.input_schema["properties"]) >= {"command", "path", "view_range", "file_text", "old_str", "new_str", "insert_line", "insert_text", "old_path", "new_path"}


def test_claude_models_get_the_native_declaration_and_others_the_function_declaration(root: Path) -> None:
    tool = AnthropicMemoryTool(root)
    assert declare_tools([tool], "claude-sonnet-5-5")[0] == {"type": "memory_20250818", "name": "memory"}
    plain = declare_tools([tool], "MiniMax-M2")[0]
    assert plain["name"] == "memory"
    assert "type" not in plain
    assert plain["input_schema"] == tool.input_schema


# ---------------------------------------------------------------------------
# Registration and the directory
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path, **model: Any) -> NerdvanaSettings:
    settings     = NerdvanaSettings(model=ModelConfig(**model))
    settings.cwd = str(tmp_path / "project")
    return settings


def test_the_setting_is_off_by_default(tmp_path: Path) -> None:
    assert ModelConfig().anthropic_memory_tool is False
    assert create_memory_tool(_settings(tmp_path)) is None


def test_the_tool_is_created_for_an_anthropic_model_when_the_setting_is_on(tmp_path: Path) -> None:
    assert create_memory_tool(_settings(tmp_path, anthropic_memory_tool=True)) is not None
    assert create_memory_tool(_settings(tmp_path, provider="anthropic", model="claude-opus-5-5", anthropic_memory_tool=True)) is not None


@pytest.mark.parametrize(("provider", "model"), [("openai", "gpt-5.6"), ("gemini", "gemini-3.6-flash"), ("", "gpt-4.1"), ("groq", "llama-3.3-70b")])
def test_no_tool_is_created_for_another_provider(tmp_path: Path, provider: str, model: str) -> None:
    assert create_memory_tool(_settings(tmp_path, provider=provider, model=model, anthropic_memory_tool=True)) is None


def test_a_settings_stub_never_turns_the_tool_on() -> None:
    assert create_memory_tool(MagicMock()) is None
    assert create_memory_tool(SimpleNamespace()) is None


def test_the_registry_holds_the_tool_only_when_it_is_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    assert create_tool_registry(settings=_settings(tmp_path)).get("memory") is None
    assert create_tool_registry(settings=_settings(tmp_path, anthropic_memory_tool=True)).get("memory") is not None
    assert create_tool_registry(settings=_settings(tmp_path, provider="openai", model="gpt-5.6", anthropic_memory_tool=True)).get("memory") is None


def test_each_project_gets_its_own_directory_under_the_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    one = core_paths.project_memory_tool_dir(str(tmp_path / "one"))
    two = core_paths.project_memory_tool_dir(str(tmp_path / "two"))
    assert one != two
    assert one == core_paths.project_memory_tool_dir(str(tmp_path / "one" / "."))
    assert core_paths.user_data_home() in one.parents
    assert one.name.startswith("one-")


def test_files_written_through_the_registered_tool_land_in_that_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    settings = _settings(tmp_path, anthropic_memory_tool=True)
    tool = create_tool_registry(settings=settings).get("memory")
    assert tool is not None
    asyncio.run(tool.call({"command": "create", "path": "/memories/n.txt", "file_text": "hi"}, ToolContext(), None))
    assert (core_paths.project_memory_tool_dir(settings.cwd) / "n.txt").read_text() == "hi"
