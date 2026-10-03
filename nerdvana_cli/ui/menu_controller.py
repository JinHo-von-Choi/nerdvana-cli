"""Command menu and selector handling of the Textual TUI.

Extracted from ``NerdvanaApp``. Fills the slash-command menu from the typed text and
routes a pick from the command menu, the model selector or the provider selector. The
functions take the App reference and talk to widgets through it.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from nerdvana_cli.core.skills import Skill
from nerdvana_cli.ui.widgets import SLASH_COMMANDS, CommandMenu, ModelSelector, ProviderSelector

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


def seed_skill_options(menu: CommandMenu, skills: list[Skill]) -> None:
    """Add the skill triggers that are not built-in slash commands to the menu."""
    # Pre-seed seen set with built-in slash command IDs to avoid DuplicateID
    seen: set[str] = {cmd for cmd, _ in SLASH_COMMANDS}
    for skill in skills:
        if skill.trigger not in seen:
            menu.add_option(Option(f"{skill.trigger}  {skill.description}", id=skill.trigger))
            seen.add(skill.trigger)


def refresh_command_menu(app: NerdvanaApp, value: str) -> None:
    """Show or hide the command menu for the text typed in the input."""
    menu = app.query_one("#command-menu", CommandMenu)
    if not value.startswith("/"):
        menu.remove_class("visible")
        return

    query = value.lower()
    menu.clear_options()
    for cmd, desc in SLASH_COMMANDS:
        if query == "/" or cmd.startswith(query):
            menu.add_option(Option(f"{cmd}  {desc}", id=cmd))
    if app._agent_loop:
        seen: set[str] = {cmd for cmd, _ in SLASH_COMMANDS}
        for skill in app._agent_loop.skill_loader.list_skills():
            if skill.trigger not in seen and (query == "/" or skill.trigger.startswith(query)):
                menu.add_option(Option(f"{skill.trigger}  {skill.description}", id=skill.trigger))
                seen.add(skill.trigger)
        for command in app._user_commands():
            if command.trigger not in seen and (query == "/" or command.trigger.startswith(query)):
                menu.add_option(Option(f"{command.trigger}  {command.description}", id=command.trigger))
                seen.add(command.trigger)
    if menu.option_count > 0:
        menu.add_class("visible")
    else:
        menu.remove_class("visible")


def handle_option_selected(app: NerdvanaApp, event: OptionList.OptionSelected) -> None:
    """Handle a pick from the command menu, the model selector or the provider selector."""
    # Provider selector
    if isinstance(event.option_list, ProviderSelector):
        selector = app.query_one("#provider-selector", ProviderSelector)
        selector.remove_class("visible")
        provider_name = event.option.id
        if provider_name:
            from nerdvana_cli.commands.model_commands import handle_provider_selection
            asyncio.create_task(handle_provider_selection(app, provider_name))
        return

    # Model selector
    if isinstance(event.option_list, ModelSelector):
        model_selector = app.query_one("#model-selector", ModelSelector)
        model_selector.remove_class("visible")
        model_id = event.option.id
        if model_id:
            input_widget = app.query_one("#user-input", Input)
            input_widget.value = f"/model {model_id}"
            app.call_later(input_widget.action_submit)
            input_widget.focus()
        return

    # Command menu
    menu = app.query_one("#command-menu", CommandMenu)
    input_widget = app.query_one("#user-input", Input)
    menu.remove_class("visible")
    cmd = event.option.id
    if cmd:
        input_widget.value = cmd
        app.call_later(input_widget.action_submit)
