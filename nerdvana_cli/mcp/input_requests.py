"""Answering a server's request for more input through the existing user-question mechanism.

A server on the 2026-07-28 revision may answer a tool call with ``input_required``: a list of questions
(elicitations) and an opaque state to send back with the answers. The SDK client drives that exchange and
hands each question to the callback below. It is put to the user through the ``ask_user`` callback that
the calling tool context carries, the one the ``AskUser`` tool uses, and refused with a plain message when
no user can be asked (a one-shot run, a sub-agent, a background task).

Only ``answer_elicitation`` needs the SDK, and imports it when called, so that tools and skills load without it.

The callback reads the question channel from a context variable that ``bind_ask_user`` sets around one tool
call. A server that still speaks the 2025 revision sends its question as a separate request while the call
is open; that request is not run in the caller's context and is refused. Sampling and roots requests are
always refused: the specification deprecates both.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.tool import AskUserCallback

if TYPE_CHECKING:
    import mcp_types as types
    from mcp.client.session import ClientRequestContext

_ASK_USER: ContextVar[AskUserCallback | None] = ContextVar("mcp_ask_user", default=None)

MAX_CHOICES = 4
_YES        = ("yes", "y", "true", "1")
_NO         = ("no", "n", "false", "0")

NO_USER_MESSAGE = "This MCP server asked for more input, but no user is available to answer in this session."


@contextmanager
def bind_ask_user(ask: AskUserCallback | None) -> Iterator[None]:
    """Let server questions raised by the calls made inside this block reach the user through *ask*."""
    token = _ASK_USER.set(ask)
    try:
        yield
    finally:
        _ASK_USER.reset(token)


def _refusal(message: str) -> types.ErrorData:
    import mcp_types as types

    return types.ErrorData(code=types.INVALID_REQUEST, message=message)


def _choices(spec: dict[str, Any]) -> list[str]:
    """The suggested answers for one schema property: enum values, or yes and no for a boolean."""
    if spec.get("type") == "boolean":
        return ["Yes", "No"]
    enum = spec.get("enum")
    if isinstance(enum, list) and 0 < len(enum) <= MAX_CHOICES:
        return [str(value) for value in enum]
    return []


def _label(name: str, spec: dict[str, Any]) -> str:
    title       = str(spec.get("title") or name)
    description = str(spec.get("description") or "")
    return f"{title}: {description}" if description else title


def _convert(answer: str, spec: dict[str, Any]) -> Any:
    """The typed value for *answer*; ValueError when it does not fit the property."""
    kind = spec.get("type")
    text = answer.strip()
    if kind == "boolean":
        if text.lower() in _YES:
            return True
        if text.lower() in _NO:
            return False
        raise ValueError("not a yes or no answer")
    if kind == "integer":
        return int(text)
    if kind == "number":
        return float(text)
    if kind == "array":
        return [item.strip() for item in text.split(",") if item.strip()]
    enum = spec.get("enum")
    if isinstance(enum, list) and text not in [str(value) for value in enum]:
        raise ValueError("not one of the allowed values")
    return text


async def answer_elicitation(
    context: ClientRequestContext,
    params:  types.ElicitRequestParams,
) -> types.ElicitResult | types.ErrorData:
    """Put a form elicitation to the user, one property at a time; refuse a URL elicitation."""
    import mcp_types as types

    if not isinstance(params, types.ElicitRequestFormParams):
        return _refusal("This client does not support URL elicitation.")
    ask = _ASK_USER.get()
    if ask is None:
        return _refusal(NO_USER_MESSAGE)
    properties = params.requested_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        reply = await ask(params.message, ["Yes", "No"])
        return types.ElicitResult(action="accept" if (reply or "").strip().lower() in _YES else "decline")
    content: dict[str, str | int | float | bool | list[str] | None] = {}
    for name, spec in properties.items():
        spec  = spec if isinstance(spec, dict) else {}
        reply = await ask(f"{params.message}\n{_label(name, spec)}", _choices(spec))
        if reply is None or not reply.strip():
            return types.ElicitResult(action="cancel")
        try:
            content[name] = _convert(reply, spec)
        except ValueError:
            return types.ElicitResult(action="decline")
    return types.ElicitResult(action="accept", content=content)
