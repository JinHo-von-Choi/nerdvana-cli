"""Core agent loop — the heart of NerdVana CLI.

Inspired by Claude Code's query.ts: a streaming agent loop that
calls the model, executes tools, and repeats until completion.
Provider-agnostic design: works with Anthropic, OpenAI, Gemini, Groq, etc.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncGenerator
from typing import Any

from rich.console import Console

from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.types import (
    Message,
    PermissionBehavior,
    Role,
    SessionState,
    ToolResult,
)

console = Console()

TOOL_STATUS_PREFIX = "\x00TOOL:"
TOOL_DONE_PREFIX   = "\x00TOOL_DONE:"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def estimate_messages_tokens(messages: list) -> int:
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        total += estimate_tokens(content)
        if msg.tool_uses:
            total += estimate_tokens(json.dumps(msg.tool_uses))
    return total


def compact_messages(messages: list, max_tokens: int) -> list:
    if not messages:
        return messages
    current = estimate_messages_tokens(messages)
    if current <= max_tokens:
        return messages
    keep_recent = min(10, len(messages))
    recent = messages[-keep_recent:]
    remaining_budget = max_tokens - estimate_messages_tokens(recent)
    if remaining_budget <= 0:
        return messages[-4:]
    early = []
    for msg in messages[:-keep_recent]:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        cost = estimate_tokens(content)
        if remaining_budget - cost < 0:
            break
        early.append(msg)
        remaining_budget -= cost
    dropped_count = len(messages) - len(early) - len(recent)
    if dropped_count > 0:
        summary = Message(role=Role.USER, content=f"[context compacted: {dropped_count} earlier messages removed to fit context window]")
        return early + [summary] + recent
    return early + recent


class AgentLoop:
    """Core agent loop: API call -> stream -> tool exec -> repeat."""

    def __init__(
        self,
        settings: NerdvanaSettings,
        registry: ToolRegistry,
        session: SessionStorage | None = None,
    ):
        self.settings = settings
        self.registry = registry
        self.session = session or SessionStorage()
        self.state = SessionState()
        self.console = Console()

        self.provider = self.create_provider_from_settings()

    def create_provider_from_settings(self):
        """Create provider from current settings."""
        provider_name = None
        if self.settings.model.provider:
            provider_name = ProviderName(self.settings.model.provider)

        return create_provider(
            provider=provider_name,
            model=self.settings.model.model,
            api_key=self.settings.model.api_key,
            base_url=self.settings.model.base_url,
            max_tokens=self.settings.model.max_tokens,
            temperature=self.settings.model.temperature,
        )

    def build_system_prompt(self) -> str:
        from nerdvana_cli.core.prompts import build_system_prompt as build_prompt

        return build_prompt(
            tools=self.registry.all_tools(),
            parism_active=self.registry.get("Parism") is not None,
            model=self.settings.model.model,
            provider=self.settings.model.provider,
            cwd=self.settings.cwd,
        )

    def _to_provider_messages(self) -> list[dict[str, Any]]:
        """Convert internal Message objects to provider-neutral dict format."""
        messages = []
        for msg in self.state.messages:
            if msg.role == Role.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                d = {"role": "assistant", "content": msg.content}
                if msg.tool_uses:
                    d["tool_uses"] = msg.tool_uses
                messages.append(d)
            elif msg.role == Role.TOOL:
                messages.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_use_id": msg.tool_use_id or "",
                        "is_error": msg.is_error,
                    }
                )
        return messages

    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """Main entry point: submit a message and run the agent loop."""
        user_msg = Message(role=Role.USER, content=prompt)
        self.state.messages.append(user_msg)
        self.session.record_user_message(prompt)

        system_prompt = self.build_system_prompt()
        tools = self.registry.all_tools()

        async for event in self._loop(system_prompt, tools):
            yield event

    async def _loop(
        self,
        system_prompt: str,
        tools: list[Any],
    ) -> AsyncGenerator[str, None]:
        """Core while-true agent loop."""
        tool_context = ToolContext(cwd=self.settings.cwd)
        turn = 0

        while True:
            turn += 1
            if turn > self.settings.session.max_turns:
                yield f"\n[bold yellow]Max turns ({self.settings.session.max_turns}) reached.[/bold yellow]"
                return

            max_ctx = self.settings.session.max_context_tokens
            threshold = int(max_ctx * self.settings.session.compact_threshold)
            current_tokens = estimate_messages_tokens(self.state.messages)
            if current_tokens > threshold:
                self.state.messages = compact_messages(self.state.messages, threshold)

            if self.settings.verbose:
                self.console.print(f"[dim]Turn {turn} — {len(self.state.messages)} messages[/dim]")

            messages = self._to_provider_messages()

            try:
                assistant_text = ""
                tool_uses: list[dict[str, Any]] = []
                current_tool_input = ""

                async for event in self.provider.stream(system_prompt, messages, tools):
                    if event.type == "content_delta":
                        assistant_text += event.content
                        yield event.content

                    elif event.type == "tool_use_start":
                        current_tool_input = ""

                    elif event.type == "tool_use_delta":
                        current_tool_input += event.tool_input_delta

                    elif event.type == "tool_use_complete":
                        tool_uses.append(
                            {
                                "id": event.tool_use_id or f"call_{len(tool_uses)}",
                                "name": event.tool_name,
                                "input": event.tool_input_complete or {},
                            }
                        )

                    elif event.type == "usage":
                        if event.usage:
                            self.state.usage.input_tokens = event.usage.get("input_tokens", 0)
                            self.state.usage.output_tokens = event.usage.get("output_tokens", 0)

                    elif event.type == "done":
                        stop_reason = event.stop_reason
                        if stop_reason == "max_tokens":
                            yield "\n\n[bold red]Max tokens reached.[/bold red]"
                            return

                        if stop_reason == "end_turn":
                            if assistant_text:
                                self.state.messages.append(Message(role=Role.ASSISTANT, content=assistant_text))
                                self.session.record_assistant_message(assistant_text)
                            return

                        if stop_reason == "tool_use" and tool_uses:
                            if assistant_text:
                                self.session.record_assistant_message(assistant_text, tool_uses)

                            # Yield tool execution start markers
                            for tu in tool_uses:
                                input_preview = json.dumps(tu["input"], ensure_ascii=False)[:80]
                                yield f"{TOOL_STATUS_PREFIX}{tu['name']} {input_preview}"

                            tool_results = await self._execute_tools(tool_uses, tool_context)

                            # Yield tool execution done markers
                            for i, tr in enumerate(tool_results):
                                tool_name = tool_uses[i]["name"] if i < len(tool_uses) else "unknown"
                                status = "error" if tr.is_error else "done"
                                yield f"{TOOL_DONE_PREFIX}{tool_name} [{status}]"

                            assistant_msg = Message(
                                role=Role.ASSISTANT,
                                content=assistant_text if assistant_text else "[tool execution]",
                                tool_uses=tool_uses,
                            )
                            self.state.messages.append(assistant_msg)

                            for tr in tool_results:
                                self.state.messages.append(
                                    Message(
                                        role=Role.TOOL,
                                        content=tr.content,
                                        tool_use_id=tr.tool_use_id,
                                        is_error=tr.is_error,
                                    )
                                )
                                self.session.record_tool_result(
                                    tool_name=tr.tool_use_id.split(":")[0] if ":" in tr.tool_use_id else "unknown",
                                    tool_use_id=tr.tool_use_id,
                                    content=tr.content,
                                    is_error=tr.is_error,
                                )

                    elif event.type == "error":
                        error_msg = event.error or "Unknown error"
                        if (
                            "utf-8" in error_msg.lower()
                            or "decode" in error_msg.lower()
                            or "encoding" in error_msg.lower()
                        ):
                            yield "\n[dim yellow]Streaming error, retrying without streaming...[/dim yellow]\n"
                            async for fallback_event in self._fallback_to_send(
                                system_prompt, messages, tools, tool_context
                            ):
                                yield fallback_event
                            return
                        yield f"\n[bold red]Provider error: {error_msg}[/bold red]"
                        return

            except UnicodeDecodeError:
                yield "\n[dim yellow]Encoding error, retrying without streaming...[/dim yellow]\n"
                try:
                    async for event in self._fallback_to_send(system_prompt, messages, tools, tool_context):
                        yield event
                except Exception as fallback_err:
                    yield f"\n[bold red]Fallback also failed: {fallback_err}[/bold red]"
                return

            except Exception as e:
                error_msg = f"\n[bold red]Error: {e}[/bold red]"
                yield error_msg
                self.state.messages.append(Message(role=Role.ASSISTANT, content=f"Error occurred: {e}"))
                return

    async def _fallback_to_send(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[Any],
        context: ToolContext,
    ) -> AsyncGenerator[str, None]:
        """Fallback to non-streaming API call. Loops until model stops requesting tools."""
        max_fallback_turns = 10
        for _ in range(max_fallback_turns):
            try:
                result = await self.provider.send(system_prompt, self._to_provider_messages(), tools)
            except Exception as e:
                yield f"\n[bold red]Fallback error: {e}[/bold red]"
                return

            content = result.get("content", "")
            tool_uses = result.get("tool_uses", [])
            usage = result.get("usage", {})

            if content:
                yield content

            if usage:
                self.state.usage.input_tokens = usage.get("input_tokens", 0)
                self.state.usage.output_tokens = usage.get("output_tokens", 0)

            if tool_uses:
                assistant_msg = Message(
                    role=Role.ASSISTANT,
                    content=content if content else "[tool execution]",
                    tool_uses=tool_uses,
                )
                self.state.messages.append(assistant_msg)
                if content:
                    self.session.record_assistant_message(content, tool_uses)

                tool_results = await self._execute_tools(tool_uses, context)
                for tr in tool_results:
                    self.state.messages.append(
                        Message(role=Role.TOOL, content=tr.content, tool_use_id=tr.tool_use_id, is_error=tr.is_error)
                    )
                continue

            if content:
                self.state.messages.append(Message(role=Role.ASSISTANT, content=content))
                self.session.record_assistant_message(content)
            return

    async def _execute_tools(
        self,
        tool_uses: list[dict[str, Any]],
        context: ToolContext,
    ) -> list[ToolResult]:
        """Execute tool calls with permission checking."""
        results: list[ToolResult] = []
        serial_tools = []
        concurrent_tools = []

        for tu in tool_uses:
            tool = self.registry.get(tu["name"])
            if tool is None:
                results.append(
                    ToolResult(
                        tool_use_id=tu["id"],
                        content=f"Unknown tool: {tu['name']}",
                        is_error=True,
                    )
                )
                continue

            if tool.is_concurrency_safe:
                concurrent_tools.append((tu, tool))
            else:
                serial_tools.append((tu, tool))

        for tu, tool in serial_tools:
            result = await self._run_single_tool(tu, tool, context)
            results.append(result)

        if concurrent_tools:
            tasks = [self._run_single_tool(tu, tool, context) for tu, tool in concurrent_tools]
            concurrent_results = await asyncio.gather(*tasks)
            results.extend(concurrent_results)

        return results

    async def _run_single_tool(
        self,
        tool_use: dict[str, Any],
        tool: Any,
        context: ToolContext,
    ) -> ToolResult:
        """Run a single tool with permission check."""
        tool_input = tool_use["input"]
        tool_id = tool_use["id"]

        try:
            parsed_args = tool.parse_args(tool_input)
        except (TypeError, ValueError) as e:
            return ToolResult(tool_use_id=tool_id, content=f"Invalid tool input: {e}", is_error=True)

        perm_result = tool.check_permissions(parsed_args, context)
        if perm_result.behavior == PermissionBehavior.DENY:
            return ToolResult(tool_use_id=tool_id, content=f"Permission denied: {perm_result.message}", is_error=True)

        if perm_result.behavior == PermissionBehavior.ASK:
            return ToolResult(
                tool_use_id=tool_id,
                content=f"Permission required (auto-denied in current mode): {perm_result.message}",
                is_error=True,
            )

        validation_error = tool.validate_input(parsed_args, context)
        if validation_error:
            return ToolResult(tool_use_id=tool_id, content=f"Validation error: {validation_error}", is_error=True)

        try:
            result = await tool.call(parsed_args, context, can_use_tool=None)
            result.tool_use_id = tool_id
            result.content = tool.truncate_result(result.content)
            return result
        except Exception as e:
            return ToolResult(tool_use_id=tool_id, content=f"Tool execution error: {e}", is_error=True)
