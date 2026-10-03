"""Entry point for the subprocess tests: ``nerdvana acp`` with a scripted model in place of a provider.

Author: 최진호
Date:   2026-10-03

The script is read from the file named by ``ACP_FAKE_SCRIPT``: a JSON list of model turns, each a list of
provider events given as keyword arguments of ``ProviderEvent``. The system prompt builder prints a line to
standard output on purpose, to show that nothing but protocol frames reaches the editor.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.main import app
from nerdvana_cli.providers.base import ProviderEvent


class _Scripted:
    def __init__(self, turns: list[list[ProviderEvent]]) -> None:
        self.turns = turns
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> Any:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        for event in turn:
            yield event


def _system_prompt(loop: AgentLoop) -> str:
    print("stray line on standard output")
    os.write(1, b"stray bytes on descriptor 1\n")
    return "system"


def main() -> None:
    with open(os.environ["ACP_FAKE_SCRIPT"], encoding="utf-8") as handle:
        turns = [[ProviderEvent(**event) for event in turn] for turn in json.load(handle)]
    provider = _Scripted(turns)
    AgentLoop.create_provider_from_settings = lambda self: provider  # type: ignore[method-assign,assignment,misc]
    AgentLoop.build_system_prompt            = _system_prompt  # type: ignore[method-assign]
    app(["acp", *sys.argv[1:]])


if __name__ == "__main__":
    main()
