"""Widget sub-package for the NerdVana TUI.

Re-exports all widget classes so callers can use either the flat form::

    from nerdvana_cli.ui.widgets import StreamingOutput

or the module-specific form::

    from nerdvana_cli.ui.widgets.streaming import StreamingOutput
"""

from __future__ import annotations

from nerdvana_cli.ui.widgets.activity_indicator import ActivityIndicator
from nerdvana_cli.ui.widgets.chat_message import ChatMessage
from nerdvana_cli.ui.widgets.command_menu import SLASH_COMMANDS, CommandMenu
from nerdvana_cli.ui.widgets.model_selector import ModelSelector
from nerdvana_cli.ui.widgets.multiline_input import MultilineAwareInput
from nerdvana_cli.ui.widgets.provider_selector import ProviderSelector
from nerdvana_cli.ui.widgets.status_bar import StatusBar
from nerdvana_cli.ui.widgets.streaming import StreamingOutput
from nerdvana_cli.ui.widgets.tool_status import ToolStatusLine

__all__ = [
    "ActivityIndicator",
    "ChatMessage",
    "CommandMenu",
    "ModelSelector",
    "MultilineAwareInput",
    "ProviderSelector",
    "SLASH_COMMANDS",
    "StatusBar",
    "StreamingOutput",
    "ToolStatusLine",
]
