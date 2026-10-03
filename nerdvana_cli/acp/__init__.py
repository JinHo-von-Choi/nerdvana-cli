"""Agent Client Protocol (ACP) agent mode: ``nerdvana acp`` serves the agent loop to an editor over stdio.

Author: 최진호
Date:   2026-10-03

The package needs the optional ``acp`` extra (``agent-client-protocol``). Only ``command`` is imported
when the CLI starts; everything that touches the SDK is imported when the command runs.
"""
