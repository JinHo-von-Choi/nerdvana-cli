"""Interactive setup wizard for first-time configuration."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from nerdvana_cli.core.config import paths
from nerdvana_cli.providers.base import (
    DEFAULT_MODELS,
    PROVIDER_CAPABILITIES,
    PROVIDER_KEY_ENVVARS,
    ProviderName,
)

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
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
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


_PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI (GPT/o-series)",
    "gemini": "Google Gemini",
    "groq": "Groq (Fast LLM)",
    "openrouter": "OpenRouter (Many)",
    "xai": "xAI (Grok)",
    "ollama": "Ollama (Local)",
    "vllm": "vLLM (Local)",
    "deepseek": "DeepSeek",
    "mistral": "Mistral AI",
    "cohere": "Cohere",
    "together": "Together AI",
    "zai": "Z.AI (GLM)",
    "featherless": "Featherless AI",
    "xiaomi_mimo": "Xiaomi MiMo",
    "moonshot": "Moonshot (Kimi)",
    "dashscope": "DashScope (Qwen)",
    "minimax": "MiniMax",
    "perplexity": "Perplexity (Sonar)",
    "fireworks": "Fireworks AI",
    "cerebras": "Cerebras",
}

_WIZARD_ORDER: tuple[str, ...] = (
    "anthropic", "openai", "gemini", "groq", "openrouter", "xai",
    "featherless", "xiaomi_mimo", "ollama", "vllm",
    "deepseek", "mistral", "cohere", "together", "zai",
    "moonshot", "dashscope", "minimax", "perplexity", "fireworks", "cerebras",
)

_NO_KEY_LABEL = "(no key needed)"


def build_providers_display() -> list[tuple[str, str, str, str]]:
    """Build wizard rows (name, label+caps, default_model, key_env) from base.py."""
    rows: list[tuple[str, str, str, str]] = []
    for name in _WIZARD_ORDER:
        provider = ProviderName(name)
        caps = PROVIDER_CAPABILITIES.get(provider, {})
        tags = [
            tag
            for tag, on in (
                ("tools", caps.get("supports_tools")),
                ("vision", caps.get("supports_vision")),
                ("thinking", caps.get("supports_thinking")),
            )
            if on
        ]
        label = _PROVIDER_LABELS.get(name, name.title())
        if tags:
            label = f"{label} [{', '.join(tags)}]"
        if provider in (ProviderName.OLLAMA, ProviderName.VLLM):
            key_env = _NO_KEY_LABEL
        else:
            envs = PROVIDER_KEY_ENVVARS.get(provider, [])
            key_env = envs[0] if envs else "(none)"
        rows.append((name, label, DEFAULT_MODELS.get(provider, ""), key_env))
    return rows


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


@dataclass
class _ProviderChoice:
    """The provider picked in the wizard, with what its deployment mode changed."""

    name:          str
    label:         str
    default_model: str
    key_env:       str
    base_url:      str = ""
    ollama_mode:   str = ""

    @property
    def needs_key(self) -> bool:
        return not (self.name == "vllm" or (self.name == "ollama" and self.ollama_mode != "cloud"))


def _choose_provider() -> _ProviderChoice:
    """Step 1: show the providers and ask for one, defaulting to the saved one."""
    console.print()
    console.print("[bold]Step 1: Select AI Provider[/bold]")
    console.print()

    providers_display = build_providers_display()

    table = Table(title="Available Providers")
    table.add_column("#", style="dim", width=3)
    table.add_column("Provider", style="cyan", no_wrap=True)
    table.add_column("Default Model", style="green")
    table.add_column("API Key", style="yellow")
    for i, (_name, label, model, key_env) in enumerate(providers_display, 1):
        table.add_row(str(i), label, model, key_env)
    console.print(table)
    console.print()

    saved_provider = (load_config().get("model") or {}).get("provider", "")
    choice = Prompt.ask(
        "Select provider",
        choices=[str(i) for i in range(1, len(providers_display) + 1)],
        default=_resolve_default_provider_index(saved_provider, providers_display),
    )
    name, label, default_model, key_env = providers_display[int(choice) - 1]
    picked = _ProviderChoice(name, label, default_model, key_env)
    if name == "ollama":
        _choose_ollama_mode(picked)
    return picked


def _choose_ollama_mode(picked: _ProviderChoice) -> None:
    """Ollama runs locally, in Ollama Cloud or on a host of your own; each needs a different address and key."""
    console.print()
    console.print("[dim]Ollama deployment mode:[/dim]")
    console.print("  1. Local (http://localhost:11434/v1) — no API key needed")
    console.print("  2. Cloud (https://ollama.com/v1) — requires API key")
    console.print("  3. Self-hosted — custom URL")
    ollama_choice = Prompt.ask("Select mode", choices=["1", "2", "3"], default="1")
    if ollama_choice == "1":
        picked.ollama_mode = "local"
        picked.base_url    = "http://localhost:11434/v1"
    elif ollama_choice == "2":
        picked.ollama_mode   = "cloud"
        picked.label         = "Ollama (Cloud)"
        picked.key_env       = "OLLAMA_API_KEY"
        picked.default_model = "gpt-oss:120b"
        picked.base_url      = "https://ollama.com/v1"
    else:
        picked.ollama_mode = "self-hosted"
        picked.label       = "Ollama (Self-hosted)"
        picked.base_url    = Prompt.ask("Ollama server URL", default="http://localhost:11434/v1")
        if Confirm.ask("Requires API key?", default=False):
            picked.key_env = "OLLAMA_API_KEY"


def _ask_api_key(picked: _ProviderChoice) -> str:
    """Step 2: the API key, offering the one already in the environment."""
    console.print()
    console.print(f"[bold]Step 2: API Key for {picked.label}[/bold]")

    if not picked.needs_key:
        console.print(f"[dim]{picked.label} runs locally — no API key required.[/dim]")
        return ""

    existing_key = next(
        (value for var in PROVIDER_KEY_ENVVARS.get(ProviderName(picked.name), []) if (value := os.environ.get(var, ""))),
        "",
    )
    if existing_key:
        masked = existing_key[:8] + "..." + existing_key[-4:]
        console.print(f"[dim]Found {picked.key_env}={masked}[/dim]")
        if Confirm.ask("Use this key?", default=True):
            return existing_key
        return Prompt.ask("Enter API key (or leave empty to use env var)", password=True, default="")

    console.print(f"[dim]Set {picked.key_env} environment variable, or enter directly.[/dim]")
    api_key = Prompt.ask("Enter API key (or leave empty to use env var)", password=True, default="")
    if not api_key:
        console.print(f"[yellow]No API key entered. You must set {picked.key_env} before using NerdVana CLI.[/yellow]")
    return api_key


def _ask_model(default_model: str) -> tuple[str, int]:
    """Steps 3 and 4: the model and the response token limit."""
    console.print()
    console.print("[bold]Step 3: Model Selection[/bold]")
    console.print(f"[dim]Default: {default_model}[/dim]")
    use_default = Confirm.ask(f"Use {default_model}?", default=True)
    model = default_model if use_default else Prompt.ask("Enter model name", default=default_model)

    console.print()
    console.print("[bold]Step 4: Token Settings[/bold]")
    return model, int(Prompt.ask("Max tokens per response", default="8192"))


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

    picked            = _choose_provider()
    api_key           = _ask_api_key(picked)
    model, max_tokens = _ask_model(picked.default_model)

    config = {
        "model": {
            "provider": picked.name,
            "model": model,
            "api_key": api_key,
            "base_url": picked.base_url,
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
    saved_path = save_config(config)

    console.print()
    console.print(
        Panel.fit(
            f"[bold green]Setup complete![/bold green]\n"
            f"Provider: {picked.label}\n"
            f"Model: {model}\n"
            f"Config: {saved_path}",
            title="Configuration Saved",
            border_style="green",
        )
    )

    return config
