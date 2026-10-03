"""Reading an image file into the block a prompt can carry.

Author: 최진호
Date:   2026-10-03

An image block is ``{"type": "image", "media_type": "image/png", "data": "<base64>"}``; the providers turn it
into their own shape. The file must be a PNG, JPEG, GIF or WebP, and its first bytes must say so: the
extension alone does not decide, so a text file renamed to ``.png`` is refused instead of sent.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES      = 6


class ImageError(ValueError):
    """The file cannot be sent as an image."""


def _media_type(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_image(path: str, cwd: str = ".") -> dict[str, Any]:
    """The image block for *path* (relative to *cwd*); raises ImageError with the reason."""
    file = Path(path).expanduser()
    if not file.is_absolute():
        file = Path(cwd) / file
    try:
        size = file.stat().st_size
    except OSError as exc:
        raise ImageError(f"{path}: cannot read ({exc.strerror or exc})") from exc
    if not file.is_file():
        raise ImageError(f"{path}: not a file")
    if size > MAX_IMAGE_BYTES:
        raise ImageError(f"{path}: {size / 1_048_576:.1f} MB is over the {MAX_IMAGE_BYTES // 1_048_576} MB limit")
    data       = file.read_bytes()
    media_type = _media_type(data[:12])
    if media_type is None:
        raise ImageError(f"{path}: not a PNG, JPEG, GIF or WebP image")
    return {"type": "image", "media_type": media_type, "data": base64.b64encode(data).decode("ascii"), "name": file.name}


def load_images(paths: list[str], cwd: str = ".") -> list[dict[str, Any]]:
    """Several image blocks; at most ``MAX_IMAGES``."""
    if len(paths) > MAX_IMAGES:
        raise ImageError(f"at most {MAX_IMAGES} images per prompt")
    return [load_image(p, cwd) for p in paths]


def prompt_content(prompt: str, images: list[dict[str, Any]] | None) -> str | list[dict[str, Any]]:
    """The user message content: the plain prompt, or the prompt followed by its images."""
    if not images:
        return prompt
    return [{"type": "text", "text": prompt}, *({k: v for k, v in image.items() if k != "name"} for image in images)]


def transcript_text(prompt: str, images: list[dict[str, Any]] | None) -> str:
    """What the session transcript keeps of the prompt: the text and the names of the images, not their bytes."""
    if not images:
        return prompt
    return prompt + "".join(f"\n[image attached: {image.get('name', 'image')}]" for image in images)
