"""Images in prompts: reading the file, the providers' shapes, the loop and the commands.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context_budget import IMAGE_TOKENS, message_tokens
from nerdvana_cli.core.images import (
    MAX_IMAGE_BYTES,
    ImageError,
    load_image,
    load_images,
    prompt_content,
    transcript_text,
)
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.main import app
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.types import Message, Role

PNG  = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF  = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32


@pytest.fixture()
def picture(tmp_path: Path) -> Path:
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    return path


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("data", "media"), [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")])
def test_the_type_comes_from_the_first_bytes_not_the_name(tmp_path: Path, data: bytes, media: str) -> None:
    (tmp_path / "x.dat").write_bytes(data)
    block = load_image("x.dat", str(tmp_path))
    assert block["media_type"] == media and base64.b64decode(block["data"]) == data and block["name"] == "x.dat"


def test_a_renamed_text_file_a_missing_file_and_a_directory_are_refused(tmp_path: Path) -> None:
    (tmp_path / "fake.png").write_text("not an image at all")
    for name, reason in (("fake.png", "not a PNG"), ("absent.png", "cannot read"), (".", "not a file")):
        with pytest.raises(ImageError, match=reason):
            load_image(name, str(tmp_path))


def test_a_file_over_the_limit_and_too_many_images_are_refused(tmp_path: Path) -> None:
    (tmp_path / "big.png").write_bytes(PNG + b"\x00" * MAX_IMAGE_BYTES)
    with pytest.raises(ImageError, match="over the 5 MB limit"):
        load_image("big.png", str(tmp_path))
    (tmp_path / "a.png").write_bytes(PNG)
    with pytest.raises(ImageError, match="at most 6"):
        load_images(["a.png"] * 7, str(tmp_path))


def test_the_prompt_content_and_the_transcript_text() -> None:
    block = {"type": "image", "media_type": "image/png", "data": "QQ==", "name": "a.png"}
    assert prompt_content("hi", None) == "hi"
    assert prompt_content("hi", [block]) == [{"type": "text", "text": "hi"}, {"type": "image", "media_type": "image/png", "data": "QQ=="}]
    assert transcript_text("hi", [block]) == "hi\n[image attached: a.png]" and transcript_text("hi", None) == "hi"


def test_an_image_is_estimated_at_a_flat_figure_not_by_its_bytes() -> None:
    big = Message(role=Role.USER, content=[{"type": "text", "text": "x"}, {"type": "image", "media_type": "image/png", "data": "A" * 400_000}])
    assert IMAGE_TOKENS <= message_tokens([big]) < IMAGE_TOKENS + 50


# ---------------------------------------------------------------------------
# The providers' shapes
# ---------------------------------------------------------------------------

IMAGE_MESSAGE = [{"role": "user", "content": [{"type": "text", "text": "what is this?"}, {"type": "image", "media_type": "image/png", "data": "QUJD"}]}]


def test_anthropic_gets_a_base64_image_source() -> None:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="claude-sonnet-5-5"))
    content  = provider._convert_messages(IMAGE_MESSAGE)[0]["content"]
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}


def test_openai_gets_content_parts_with_a_data_url() -> None:
    provider = OpenAIProvider(ProviderConfig(provider=ProviderName.OPENAI, api_key="k", model="m"))
    message  = provider._convert_messages("sys", IMAGE_MESSAGE)[1]
    assert message["content"] == [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]
    plain = provider._convert_messages("sys", [{"role": "user", "content": "plain"}])[1]
    assert plain == {"role": "user", "content": "plain"}


def test_gemini_gets_inline_data_as_bytes() -> None:
    provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, api_key="k", model="gemini-2.5-flash"))
    parts    = provider._convert_messages(IMAGE_MESSAGE)[0]["parts"]
    assert parts == [{"text": "what is this?"}, {"inlineData": {"mimeType": "image/png", "data": b"ABC"}}]
    from google.genai import types

    assert types.Content.model_validate(provider._convert_messages(IMAGE_MESSAGE)[0]).parts[1].inline_data.data == b"ABC"  # type: ignore[index, union-attr]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class _Echo:
    def __init__(self) -> None:
        self.seen: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
        self.seen.append([dict(m) for m in messages])
        yield ProviderEvent(type="content_delta", content="a cat")
        yield ProviderEvent(type="done", stop_reason="end_turn")


async def test_the_prompt_reaches_the_provider_with_its_image_and_the_transcript_keeps_no_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    provider = _Echo()
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="img", storage_dir=str(tmp_path / "s")))
    block = {"type": "image", "media_type": "image/png", "data": "QUJD", "name": "cat.png"}
    async for _ in loop.run("what is this?", [block]):
        pass
    content = [m["content"] for m in provider.seen[0] if m["role"] == "user" and isinstance(m["content"], list)][0]
    assert content[1] == {"type": "image", "media_type": "image/png", "data": "QUJD"}
    recorded = json.dumps(loop.session.replay())
    assert "QUJD" not in recorded and "[image attached: cat.png]" in recorded


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------

runner = CliRunner()


def test_run_refuses_a_bad_image_before_calling_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.png").write_text("nope")
    result = runner.invoke(app, ["run", "go", "--image", "x.png", "--output-format", "json"])
    assert result.exit_code == 2 and "not a PNG" in json.loads(result.stdout)["error"]


def test_run_sends_the_image_with_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, picture: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.chdir(tmp_path)
    provider = _Echo()
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    result = runner.invoke(app, ["run", "what is this?", "--image", "shot.png", "--output-format", "json"])
    assert result.exit_code == 0, result.output
    sent = [m["content"] for m in provider.seen[0] if isinstance(m["content"], list)][0]
    assert sent[1]["type"] == "image" and sent[1]["media_type"] == "image/png"


async def test_the_image_command_attaches_leading_files_and_asks_the_rest(tmp_path: Path, picture: Path) -> None:
    from nerdvana_cli.commands.image_command import handle_image, split_args

    assert split_args("shot.png what is it", str(tmp_path)) == (["shot.png"], "what is it")
    assert split_args("what is it", str(tmp_path)) == ([], "what is it")

    class _App:
        settings = NerdvanaSettings()

        def __init__(self) -> None:
            self.settings.cwd = str(tmp_path)
            self._pending_images = None
            self.started: list[tuple[str, str]] = []
            self.messages: list[str] = []

        def _start_prompt(self, shown: str, prompt: str) -> None:
            self.started.append((shown, prompt))

        def _add_chat_message(self, markup: str, **_: Any) -> None:
            self.messages.append(markup)

    fake = _App()
    await handle_image(fake, "shot.png what is it")  # type: ignore[arg-type]
    assert fake.started == [("/image shot.png: what is it", "what is it")] and fake._pending_images[0]["media_type"] == "image/png"  # type: ignore[index]
    empty = _App()
    await handle_image(empty, "no files here")  # type: ignore[arg-type]
    assert empty.started == [] and "No image file" in empty.messages[0]
