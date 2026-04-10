from nerdvana_cli.tools.registry import create_subagent_registry


def _names(registry) -> set[str]:
    return {t.name for t in registry.all_tools()}


def test_wildcard_allows_all_tools() -> None:
    registry = create_subagent_registry(allowed_tools=["*"])
    names = _names(registry)
    assert "Bash" in names
    assert "FileRead" in names
    assert "FileWrite" in names
    assert "Glob" in names
    assert "Grep" in names


def test_filtered_registry_only_has_allowed() -> None:
    registry = create_subagent_registry(allowed_tools=["Glob", "Grep", "FileRead"])
    names = _names(registry)
    assert "Glob" in names
    assert "Grep" in names
    assert "FileRead" in names
    assert "Bash" not in names
    assert "FileWrite" not in names
    assert "FileEdit" not in names


def test_empty_allowed_tools_returns_empty() -> None:
    registry = create_subagent_registry(allowed_tools=[])
    assert _names(registry) == set()


def test_bash_filter_allows_bash() -> None:
    registry = create_subagent_registry(allowed_tools=["Bash", "FileRead"])
    names = _names(registry)
    assert "Bash" in names
    assert "FileRead" in names
    assert "FileWrite" not in names
