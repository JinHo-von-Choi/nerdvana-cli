"""Agent type registry — maps type names to definitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentDefinition:
    """Defines how a specific agent type is configured."""

    agent_type:    str
    description:   str
    max_turns:     int       = 50
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    system_prompt: str       = ""


class AgentTypeRegistry:
    """Maps agent type name strings to AgentDefinition objects."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, defn: AgentDefinition) -> None:
        self._agents[defn.agent_type] = defn

    def get(self, agent_type: str) -> AgentDefinition | None:
        return self._agents.get(agent_type)

    def all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def load_from_dir(self, directory: str) -> None:
        """Load AgentDefinition objects from *.yml files in directory.

        Silently skips the directory if it doesn't exist or pyyaml is
        unavailable. Each YAML file must have at least a `name` key.
        """
        import glob
        import os

        if not os.path.isdir(directory):
            return

        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return

        for yml_path in glob.glob(os.path.join(directory, "*.yml")):
            try:
                with open(yml_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict) or "name" not in data:
                    continue
                defn = AgentDefinition(
                    agent_type    = data["name"],
                    description   = data.get("description", ""),
                    max_turns     = int(data.get("max_turns", 50)),
                    allowed_tools = list(data.get("allowed_tools", ["*"])),
                    system_prompt = data.get("system_prompt", ""),
                )
                self.register(defn)
            except Exception:  # noqa: BLE001
                continue
