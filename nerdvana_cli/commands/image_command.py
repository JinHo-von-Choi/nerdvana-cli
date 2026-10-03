"""The ``/image`` command: send a prompt with an image attached.

Author: 최진호
Date:   2026-10-03

    /image <path> [<path> ...] <question>

Words at the start that name existing image files are attached; the rest is the question.
"""

from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING

from rich.markup import escape

from nerdvana_cli.core.images import ImageError, load_images

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

USAGE = "Usage: /image <path> [<path> ...] <question>"


def split_args(args: str, cwd: str) -> tuple[list[str], str]:
    """The leading words that are files, and the remaining words as the question."""
    try:
        words = shlex.split(args)
    except ValueError:
        words = args.split()
    paths: list[str] = []
    while words and os.path.isfile(os.path.join(cwd, os.path.expanduser(words[0]))):
        paths.append(words.pop(0))
    return paths, " ".join(words)


async def handle_image(app: NerdvanaApp, args: str) -> None:
    """Handle ``/image``."""
    paths, question = split_args(args, app.settings.cwd or ".")
    if not paths:
        app._add_chat_message(f"[red]No image file found at the start.[/red] [dim]{escape(USAGE)}[/dim]")
        return
    try:
        images = load_images(paths, app.settings.cwd or ".")
    except ImageError as exc:
        app._add_chat_message(f"[red]{escape(str(exc))}[/red]")
        return
    app._pending_images = images
    names = ", ".join(image["name"] for image in images)
    app._start_prompt(f"/image {names}: {question}", question or "Describe the attached image.")
