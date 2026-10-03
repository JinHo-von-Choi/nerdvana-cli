"""Span names and attribute names for the OpenTelemetry traces.

Author: 최진호
Date:   2026-10-03

Every ``gen_ai.*`` attribute of the OpenTelemetry GenAI semantic conventions is still at the
Development stability level, so a name can change in a later revision of the conventions. All of
them live in this one table; a rename is a change to this file and nothing else. The module
imports nothing: it is read whether or not the OpenTelemetry SDK is installed.
"""

from __future__ import annotations


class Attr:
    """Attribute names written on the spans."""

    OPERATION_NAME      = "gen_ai.operation.name"
    PROVIDER_NAME       = "gen_ai.provider.name"
    REQUEST_MODEL       = "gen_ai.request.model"
    CONVERSATION_ID     = "gen_ai.conversation.id"
    AGENT_ID            = "gen_ai.agent.id"
    AGENT_NAME          = "gen_ai.agent.name"
    USAGE_INPUT_TOKENS  = "gen_ai.usage.input_tokens"
    USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    USAGE_CACHE_READ    = "gen_ai.usage.cache_read.input_tokens"
    USAGE_CACHE_WRITE   = "gen_ai.usage.cache_write.input_tokens"
    TOOL_NAME           = "gen_ai.tool.name"
    TOOL_TYPE           = "gen_ai.tool.type"
    TOOL_CALL_ID        = "gen_ai.tool.call.id"
    TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
    TOOL_CALL_RESULT    = "gen_ai.tool.call.result"
    INPUT_MESSAGES      = "gen_ai.input.messages"
    ERROR_TYPE          = "error.type"
    COST_USD            = "nerdvana.cost_usd"
    TURN                = "nerdvana.turn"


class Operation:
    """Values of ``gen_ai.operation.name``; the span name is the operation followed by its target."""

    INVOKE_AGENT = "invoke_agent"
    CHAT         = "chat"
    EXECUTE_TOOL = "execute_tool"


# gen_ai.tool.type for the tools the agent runs itself.
TOOL_TYPE_FUNCTION = "function"

# error.type of a tool call whose result says it failed, and of a span closed without a result.
ERROR_TOOL    = "tool_error"
ERROR_ABORTED = "aborted"

# Provider names as the conventions spell them where they differ from nerdvana's own.
PROVIDER_NAMES: dict[str, str] = {
    "gemini": "gcp.gemini",
}

# Tools that start sub-agents: the spans of those agents hang below the open call of one of these.
SUBAGENT_TOOLS = frozenset({"Agent", "Swarm"})


def provider_name(provider: str) -> str:
    """The convention's name for *provider*."""
    return PROVIDER_NAMES.get(provider, provider)
