"""Advisor: ask a stronger model for guidance at a decision point (the consultation itself is ``core/advisor.py``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult


@dataclass
class AdvisorArgs:
    question: str


class AdvisorTool(BaseTool[AdvisorArgs]):
    """Put a short question to the model named by ``advisor.model`` and return its guidance."""

    name             = "Advisor"
    description_text = (
        "Ask a stronger model for guidance at a decision point: choosing between approaches, before a change that is "
        "hard to undo, or when you are stuck after repeated failures. Pass one short, specific question that states "
        "the options you see. The advisor reads only your question and an excerpt of the recent conversation (tool "
        "output shortened, secrets masked), cannot use tools and does no work; it answers with advice, which you "
        "weigh and carry out yourself. The number of consultations per run is limited, so do not use it for routine steps."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The decision or problem, with the options you see, in a few sentences."},
        },
        "required": ["question"],
    }
    args_class                       = AdvisorArgs
    category: ClassVar[ToolCategory] = ToolCategory.META
    side_effects                     = ToolSideEffect.EXTERNAL
    tags: ClassVar[frozenset[str]]   = frozenset({"advisor"})
    is_concurrency_safe              = False

    def validate_input(self, args: AdvisorArgs, context: ToolContext) -> str | None:
        return None if args.question.strip() else "question must not be empty"

    async def call(self, args: AdvisorArgs, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        advisor = context.state.get("advisor")
        if advisor is None:
            return ToolResult(tool_use_id="", content="The advisor is not available in this session.", is_error=True)
        advice = await advisor.advise(args.question)
        return ToolResult(tool_use_id="", content=advice.text, is_error=not advice.ok)
