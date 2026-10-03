"""The stdio streams the protocol runs on, kept apart from everything else the process prints.

Author: 최진호
Date:   2026-10-03

Standard output carries JSON-RPC frames and nothing else: one stray line from a library, a tool or a child
process would corrupt a frame. The protocol therefore gets private copies of descriptors 0 and 1, and the
process's own descriptor 1 is pointed at standard error and descriptor 0 at the null device.
"""

from __future__ import annotations

import asyncio
import os
import sys

from acp import stdio_streams
from acp.core import DEFAULT_STDIO_BUFFER_LIMIT_BYTES


async def protocol_streams() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """The reader and writer for the protocol, with the process's own stdin and stdout detached from it."""
    if sys.platform == "win32":
        return await stdio_streams(limit=DEFAULT_STDIO_BUFFER_LIMIT_BYTES)
    sys.stdout.flush()
    protocol_in  = os.fdopen(os.dup(0), "rb", buffering=0)
    protocol_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    saved = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = protocol_in, protocol_out
    try:
        return await stdio_streams(limit=DEFAULT_STDIO_BUFFER_LIMIT_BYTES)
    finally:
        sys.stdin, sys.stdout = saved
