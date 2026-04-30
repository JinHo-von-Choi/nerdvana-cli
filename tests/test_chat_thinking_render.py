"""Tests for _add_chat_message thinking markup prefix (option B)."""

from __future__ import annotations

from unittest.mock import MagicMock


class _ModelCfg:
    show_thinking     = True
    provider          = "anthropic"
    model             = "claude-sonnet"
    max_tokens        = 8096
    temperature       = 1.0
    api_key           = None
    base_url          = None
    extended_thinking = False
    fallback_models: list[str] = []


class _FakeSettings:
    model = _ModelCfg()


def _get_markup(widget: object) -> str:
    """Return the markup string stored in a Static/ChatMessage widget."""
    # Static stores the initial content in the name-mangled '_Static__content' slot.
    return str(getattr(widget, "_Static__content", ""))


def _make_app() -> MagicMock:
    """Return a minimal NerdvanaApp stub with the real _add_chat_message logic."""
    from nerdvana_cli.ui.app import ChatMessage, NerdvanaApp, StreamingOutput

    # We test only the logic of _add_chat_message — no Textual event loop needed.
    # Patch query_one to capture mounted widgets.
    app = MagicMock(spec=NerdvanaApp)
    app.settings = _FakeSettings()

    mounted_messages: list[ChatMessage] = []

    def _fake_mount(widget: ChatMessage, **kwargs: object) -> None:
        mounted_messages.append(widget)

    fake_scroll = MagicMock()
    fake_scroll.mount.side_effect = _fake_mount
    fake_scroll.scroll_end = MagicMock()

    fake_streaming = MagicMock(spec=StreamingOutput)

    def _query_one(selector: str, type_: type | None = None) -> MagicMock:
        if "chat-frame" in str(selector):
            return fake_scroll
        return fake_streaming

    app.query_one.side_effect = _query_one
    app._mounted_messages = mounted_messages

    # Bind the real method to the stub
    app._add_chat_message = NerdvanaApp._add_chat_message.__get__(app, NerdvanaApp)
    return app


class TestAddChatMessageThinkingMarkup:
    def test_no_thinking_renders_markup_only(self) -> None:
        app = _make_app()
        app._add_chat_message("hello world", thinking="")
        assert len(app._mounted_messages) == 1
        msg = app._mounted_messages[0]
        rendered = _get_markup(msg)
        assert "hello world" in rendered
        assert "[thinking]" not in rendered

    def test_thinking_prefixed_before_markup(self) -> None:
        app = _make_app()
        app._add_chat_message("answer text", thinking="inner reasoning")
        assert len(app._mounted_messages) == 1
        rendered = _get_markup(app._mounted_messages[0])
        assert "[thinking]" in rendered
        assert "inner reasoning" in rendered
        # thinking block must come BEFORE the answer
        assert rendered.index("[thinking]") < rendered.index("answer text")

    def test_thinking_uses_dim_italic_markup(self) -> None:
        app = _make_app()
        app._add_chat_message("response", thinking="some reasoning")
        rendered = _get_markup(app._mounted_messages[0])
        assert "[dim italic]" in rendered
        assert "[/dim italic]" in rendered

    def test_show_thinking_false_suppresses_block(self) -> None:
        app = _make_app()
        app.settings.model.show_thinking = False
        app._add_chat_message("only answer", thinking="hidden reasoning")
        rendered = _get_markup(app._mounted_messages[0])
        assert "[thinking]" not in rendered
        assert "hidden reasoning" not in rendered
        assert "only answer" in rendered

    def test_empty_thinking_no_prefix(self) -> None:
        app = _make_app()
        app._add_chat_message("[bold]msg[/bold]", thinking="")
        rendered = _get_markup(app._mounted_messages[0])
        assert rendered == "[bold]msg[/bold]"

    def test_raw_text_preserved_without_thinking_markup(self) -> None:
        app = _make_app()
        app._add_chat_message("content", raw_text="plain content", thinking="thought")
        msg = app._mounted_messages[0]
        assert msg._raw_text == "plain content"
