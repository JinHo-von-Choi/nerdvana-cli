"""What the editor puts in a prompt and what it may type: content blocks, images and slash commands.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

from acp.exceptions import RequestError
from acp.schema import (
    AudioContentBlock,
    AvailableCommand,
    AvailableCommandInput,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
    UnstructuredCommandInput,
)

from nerdvana_cli.core.context.user_commands import UserCommandLoader
from nerdvana_cli.core.loop.images import MAX_IMAGES

_ARGUMENT_HINT = "arguments"


def _block_text(block: Any) -> str:
    """The text a block contributes to the prompt; an image or audio block contributes none."""
    if isinstance(block, TextContentBlock):
        return str(block.text)
    if isinstance(block, ResourceContentBlock):
        return f"[{block.name}]({block.uri})"
    if isinstance(block, EmbeddedResourceContentBlock):
        resource = block.resource
        if isinstance(resource, TextResourceContents):
            return f'<context uri="{resource.uri}">\n{resource.text}\n</context>'
        return f"[{resource.uri}: binary content not attached]"
    if isinstance(block, AudioContentBlock):
        return "[audio not attached]"
    return ""


def prompt_parts(blocks: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """The prompt text and the image blocks (in the shape ``AgentLoop.run`` takes) of one ``session/prompt``."""
    images = [
        {"type": "image", "media_type": block.mime_type, "data": block.data}
        for block in blocks
        if isinstance(block, ImageContentBlock)
    ]
    if len(images) > MAX_IMAGES:
        raise RequestError.invalid_params({"message": f"at most {MAX_IMAGES} images per prompt"})
    text = "\n\n".join(part for part in map(_block_text, blocks) if part)
    return text, images


def expand_command(text: str, loader: UserCommandLoader) -> str:
    """The prompt a typed ``/command args`` stands for; *text* itself when it names no user command."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return text
    trigger, _, args = stripped.partition(" ")
    command = loader.get(trigger)
    return command.render(args.strip()) if command is not None else text


def available_commands(loader: UserCommandLoader) -> list[AvailableCommand]:
    """The user commands as ACP slash commands; each takes free-form arguments."""
    return [
        AvailableCommand(
            name        = command.name,
            description = command.description or f"Run the {command.name} command template",
            input       = AvailableCommandInput(UnstructuredCommandInput(hint=_ARGUMENT_HINT)),
        )
        for command in loader.list_commands()
    ]
