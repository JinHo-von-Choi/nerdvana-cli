"""Tests for tool status markers."""

from nerdvana_cli.core.agent_loop import TOOL_DONE_PREFIX, TOOL_STATUS_PREFIX


class TestToolStatusMarkers:
    def test_status_prefix_is_non_printable(self):
        assert TOOL_STATUS_PREFIX.startswith("\x00")

    def test_done_prefix_is_non_printable(self):
        assert TOOL_DONE_PREFIX.startswith("\x00")

    def test_marker_parsing(self):
        chunk = f'{TOOL_STATUS_PREFIX}Bash {{"command": "ls"}}'
        assert chunk.startswith(TOOL_STATUS_PREFIX)
        info = chunk[len(TOOL_STATUS_PREFIX):]
        assert "Bash" in info

    def test_done_marker_parsing(self):
        chunk = f'{TOOL_DONE_PREFIX}Bash [done]'
        assert chunk.startswith(TOOL_DONE_PREFIX)
        info = chunk[len(TOOL_DONE_PREFIX):]
        assert "[done]" in info

    def test_normal_text_not_matched(self):
        chunk = "Hello world"
        assert not chunk.startswith(TOOL_STATUS_PREFIX)
        assert not chunk.startswith(TOOL_DONE_PREFIX)

    def test_error_status(self):
        chunk = f'{TOOL_DONE_PREFIX}Bash [error]'
        info = chunk[len(TOOL_DONE_PREFIX):]
        assert "[error]" in info
