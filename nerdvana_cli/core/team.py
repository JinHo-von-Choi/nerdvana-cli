"""Team model and file-based mailbox for multi-agent communication."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Mailbox types
# ---------------------------------------------------------------------------

@dataclass
class TeammateMessage:
    from_agent: str
    text:       str
    summary:    str  = ""
    read:       bool = False
    timestamp:  str  = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


def get_inbox_path(
    agent_name: str,
    team_name:  str,
    base_dir:   str = "",
) -> str:
    """Return the inbox JSON path for an agent in a team.

    Default base_dir: ~/.nerdvana/teams/
    """
    if not base_dir:
        base_dir = os.path.join(os.path.expanduser("~"), ".nerdvana", "teams")
    safe_team  = _sanitize(team_name)
    safe_agent = _sanitize(agent_name)
    return str(Path(base_dir) / safe_team / "inboxes" / f"{safe_agent}.json")


async def write_to_inbox(inbox_path: str, msg: TeammateMessage) -> None:
    """Append a message to the agent's inbox, creating the file if absent."""
    path = Path(inbox_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    messages = await read_inbox(inbox_path)
    messages.append(msg)
    data = [asdict(m) for m in messages]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


async def read_inbox(inbox_path: str) -> list[TeammateMessage]:
    """Read all messages from an inbox file. Returns [] if absent."""
    path = Path(inbox_path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [TeammateMessage(**m) for m in raw]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    """Replace path-unsafe characters with underscores."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


# ---------------------------------------------------------------------------
# Team model
# ---------------------------------------------------------------------------

@dataclass
class TeamMember:
    agent_id:  str
    name:      str
    team_name: str


@dataclass
class Team:
    name:    str
    members: dict[str, TeamMember] = field(default_factory=dict)


class TeamRegistry:
    """In-memory registry of active teams."""

    def __init__(self) -> None:
        self._teams: dict[str, Team] = {}

    def create(self, name: str) -> Team:
        team = Team(name=name)
        self._teams[name] = team
        return team

    def get(self, name: str) -> Team | None:
        return self._teams.get(name)

    def get_member(self, agent_id: str) -> TeamMember | None:
        for team in self._teams.values():
            if agent_id in team.members:
                return team.members[agent_id]
        return None

    def register_member(self, team_name: str, member: TeamMember) -> None:
        team = self._teams.get(team_name)
        if team is None:
            team = self.create(team_name)
        team.members[member.agent_id] = member


# Mailbox alias kept for backward compat with tests
Mailbox = TeammateMessage
