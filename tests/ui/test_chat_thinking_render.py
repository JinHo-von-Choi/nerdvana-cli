"""Chat messages: answers render as Markdown, status lines as markup, and neither drops the model's text."""

from __future__ import annotations

from unittest.mock import MagicMock

from rich.console import Console

from nerdvana_cli.ui.widgets.chat_message import ChatMessage, answer_renderable, markup_or_plain


class _ModelCfg:
    show_thinking = True


class _FakeSettings:
    model = _ModelCfg()


def _shown(widget: ChatMessage) -> str:
    """What a chat message shows, as plain text."""
    console = Console(width=100, record=True, color_system=None)
    console.print(widget._Static__content)
    return console.export_text()


def _make_app() -> MagicMock:
    """A NerdvanaApp stub that runs the real message methods and records what they mount."""
    from nerdvana_cli.ui.app import NerdvanaApp

    app = MagicMock(spec=NerdvanaApp)
    app.settings = _FakeSettings()
    app.settings.model.show_thinking = True
    mounted: list[ChatMessage] = []
    app._mount_chat = mounted.append
    app._mounted_messages = mounted
    app._add_chat_message = NerdvanaApp._add_chat_message.__get__(app, NerdvanaApp)
    app._add_answer = NerdvanaApp._add_answer.__get__(app, NerdvanaApp)
    return app


class TestAnswers:
    def test_brackets_in_code_are_kept(self) -> None:
        app = _make_app()
        app._add_answer("The type is `list[str]` and `a[0]` is the first item.")
        shown = _shown(app._mounted_messages[0])
        assert "list[str]" in shown
        assert "a[0]" in shown

    def test_a_closing_tag_does_not_break_the_message(self) -> None:
        app = _make_app()
        app._add_answer("Rich would choke on [/b] here.")
        assert "[/b]" in _shown(app._mounted_messages[0])

    def test_markdown_tables_and_bold_are_rendered(self) -> None:
        app = _make_app()
        app._add_answer("**main files**\n\n| file | size |\n|---|---|\n| a.py | 1 KB |")
        shown = _shown(app._mounted_messages[0])
        assert "main files" in shown and "**" not in shown
        assert "a.py" in shown and "|---|" not in shown

    def test_thinking_comes_before_the_answer(self) -> None:
        app = _make_app()
        app._add_answer("answer text", thinking="inner reasoning")
        shown = _shown(app._mounted_messages[0])
        assert shown.index("inner reasoning") < shown.index("answer text")

    def test_show_thinking_false_hides_it(self) -> None:
        app = _make_app()
        app.settings.model.show_thinking = False
        app._add_answer("only answer", thinking="hidden reasoning")
        shown = _shown(app._mounted_messages[0])
        assert "hidden reasoning" not in shown and "only answer" in shown

    def test_clipboard_gets_the_answer_as_written(self) -> None:
        app = _make_app()
        app._add_answer("**bold** list[str]")
        assert app._mounted_messages[0]._raw_text == "**bold** list[str]"


class TestStatusMessages:
    def test_markup_is_applied(self) -> None:
        app = _make_app()
        app._add_chat_message("[bold]msg[/bold]")
        assert _shown(app._mounted_messages[0]).strip() == "msg"

    def test_invalid_markup_is_shown_as_typed(self) -> None:
        app = _make_app()
        app._add_chat_message("memory 'x[/y]' removed")
        assert "x[/y]" in _shown(app._mounted_messages[0])

    def test_raw_text_is_kept(self) -> None:
        app = _make_app()
        app._add_chat_message("content", raw_text="plain content")
        assert app._mounted_messages[0]._raw_text == "plain content"


def test_markup_or_plain_and_answer_renderable_never_raise() -> None:
    markup_or_plain("[/unbalanced]")
    answer_renderable("[/b] list[str]", "[/thinking]")
