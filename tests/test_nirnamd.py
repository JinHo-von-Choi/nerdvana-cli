"""Tests for NIRNA.md loading."""


from nerdvana_cli.core.nirnamd import NirnaFile, format_nirna_for_prompt, load_nirna_files


class TestNirnaMdLoading:
    def test_no_files_returns_empty(self, tmp_path):
        files = load_nirna_files(cwd=str(tmp_path), global_path=str(tmp_path / "nonexistent"))
        assert files == []

    def test_project_nirnamd(self, tmp_path):
        (tmp_path / "NIRNA.md").write_text("# Project Rules\nUse tabs.")
        files = load_nirna_files(cwd=str(tmp_path), global_path=str(tmp_path / "nonexistent"))
        assert len(files) == 1
        assert files[0].type == "project"
        assert "Use tabs" in files[0].content

    def test_local_nirnamd(self, tmp_path):
        (tmp_path / "NIRNA.local.md").write_text("# My Prefs\nBe terse.")
        files = load_nirna_files(cwd=str(tmp_path), global_path=str(tmp_path / "nonexistent"))
        assert len(files) == 1
        assert files[0].type == "local"

    def test_priority_order(self, tmp_path):
        (tmp_path / "NIRNA.md").write_text("project rules")
        (tmp_path / "NIRNA.local.md").write_text("local rules")
        files = load_nirna_files(cwd=str(tmp_path), global_path=str(tmp_path / "nonexistent"))
        assert len(files) == 2
        assert files[0].type == "project"
        assert files[1].type == "local"

    def test_global_nirnamd(self, tmp_path):
        global_file = tmp_path / "global_nirna.md"
        global_file.write_text("global rules")
        files = load_nirna_files(cwd=str(tmp_path), global_path=str(global_file))
        assert len(files) == 1
        assert files[0].type == "global"

    def test_all_three_files(self, tmp_path):
        global_file = tmp_path / "global_nirna.md"
        global_file.write_text("global")
        (tmp_path / "NIRNA.md").write_text("project")
        (tmp_path / "NIRNA.local.md").write_text("local")
        files = load_nirna_files(cwd=str(tmp_path), global_path=str(global_file))
        assert len(files) == 3
        assert [f.type for f in files] == ["global", "project", "local"]


class TestFormatNirnaForPrompt:
    def test_none_when_empty(self):
        assert format_nirna_for_prompt([]) is None

    def test_format_includes_override_instruction(self):
        files = [NirnaFile(path="NIRNA.md", type="project", content="Use tabs.")]
        result = format_nirna_for_prompt(files)
        assert "OVERRIDE" in result
        assert "Use tabs" in result

    def test_to_prompt_section(self):
        f = NirnaFile(path="NIRNA.md", type="project", content="Always use snake_case.")
        section = f.to_prompt_section()
        assert "NIRNA.md" in section
        assert "project instructions" in section
        assert "snake_case" in section
