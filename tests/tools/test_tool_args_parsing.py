"""Tests for tool args parsing — dict from API -> typed Args objects."""


from nerdvana_cli.tools.bash_tool import BashArgs, BashTool
from nerdvana_cli.tools.file_tools import (
    FileEditArgs,
    FileEditTool,
    FileReadArgs,
    FileReadTool,
    FileWriteArgs,
    FileWriteTool,
)
from nerdvana_cli.tools.search_tools import GlobArgs, GlobTool, GrepArgs, GrepTool


class TestBashToolParseArgs:
    def test_parse_all_fields(self):
        tool = BashTool()
        raw = {"command": "ls -la", "timeout": 60, "description": "list files"}
        args = tool.parse_args(raw)
        assert isinstance(args, BashArgs)
        assert args.command == "ls -la"
        assert args.timeout == 60
        assert args.description == "list files"

    def test_parse_required_only(self):
        tool = BashTool()
        raw = {"command": "pwd"}
        args = tool.parse_args(raw)
        assert isinstance(args, BashArgs)
        assert args.command == "pwd"
        assert args.timeout == 120
        assert args.description == ""


class TestFileReadToolParseArgs:
    def test_parse_all_fields(self):
        tool = FileReadTool()
        raw = {"path": "src/main.py", "offset": 10, "limit": 50}
        args = tool.parse_args(raw)
        assert isinstance(args, FileReadArgs)
        assert args.path == "src/main.py"
        assert args.offset == 10
        assert args.limit == 50

    def test_parse_required_only(self):
        tool = FileReadTool()
        raw = {"path": "README.md"}
        args = tool.parse_args(raw)
        assert args.path == "README.md"
        assert args.offset == 0
        assert args.limit == 0


class TestFileWriteToolParseArgs:
    def test_parse_args(self):
        tool = FileWriteTool()
        raw = {"path": "out.txt", "content": "hello"}
        args = tool.parse_args(raw)
        assert isinstance(args, FileWriteArgs)
        assert args.path == "out.txt"
        assert args.content == "hello"


class TestFileEditToolParseArgs:
    def test_parse_all_fields(self):
        tool = FileEditTool()
        raw = {"path": "f.py", "old_string": "a", "new_string": "b", "replace_all": True}
        args = tool.parse_args(raw)
        assert isinstance(args, FileEditArgs)
        assert args.replace_all is True

    def test_parse_required_only(self):
        tool = FileEditTool()
        raw = {"path": "f.py", "old_string": "a", "new_string": "b"}
        args = tool.parse_args(raw)
        assert args.replace_all is False


class TestGlobToolParseArgs:
    def test_parse_all_fields(self):
        tool = GlobTool()
        raw = {"pattern": "**/*.py", "path": "src"}
        args = tool.parse_args(raw)
        assert isinstance(args, GlobArgs)
        assert args.pattern == "**/*.py"
        assert args.path == "src"

    def test_parse_required_only(self):
        tool = GlobTool()
        raw = {"pattern": "*.ts"}
        args = tool.parse_args(raw)
        assert args.path == "."


class TestGrepToolParseArgs:
    def test_parse_all_fields(self):
        tool = GrepTool()
        raw = {"pattern": "TODO", "path": "src", "include": "*.py", "case_sensitive": True}
        args = tool.parse_args(raw)
        assert isinstance(args, GrepArgs)
        assert args.case_sensitive is True

    def test_parse_required_only(self):
        tool = GrepTool()
        raw = {"pattern": "TODO"}
        args = tool.parse_args(raw)
        assert args.path == "."
        assert args.include == ""
        assert args.case_sensitive is False


class TestParseArgsIgnoresUnknownKeys:
    def test_unknown_keys_ignored(self):
        tool = GlobTool()
        raw = {"pattern": "*.py", "unknown_field": 123}
        args = tool.parse_args(raw)
        assert args.pattern == "*.py"
