"""Running an ACP agent on the process's stdio until the editor closes the connection.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import cast

from acp import Agent, run_agent

from nerdvana_cli.acp.agent import NerdvanaAcpAgent
from nerdvana_cli.acp.stdio import protocol_streams


async def serve(agent: NerdvanaAcpAgent) -> None:
    """Answer protocol requests on stdin and stdout; every session is closed when the connection ends."""
    reader, writer = await protocol_streams()
    # The SDK routes a method to the agent only when the agent defines it; this agent defines the ones it supports.
    try:
        await run_agent(cast(Agent, agent), input_stream=writer, output_stream=reader)
    finally:
        await agent.shutdown()
