"""Model and provider-related command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def handle_model(app: NerdvanaApp, args: str) -> None:
    """Handle /model command — show or switch the current model."""
    if args:
        from nerdvana_cli.ui.app import StatusBar

        # Invariant: /model is a model-only operation. Provider and base_url
        # are owned by /provider; re-detecting here would corrupt state when
        # switching to a model whose name does not match the active provider's
        # prefix (e.g., Ollama's "gemma4:31b-cloud" on the Ollama endpoint).
        app.settings.model.model = args
        assert app._agent_loop is not None
        app._agent_loop.provider = app._agent_loop.create_provider_from_settings()

        # Persist so the selection survives restart.
        try:
            from nerdvana_cli.core.setup import load_config, save_config
            existing = load_config()
            existing.setdefault("model", {})
            existing["model"]["model"] = args
            existing["model"]["provider"] = app.settings.model.provider
            existing["model"]["base_url"] = app.settings.model.base_url
            existing.setdefault("model_history", {})[app.settings.model.provider] = args
            _mh = getattr(app.settings, "model_history", None)
            if isinstance(_mh, dict):
                _mh[app.settings.model.provider] = args
            save_config(existing)
        except Exception as save_err:
            app._add_chat_message(
                f"[yellow]Config save failed: {save_err}[/yellow]"
            )

        app._add_chat_message(
            f"[dim]Switched to {app.settings.model.provider}/{args}[/dim]"
        )
        app.query_one("#status-bar", StatusBar).update_status(
            model=app.settings.model.model,
            provider=app.settings.model.provider,
            tools=len(app._agent_loop.registry.all_tools()),
            parism=app.parism_client is not None,
        )
        app._update_banner()
    else:
        app._add_chat_message(
            f"[dim]Model: {app.settings.model.provider}/{app.settings.model.model}[/dim]"
        )


async def handle_models(app: NerdvanaApp, args: str) -> None:
    """Handle /models command — list available models for the current provider."""
    from textual.widgets.option_list import Option

    from nerdvana_cli.ui.app import ModelSelector

    app._add_chat_message("[dim]Fetching models...[/dim]")
    try:
        assert app._agent_loop is not None
        models = await app._agent_loop.provider.list_models()
        if not models:
            app._add_chat_message("[yellow]No models found or API error.[/yellow]")
        else:
            selector = app.query_one("#model-selector", ModelSelector)
            selector.clear_options()
            current_idx = 0
            for idx, m in enumerate(models):
                label = m.id
                if m.id == app.settings.model.model:
                    label += "  [current]"
                    current_idx = idx
                selector.add_option(Option(label, id=m.id))
            selector.highlighted = current_idx
            selector.add_class("visible")
            selector.focus()
    except Exception as e:
        app._add_chat_message(f"[red]Error listing models: {e}[/red]")


async def handle_api_key_input(app: NerdvanaApp, api_key: str) -> None:
    """Handle API key input for /provider flow."""
    from textual.widgets import Input

    provider_name = app._pending_provider
    app._pending_provider = ""
    input_widget = app.query_one("#user-input", Input)
    input_widget.placeholder = "Message..."
    input_widget.password = False

    if not api_key.strip():
        app._add_chat_message("[yellow]Cancelled.[/yellow]")
        return

    await switch_provider(app, provider_name, api_key)


async def switch_provider(app: NerdvanaApp, provider_name: str, api_key: str) -> None:
    """Switch to a provider with the given API key.

    Saves config and refreshes UI. API key validity is NOT verified here.
    Empty ``list_models()`` result means the provider does not expose model
    enumeration — not that the key is invalid.  Real key failures surface as
    401/403 on the first stream call.
    """
    from typing import Any

    from textual.widgets.option_list import Option

    from nerdvana_cli.providers.base import DEFAULT_BASE_URLS, DEFAULT_MODELS, ProviderName
    from nerdvana_cli.providers.factory import create_provider
    from nerdvana_cli.ui.app import ModelSelector, StatusBar

    app._add_chat_message(f"[dim]Switching to {provider_name}...[/dim]")

    try:
        prov = ProviderName(provider_name)
    except ValueError:
        app._add_chat_message(f"[red]Unknown provider: {provider_name}[/red]")
        return

    base_url = DEFAULT_BASE_URLS.get(prov, "")
    default_model = DEFAULT_MODELS.get(prov, "")
    _mh = getattr(app.settings, "model_history", None)
    last_model = (_mh.get(provider_name) if isinstance(_mh, dict) else None) or default_model

    # Apply settings unconditionally — key verification is the caller's job.
    app.settings.model.provider = provider_name
    app.settings.model.api_key = api_key
    app.settings.model.base_url = base_url
    app.settings.model.model = last_model
    assert app._agent_loop is not None
    app._agent_loop.provider = app._agent_loop.create_provider_from_settings()

    app._add_chat_message(f"[dim]Switched to {provider_name}/{last_model}[/dim]")

    # Best-effort model enumeration for the selector. Empty result is fine.
    test_provider = create_provider(
        provider=prov, model=last_model, api_key=api_key, base_url=base_url,
    )
    models: list[Any] = []
    try:
        models = await test_provider.list_models()
    except Exception as exc:
        # Surface the reason instead of silently swallowing it.
        app._add_chat_message(
            f"[dim]list_models unavailable for {provider_name}: {type(exc).__name__}: {exc}[/dim]"
        )

    if models:
        selector = app.query_one("#model-selector", ModelSelector)
        selector.clear_options()
        current_model_idx = 0
        for idx, m in enumerate(models):
            current = " [current]" if m.id == last_model else ""
            if m.id == last_model:
                current_model_idx = idx
            selector.add_option(Option(f"{m.id}{current}", id=m.id))
        selector.highlighted = current_model_idx
        app._add_chat_message(f"[dim]{len(models)} models. Select one:[/dim]")
        selector.add_class("visible")
        selector.focus()
    else:
        app._add_chat_message(
            f"[dim]Model enumeration unavailable. Using default: {last_model}[/dim]"
        )

    app._update_banner()
    app.query_one("#status-bar", StatusBar).update_status(
        model=app.settings.model.model,
        provider=app.settings.model.provider,
        tools=len(app._agent_loop.registry.all_tools()),
        parism=app.parism_client is not None,
    )

    # Save config + API key per provider
    from nerdvana_cli.core.setup import load_config, save_config
    existing = load_config()
    existing["model"] = {
        "provider": app.settings.model.provider,
        "model": app.settings.model.model,
        "api_key": app.settings.model.api_key,
        "base_url": app.settings.model.base_url,
        "max_tokens": app.settings.model.max_tokens,
        "temperature": app.settings.model.temperature,
    }
    existing.setdefault("model_history", {})[provider_name] = app.settings.model.model
    if "api_keys" not in existing:
        existing["api_keys"] = {}
    existing["api_keys"][provider_name] = api_key
    save_config(existing)
    app._add_chat_message("[dim]Config saved.[/dim]")


async def handle_provider_selection(app: NerdvanaApp, provider_name: str) -> None:
    """Handle provider selection from the ProviderSelector popup.

    Checks for a saved API key (config file, then env vars). If found,
    switches directly; otherwise prompts the user to enter a key.
    """
    import os

    from textual.widgets import Input

    # Check for saved API key
    from nerdvana_cli.core.setup import load_config
    existing = load_config()
    saved_keys = existing.get("api_keys", {})
    saved_key = saved_keys.get(provider_name, "")

    # Also check env vars
    if not saved_key:
        from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS, ProviderName
        try:
            for var in PROVIDER_KEY_ENVVARS.get(ProviderName(provider_name), []):
                saved_key = os.environ.get(var, "")
                if saved_key:
                    break
        except ValueError:
            pass

    if saved_key:
        # Key exists — switch directly
        import asyncio
        asyncio.create_task(switch_provider(app, provider_name, saved_key))
    else:
        # No key — ask for input
        app._pending_provider = provider_name
        app._add_chat_message(
            f"[dim]Enter API key for {provider_name}:[/dim]"
        )
        input_widget = app.query_one("#user-input", Input)
        input_widget.placeholder = f"API key for {provider_name}..."
        input_widget.password = True
        input_widget.focus()


async def handle_provider(app: NerdvanaApp, args: str) -> None:
    """Handle /provider command — show provider selector popup."""
    from textual.widgets.option_list import Option

    from nerdvana_cli.providers.base import DEFAULT_MODELS, ProviderName
    from nerdvana_cli.ui.app import ProviderSelector

    prov_selector = app.query_one("#provider-selector", ProviderSelector)
    prov_selector.clear_options()
    current_prov_idx = 0
    for idx, prov in enumerate(ProviderName):
        default_model = DEFAULT_MODELS.get(prov, "")
        current = " [current]" if prov.value == app.settings.model.provider else ""
        if prov.value == app.settings.model.provider:
            current_prov_idx = idx
        prov_selector.add_option(Option(
            f"{prov.value}  ({default_model}){current}",
            id=prov.value,
        ))
    prov_selector.highlighted = current_prov_idx
    prov_selector.add_class("visible")
    prov_selector.focus()
