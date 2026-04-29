"""Interactive setup wizard for first-time configuration."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import yaml  # type: ignore[import-untyped]
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from nerdvana_cli.core import paths
from nerdvana_cli.providers.base import PROVIDER_KEY_ENVVARS, ProviderName

console = Console()


def get_config_path() -> str:
    """Return the user-level config path."""
    return str(paths.user_config_path())


def has_valid_api_key() -> bool:
    """Check if any provider API key is set in environment."""
    for provider, env_vars in PROVIDER_KEY_ENVVARS.items():
        if provider in (ProviderName.OLLAMA, ProviderName.VLLM):
            continue
        for var in env_vars:
            if os.environ.get(var, "").strip():
                return True
    return False


def has_config_file() -> bool:
    """Check if config file exists."""
    return os.path.exists(get_config_path())


def load_config() -> dict[str, Any]:
    """Load existing config or return empty dict."""
    path = get_config_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config: dict[str, Any], path: str = "") -> str:
    """Save config to file. Returns the path saved to."""
    if not path:
        path = get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, width=1000)
    os.chmod(path, 0o600)
    return path


def _resolve_default_provider_index(
    saved_provider: str,
    providers_display: Sequence[tuple[str, ...]],
) -> str:
    """Return the 1-based index string for saved_provider in providers_display.

    Falls back to "1" when saved_provider is absent or unrecognized.
    """
    for i, entry in enumerate(providers_display, 1):
        if entry[0] == saved_provider:
            return str(i)
    return "1"


def run_setup(force: bool = False) -> dict[str, Any] | None:
    """Run interactive setup wizard. Returns config dict or None if skipped."""

    if not force and has_config_file() and has_valid_api_key():
        console.print("[dim]Configuration already exists. Use 'nerdvana setup --force' to reconfigure.[/dim]")
        return None

    console.print()
    console.print(
        Panel.fit(
            "[bold]NerdVana CLI Setup[/bold]\nConfigure your AI provider and model.",
            border_style="cyan",
        )
    )

    # Step 1: Select provider
    console.print()
    console.print("[bold]Step 1: Select AI Provider[/bold]")
    console.print()

    providers_display = [
        ("anthropic", "Anthropic (Claude)", "claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
        ("openai", "OpenAI (GPT/o-series)", "gpt-4.1", "OPENAI_API_KEY"),
        ("gemini", "Google Gemini", "gemini-2.5-flash", "GEMINI_API_KEY"),
        ("groq", "Groq (Fast LLM)", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
        ("openrouter", "OpenRouter (Many)", "anthropic/claude-sonnet-4", "OPENROUTER_API_KEY"),
        ("xai", "xAI (Grok)", "grok-3", "XAI_API_KEY"),
        ("featherless", "Featherless AI", "featherless-llama-3-70b", "FEATHERLESS_API_KEY"),
        ("xiaomi_mimo", "Xiaomi MiMo", "mimo-v2.5-pro", "MIMO_API_KEY"),
        ("ollama", "Ollama (Local)", "qwen3", "(no key needed)"),
        ("vllm", "vLLM (Local)", "Qwen/Qwen3-32B", "(no key needed)"),
        ("deepseek", "DeepSeek", "deepseek-chat", "DEEPSEEK_API_KEY"),
        ("mistral", "Mistral AI", "mistral-medium-latest", "MISTRAL_API_KEY"),
        ("cohere", "Cohere", "command-r-plus", "CO_API_KEY"),
        ("together", "Together AI", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
        ("zai", "Z.AI (GLM)", "glm-4.7", "ZHIPUAI_API_KEY"),
    ]

    table = Table(title="Available Providers")
    table.add_column("#", style="dim", width=3)
    table.add_column("Provider", style="cyan", no_wrap=True)
    table.add_column("Default Model", style="green")
    table.add_column("API Key", style="yellow")

    for i, (_name, label, model, key_env) in enumerate(providers_display, 1):
        table.add_row(str(i), label, model, key_env)

    console.print(table)
    console.print()

    existing_cfg = load_config()
    saved_provider = (existing_cfg.get("model") or {}).get("provider", "")
    default_idx = _resolve_default_provider_index(saved_provider, providers_display)

    choice = Prompt.ask(
        "Select provider",
        choices=[str(i) for i in range(1, len(providers_display) + 1)],
        default=default_idx,
    )

    provider_name, provider_label, default_model, key_env = providers_display[int(choice) - 1]

    # Ollama sub-mode: local / cloud / self-hosted
    ollama_mode = ""
    base_url_override = ""
    if provider_name == "ollama":
        console.print()
        console.print("[dim]Ollama deployment mode:[/dim]")
        console.print("  1. Local (http://localhost:11434/v1) — no API key needed")
        console.print("  2. Cloud (https://ollama.com/v1) — requires API key")
        console.print("  3. Self-hosted — custom URL")
        ollama_choice = Prompt.ask("Select mode", choices=["1", "2", "3"], default="1")
        if ollama_choice == "1":
            ollama_mode = "local"
            base_url_override = "http://localhost:11434/v1"
        elif ollama_choice == "2":
            ollama_mode = "cloud"
            provider_label = "Ollama (Cloud)"
            key_env = "OLLAMA_API_KEY"
            default_model = "gpt-oss:120b"
            base_url_override = "https://ollama.com/v1"
        else:
            ollama_mode = "self-hosted"
            provider_label = "Ollama (Self-hosted)"
            base_url_override = Prompt.ask(
                "Ollama server URL",
                default="http://192.168.1.100:11434/v1",
            )
            use_key = Confirm.ask("Requires API key?", default=False)
            if use_key:
                key_env = "OLLAMA_API_KEY"

    # Step 2: API key
    console.print()
    console.print(f"[bold]Step 2: API Key for {provider_label}[/bold]")

    api_key = ""

    local_no_key = provider_name == "vllm" or (provider_name == "ollama" and ollama_mode != "cloud")

    if local_no_key:
        console.print(f"[dim]{provider_label} runs locally — no API key required.[/dim]")
    else:
        # Check if key already exists in env
        existing_key = ""
        for var in PROVIDER_KEY_ENVVARS.get(ProviderName(provider_name), []):
            existing_key = os.environ.get(var, "")
            if existing_key:
                break

        if existing_key:
            masked = existing_key[:8] + "..." + existing_key[-4:]
            console.print(f"[dim]Found {key_env}={masked}[/dim]")
            use_existing = Confirm.ask("Use this key?", default=True)
            if not use_existing:
                api_key = Prompt.ask("Enter API key (or leave empty to use env var)", password=True, default="")
            else:
                api_key = existing_key
        else:
            console.print(f"[dim]Set {key_env} environment variable, or enter directly.[/dim]")
            api_key = Prompt.ask("Enter API key (or leave empty to use env var)", password=True, default="")
            if not api_key:
                console.print(f"[yellow]No API key entered. You must set {key_env} before using NerdVana CLI.[/yellow]")

    # Step 3: Model
    console.print()
    console.print("[bold]Step 3: Model Selection[/bold]")
    console.print(f"[dim]Default: {default_model}[/dim]")

    use_default = Confirm.ask(f"Use {default_model}?", default=True)
    model = default_model if use_default else Prompt.ask("Enter model name", default=default_model)

    # Step 4: Max tokens
    console.print()
    console.print("[bold]Step 4: Token Settings[/bold]")
    max_tokens = int(Prompt.ask("Max tokens per response", default="8192"))

    # Build config
    config = {
        "model": {
            "provider": provider_name,
            "model": model,
            "api_key": api_key,
            "base_url": base_url_override,
            "max_tokens": max_tokens,
            "temperature": 1.0,
        },
        "permissions": {
            "mode": "default",
            "always_allow": ["FileRead", "Glob", "Grep"],
            "always_deny": [],
        },
        "session": {
            "persist": True,
            "max_turns": 200,
            "max_context_tokens": 180000,
            "compact_threshold": 0.8,
        },
    }

    # Save
    saved_path = save_config(config)

    console.print()
    console.print(
        Panel.fit(
            f"[bold green]Setup complete![/bold green]\n"
            f"Provider: {provider_label}\n"
            f"Model: {model}\n"
            f"Config: {saved_path}\n"
            f"Tools: 6 built-in tools (Bash, Read, Write, Edit, Glob, Grep)",
            title="Configuration Saved",
            border_style="green",
        )
    )

    return config
