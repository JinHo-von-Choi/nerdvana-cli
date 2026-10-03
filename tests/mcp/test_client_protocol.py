"""Protocol revisions, extensions and `input_required` results through the SDK-based client.

The servers are real subprocesses: tests/mcp/fake_server.py (an SDK server, which serves both protocol
revisions) and tests/mcp/raw_server.py (a hand-rolled server that only knows the 2025 handshake).

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mcp_types as types
import pytest
import pytest_asyncio
from mcp.client.session import ClientRequestContext

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.input_requests import NO_USER_MESSAGE, answer_elicitation, bind_ask_user
from nerdvana_cli.mcp.tools import McpToolAdapter

HERE = Path(__file__).parent


def _config(script: str, *args: str) -> McpServerConfig:
    return McpServerConfig(name="server", transport="stdio", command=sys.executable, args=[str(HERE / script), *args])


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[McpClient]:
    client = McpClient(_config("fake_server.py"))
    await client.connect()
    yield client
    await client.disconnect()


class TestProtocolRevisions:
    @pytest.mark.asyncio
    async def test_a_server_of_the_2026_revision_is_reached_without_an_initialize_handshake(self) -> None:
        client = McpClient(_config("fake_server.py"))
        try:
            handshake = await client.connect()
        finally:
            await client.disconnect()

        assert handshake["protocolVersion"] == "2026-07-28"

    @pytest.mark.asyncio
    async def test_a_server_that_only_knows_the_2025_handshake_is_reached_too(self) -> None:
        client = McpClient(_config("raw_server.py", "ok"))
        try:
            handshake = await client.connect()
            tools     = await client.list_tools()
        finally:
            await client.disconnect()

        assert handshake["protocolVersion"] < "2026"
        assert [tool["name"] for tool in tools] == ["big"]

    @pytest.mark.asyncio
    async def test_declared_extensions_are_visible(self, fake: McpClient) -> None:
        assert "io.modelcontextprotocol/skills" in fake.extensions


class TestInputRequired:
    @pytest.mark.asyncio
    async def test_the_question_reaches_the_user_and_the_answer_goes_back_to_the_server(self, fake: McpClient) -> None:
        asked: list[tuple[str, list[str]]] = []

        async def ask(question: str, options: list[str]) -> str | None:
            asked.append((question, options))
            return "blue"

        with bind_ask_user(ask):
            result = await fake.call_tool("ask")

        assert asked == [("Pick a color\ncolor", ["red", "blue"])]
        assert json.loads(result["content"][0]["text"].removeprefix("answer=")) == {"action": "accept", "content": {"color": "blue"}}

    @pytest.mark.asyncio
    async def test_without_a_user_the_call_is_refused_with_a_clear_message(self, fake: McpClient) -> None:
        with pytest.raises(RuntimeError, match="no user is available"):
            await fake.call_tool("ask")

    @pytest.mark.asyncio
    async def test_a_dismissed_question_is_reported_to_the_server_as_cancelled(self, fake: McpClient) -> None:
        async def ask(question: str, options: list[str]) -> str | None:
            return None

        with bind_ask_user(ask):
            result = await fake.call_tool("ask")

        assert json.loads(result["content"][0]["text"].removeprefix("answer="))["action"] == "cancel"

    @pytest.mark.asyncio
    async def test_a_tool_call_through_the_adapter_uses_the_context_question_channel(self, fake: McpClient) -> None:
        async def ask(question: str, options: list[str]) -> str | None:
            return "red"

        adapter = McpToolAdapter("server", {"name": "ask", "description": "", "inputSchema": {}}, fake)
        result  = await adapter.call({}, ToolContext(ask_user=ask), None)

        assert result.is_error is False
        assert '"color": "red"' in result.content

    @pytest.mark.asyncio
    async def test_a_tool_call_through_the_adapter_without_a_user_fails_with_the_reason(self, fake: McpClient) -> None:
        adapter = McpToolAdapter("server", {"name": "ask", "description": "", "inputSchema": {}}, fake)
        result  = await adapter.call({}, ToolContext(), None)

        assert result.is_error is True
        assert "no user is available" in result.content


def _form(properties: dict[str, Any]) -> types.ElicitRequestFormParams:
    return types.ElicitRequestFormParams(message="Need input", requested_schema={"type": "object", "properties": properties})


async def _answer(params: types.ElicitRequestParams, *replies: str | None) -> types.ElicitResult | types.ErrorData:
    queue = list(replies)

    async def ask(question: str, options: list[str]) -> str | None:
        return queue.pop(0)

    with bind_ask_user(ask):
        return await answer_elicitation(None, params)  # type: ignore[arg-type]


class TestElicitationAnswers:
    @pytest.mark.asyncio
    async def test_each_property_is_asked_and_typed(self) -> None:
        params = _form({
            "ok":    {"type": "boolean"},
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "tags":  {"type": "array"},
            "name":  {"type": "string"},
        })

        result = await _answer(params, "yes", "3", "0.5", "a, b", "Ada")

        assert isinstance(result, types.ElicitResult)
        assert result.action == "accept"
        assert result.content == {"ok": True, "count": 3, "ratio": 0.5, "tags": ["a", "b"], "name": "Ada"}

    @pytest.mark.asyncio
    async def test_an_answer_that_does_not_fit_the_type_declines(self) -> None:
        result = await _answer(_form({"count": {"type": "integer"}}), "many")

        assert isinstance(result, types.ElicitResult)
        assert result.action == "decline"

    @pytest.mark.asyncio
    async def test_an_answer_outside_the_enum_declines(self) -> None:
        result = await _answer(_form({"color": {"type": "string", "enum": ["red", "blue"]}}), "green")

        assert isinstance(result, types.ElicitResult)
        assert result.action == "decline"

    @pytest.mark.asyncio
    async def test_a_url_elicitation_is_refused(self) -> None:
        params = types.ElicitRequestURLParams(message="Sign in", url="https://example.com/auth", elicitation_id="e1")

        result = await _answer(params)

        assert isinstance(result, types.ErrorData)
        assert "URL elicitation" in result.message

    @pytest.mark.asyncio
    async def test_no_user_gives_the_refusal_message(self) -> None:
        context = ClientRequestContext(session=None, request_id="k")  # type: ignore[arg-type]

        result = await answer_elicitation(context, _form({"a": {"type": "string"}}))

        assert isinstance(result, types.ErrorData)
        assert result.message == NO_USER_MESSAGE
