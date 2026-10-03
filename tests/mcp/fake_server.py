"""A small MCP server on stdio for the client tests; run with ``python fake_server.py``.

It speaks both protocol revisions (the SDK server does) and serves:

- tools: ``echo`` (returns its arguments), ``fail`` (an ``isError`` result), ``ask`` (answers with
  ``input_required`` first, an elicitation, and reports the answer on the retry), ``list_count`` (how many
  ``tools/list`` requests this process served, to show the client cache at work);
- skills over MCP (``io.modelcontextprotocol/skills``): one skill with a supporting file. The environment
  variable ``FAKE_TAMPER`` names a file path that is served with other bytes than the manifest lists, and
  ``FAKE_ALLOWED_TOOLS`` adds an ``allowed-tools`` field to the skill.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import anyio
import mcp_types as types
from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.server.caching import CacheHint
from mcp.server.stdio import stdio_server

SKILL_URI   = "skill://code-review/SKILL.md"
CHECKLIST   = "skill://code-review/references/checklist.md"
EXTENSION   = "io.modelcontextprotocol/skills"
TAMPER      = os.environ.get("FAKE_TAMPER", "")
ALLOWED     = os.environ.get("FAKE_ALLOWED_TOOLS", "")

FRONTMATTER = {"name": "code-review", "description": "Review code using the team's checklist."}
if ALLOWED:
    FRONTMATTER["allowed-tools"] = ALLOWED

_HEAD = "\n".join(f"{key}: {value}" for key, value in FRONTMATTER.items())
FILES = {
    SKILL_URI: f"---\n{_HEAD}\n---\n\n# Code review\n\nRead `references/checklist.md`, then review the diff.\n",
    CHECKLIST: "# Review checklist\n\nCheck correctness, tests, and compatibility.\n",
}
SERVED = {uri: ("tampered content\n" if uri == TAMPER else text) for uri, text in FILES.items()}

LIST_CALLS = 0


class _Params(types.RequestParams):
    uri:    str = ""
    cursor: str | None = None


class _Result(types.Result):
    model_config = {**types.Result.model_config, "extra": "allow"}


def _manifest() -> list[dict[str, Any]]:
    return [
        {"uri": uri, "digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(), "size": len(text.encode())}
        for uri, text in FILES.items()
    ]


def _entry() -> dict[str, Any]:
    return {"uri": SKILL_URI, "frontmatter": FRONTMATTER, "resources": _manifest()}


def _schema(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}}


async def list_tools(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
    global LIST_CALLS
    LIST_CALLS += 1
    return types.ListToolsResult(tools=[
        types.Tool(name="echo", description="Echo the arguments", input_schema=_schema({"text": {"type": "string"}})),
        types.Tool(name="fail", description="Always fails", input_schema=_schema()),
        types.Tool(name="ask", description="Asks the user for a color", input_schema=_schema()),
        types.Tool(name="list_count", description="Count tools/list requests", input_schema=_schema()),
    ])


def _text(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


async def call_tool(
    ctx: ServerRequestContext[Any], params: types.CallToolRequestParams,
) -> types.CallToolResult | types.InputRequiredResult:
    if params.name == "echo":
        return _text(json.dumps(params.arguments or {}, sort_keys=True))
    if params.name == "fail":
        return _text("it failed", is_error=True)
    if params.name == "list_count":
        return _text(str(LIST_CALLS))
    if params.input_responses:
        answer = params.input_responses["color"]
        return _text("answer=" + json.dumps(answer.model_dump(by_alias=True, mode="json", exclude_none=True), sort_keys=True))
    question = types.ElicitRequest(params=types.ElicitRequestFormParams(
        message="Pick a color",
        requested_schema={"type": "object", "properties": {"color": {"type": "string", "enum": ["red", "blue"]}}},
    ))
    return types.InputRequiredResult(input_requests={"color": question}, request_state="state-1")


async def read_resource(ctx: ServerRequestContext[Any], params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
    if params.uri not in SERVED:
        raise MCPError(code=types.INVALID_PARAMS, message=f"unknown resource {params.uri}")
    content = types.TextResourceContents(uri=params.uri, mime_type="text/markdown", text=SERVED[params.uri])
    return types.ReadResourceResult(contents=[content])


async def skills_list(ctx: ServerRequestContext[Any], params: _Params) -> _Result:
    return _Result.model_validate({"resultType": "complete", "skills": [_entry()], "ttlMs": 0, "cacheScope": "private"})


async def skills_get(ctx: ServerRequestContext[Any], params: _Params) -> _Result:
    return _Result.model_validate({"resultType": "complete", "skill": _entry(), "ttlMs": 0, "cacheScope": "private"})


def build() -> Server[Any]:
    server = Server(
        "fake",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_read_resource=read_resource,
        cache_hints={"tools/list": CacheHint(ttl_ms=60_000, scope="public")},
    )
    server.extensions[EXTENSION] = {}
    server.add_request_handler("skills/list", _Params, skills_list)
    server.add_request_handler("skills/get", _Params, skills_get)
    return server


async def main() -> None:
    server = build()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
