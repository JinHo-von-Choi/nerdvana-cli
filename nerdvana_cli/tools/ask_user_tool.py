"""AskUser tool: put a clarifying question to the user instead of guessing.

The question reaches the user through the ``ask_user`` callback carried by
:class:`~nerdvana_cli.core.tool.ToolContext`. Only an interactive front end
(the TUI) installs that callback; one-shot runs, MCP server mode, subagents and
background tasks leave it unset, and the tool then reports that no user is
reachable so the model can continue on its own judgment.
"""

from __future__ import annotations

from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

MAX_OPTIONS = 4

_NO_USER_MESSAGE = (
    "No user is available to answer in this session. Proceed with your best "
    "judgment and state the assumptions you made, or stop and report what "
    "information is missing."
)

_NO_ANSWER_MESSAGE = (
    "The user dismissed the question without answering. Proceed with your best "
    "judgment and state the assumptions you made, or stop and report what "
    "information is missing."
)


class AskUserArgs:
    """Arguments of one AskUser call."""

    def __init__(self, question: str, options: list[str] | None = None) -> None:
        self.question = question
        self.options  = options if options is not None else []


class AskUserTool(BaseTool[AskUserArgs]):
    """Ask the user a question with optional suggested answers."""

    name             = "AskUser"
    description_text = (
        "Ask the user a clarifying question when requirements are ambiguous or "
        "progress is blocked, instead of guessing. Provide 2 to 4 short suggested "
        "options when the answer space is small; the user may pick one by number or "
        "type a free-text answer. The result is the answer text. When no user is "
        "available the call returns an error: proceed with your best judgment and "
        "state your assumptions, or stop and report. Do not use it for questions "
        "you can answer by reading the code."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type":        "string",
                "description": "The question to put to the user",
            },
            "options": {
                "type":        "array",
                "items":       {"type": "string"},
                "maxItems":    MAX_OPTIONS,
                "description": "Suggested answers (2 to 4); the user may also answer in free text",
            },
        },
        "required": ["question"],
    }
    args_class          = AskUserArgs
    is_concurrency_safe = False

    category:     ClassVar[ToolCategory]   = ToolCategory.META
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.NONE

    def validate_input(self, args: AskUserArgs, context: ToolContext) -> str | None:
        """Reject an empty question and a malformed or oversized option list."""
        if not isinstance(args.question, str) or not args.question.strip():
            return "question must be a non-empty string"
        if not isinstance(args.options, list):
            return "options must be an array of strings"
        if len(args.options) > MAX_OPTIONS:
            return f"options accepts at most {MAX_OPTIONS} items, got {len(args.options)}"
        for index, option in enumerate(args.options):
            if not isinstance(option, str) or not option.strip():
                return f"options[{index}] must be a non-empty string"
        return None

    async def call(
        self,
        args:         AskUserArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        """Relay the question to the user and return the answer as the result."""
        ask = context.ask_user
        if ask is None:
            return ToolResult(tool_use_id="", content=_NO_USER_MESSAGE, is_error=True)

        answer = await ask(args.question.strip(), [option.strip() for option in args.options])
        if answer is None or not answer.strip():
            return ToolResult(tool_use_id="", content=_NO_ANSWER_MESSAGE, is_error=True)
        return ToolResult(tool_use_id="", content=answer.strip())
