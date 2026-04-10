from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.agents.registry import AgentTypeRegistry


def _registry() -> AgentTypeRegistry:
    reg = AgentTypeRegistry()
    for a in BUILTIN_AGENTS:
        reg.register(a)
    return reg


def test_six_builtin_agents() -> None:
    assert len(BUILTIN_AGENTS) == 6


def test_code_reviewer_agent() -> None:
    reg = _registry()
    defn = reg.get("code-reviewer")
    assert defn is not None
    assert defn.max_turns == 15
    assert "FileRead" in defn.allowed_tools or "Read" in defn.allowed_tools
    assert "Bash" not in defn.allowed_tools
    assert "FileWrite" not in defn.allowed_tools


def test_git_management_agent() -> None:
    reg = _registry()
    defn = reg.get("git-management")
    assert defn is not None
    assert defn.max_turns == 20


def test_test_writer_agent() -> None:
    reg = _registry()
    defn = reg.get("test-writer")
    assert defn is not None
    assert defn.max_turns == 30
    assert "*" in defn.allowed_tools or "FileWrite" in defn.allowed_tools


def test_load_custom_yaml_agents() -> None:
    import os
    import tempfile

    import yaml

    yml_content = {
        "name":          "security-auditor",
        "description":   "OWASP security analysis",
        "max_turns":     25,
        "allowed_tools": ["FileRead", "Glob", "Grep", "Bash"],
        "system_prompt": "You are a security expert.",
    }
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "security-auditor.yml")
        with open(path, "w") as f:
            yaml.dump(yml_content, f)
        reg = AgentTypeRegistry()
        reg.load_from_dir(d)
        defn = reg.get("security-auditor")
    assert defn is not None
    assert defn.max_turns == 25
    assert "Bash" in defn.allowed_tools


def test_load_from_nonexistent_dir_is_noop() -> None:
    reg = AgentTypeRegistry()
    reg.load_from_dir("/nonexistent/path/xyz")
    assert reg.all() == []
