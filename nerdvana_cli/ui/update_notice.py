"""Startup update check of the Textual TUI.

Extracted from ``NerdvanaApp``. Runs as a background task after mount and posts a notice
in the chat when a newer release is available.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from nerdvana_cli import __version__
from nerdvana_cli.core.updater import (
    cached_or_check,
    format_update_notice,
    is_update_check_enabled,
)

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def check_for_update(app: NerdvanaApp) -> None:
    """Post a chat notice when a newer release exists and the check is enabled."""
    try:
        flag = bool(app.settings.session.update_check)
    except Exception:
        flag = True
    if not is_update_check_enabled(flag):
        return

    result = await cached_or_check(__version__)
    if result and result.get("version"):
        with contextlib.suppress(Exception):
            app._add_chat_message(
                format_update_notice(__version__, result["version"], result.get("url", ""))
            )
