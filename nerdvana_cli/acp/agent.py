"""The ACP agent: the protocol methods of ``nerdvana acp``, mapped onto sessions of the agent loop.

Author: 최진호
Date:   2026-10-03

Protocol version 1 only. A client that asks for a newer version is answered with version 1 and decides
for itself whether to continue. File reads, file writes and shell commands run through the local tools:
the editor's ``fs/*`` and ``terminal/*`` methods are not used even when the client offers them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PromptCapabilities,
    PromptResponse,
    SessionCapabilities,
)

from nerdvana_cli import __version__
from nerdvana_cli.acp.launch import LaunchError, LaunchOptions, settings_for
from nerdvana_cli.acp.session import AcpSession
from nerdvana_cli.core.state.session import SessionStorage, resume_session_id

logger = logging.getLogger(__name__)


class NerdvanaAcpAgent:
    """Implements the ``acp.Agent`` protocol; every session owns its own agent loop."""

    def __init__(self, options: LaunchOptions) -> None:
        self._options                              = options
        self._conn:    Any                         = None
        self._sessions: dict[str, AcpSession]      = {}
        self.client_capabilities                   = ClientCapabilities()

    def on_connect(self, conn: Any) -> None:
        """Called by the SDK with the connection that carries the agent's requests and updates to the client."""
        self._conn = conn

    async def initialize(
        self,
        protocol_version:    int,
        client_capabilities: ClientCapabilities | None = None,
        client_info:         Implementation | None     = None,
        **kwargs:            Any,
    ) -> InitializeResponse:
        """Negotiate the version and describe what the agent supports."""
        self.client_capabilities = client_capabilities or ClientCapabilities()
        return InitializeResponse(
            protocol_version   = PROTOCOL_VERSION,
            agent_capabilities = AgentCapabilities(
                load_session         = True,
                prompt_capabilities  = PromptCapabilities(image=True, audio=False, embedded_context=True),
                mcp_capabilities     = McpCapabilities(http=True, sse=True),
                session_capabilities = SessionCapabilities(),
            ),
            agent_info   = Implementation(name="nerdvana", title="NerdVana CLI", version=__version__),
            auth_methods = [],
        )

    async def new_session(
        self,
        cwd:                    str,
        additional_directories: list[str] | None = None,
        mcp_servers:            list[Any] | None = None,
        **kwargs:               Any,
    ) -> NewSessionResponse:
        """Start a session in *cwd*; the editor's MCP servers are connected for it alone."""
        session = await self._open(cwd, mcp_servers)
        self._sessions[session.session_id] = session
        session.publish_commands()
        return NewSessionResponse(session_id=session.session_id)

    async def load_session(
        self,
        cwd:                    str,
        session_id:             str,
        mcp_servers:            list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs:               Any,
    ) -> LoadSessionResponse | None:
        """Resume a recorded session: its conversation is replayed to the client before the answer."""
        session = self._sessions.get(session_id)
        if session is None:
            session = await self._open(cwd, mcp_servers, session_id)
            self._sessions[session.session_id] = session
        await session.replay()
        session.publish_commands()
        return LoadSessionResponse()

    async def _open(self, cwd: str, mcp_servers: list[Any] | None, session_id: str | None = None) -> AcpSession:
        if not os.path.isabs(cwd) or not os.path.isdir(cwd):
            raise RequestError.invalid_params({"message": f"cwd must be an existing absolute directory: {cwd}"})
        if session_id is not None:
            self._require_recorded(session_id)
        try:
            settings = settings_for(self._options, cwd)
        except LaunchError as exc:
            if exc.auth_required:
                raise RequestError.auth_required({"message": str(exc)}) from exc
            raise RequestError.internal_error({"details": str(exc)}) from exc
        return await AcpSession.open(self._conn, settings, mcp_servers, session_id, restore=session_id is not None)

    @staticmethod
    def _require_recorded(session_id: str) -> None:
        known = resume_session_id(session_id)
        if known is None or not os.path.exists(SessionStorage(session_id=known, persist=False).file_path):
            raise RequestError.resource_not_found(session_id)

    def _session(self, session_id: str) -> AcpSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise RequestError.invalid_params({"message": f"unknown session: {session_id}"})
        return session

    async def prompt(self, prompt: list[Any], session_id: str, **kwargs: Any) -> PromptResponse:
        """Run one prompt turn of a session; updates stream while it runs and the answer carries the stop reason."""
        return await self._session(session_id).prompt(prompt)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Abort the running turn of a session; an unknown session is ignored, as the protocol sends no answer."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.cancel()

    async def shutdown(self) -> None:
        """End every session: the editor has closed the connection."""
        await asyncio.gather(*(s.close() for s in self._sessions.values()), return_exceptions=True)
        self._sessions.clear()
