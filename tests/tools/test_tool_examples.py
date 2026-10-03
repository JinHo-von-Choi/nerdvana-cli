"""Tools whose arguments are easy to get wrong carry one concrete example call in their description.

The examples name only parameters the tool declares, so an example cannot drift from its schema.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.tools.registry import create_tool_registry

WITH_EXAMPLES = ("FileEdit", "replace_symbol_body", "Grep", "Bash", "Agent", "Swarm", "ActivateSkill", "WebFetch")

# A parameter name in an example: lower case words followed by a colon and a space.
_KEY = re.compile(r"\b([a-z][a-z_]*):\s")


@pytest.fixture(scope="module")
def tools(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path_factory.mktemp("project"))
    return {tool.name: tool for tool in create_tool_registry(settings=settings).all_tools()}


def _property_names(schema: Any) -> set[str]:
    """Every property name of a JSON schema, nested ones included."""
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names.update(value)
            names |= _property_names(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= _property_names(item)
    return names


def _example_text(description: str) -> str:
    return description[description.index("Example"):]


@pytest.mark.parametrize("name", WITH_EXAMPLES)
def test_the_description_has_an_example(tools: dict[str, Any], name: str) -> None:
    assert "Example" in tools[name].description_text


@pytest.mark.parametrize("name", WITH_EXAMPLES)
def test_the_example_names_only_declared_parameters(tools: dict[str, Any], name: str) -> None:
    tool     = tools[name]
    declared = _property_names(tool.input_schema)
    used     = set(_KEY.findall(_example_text(tool.description_text)))
    assert used, name
    assert used <= declared, (name, used - declared)


def test_an_escaped_newline_in_an_example_is_not_a_line_break(tools: dict[str, Any]) -> None:
    text = tools["FileEdit"].description_text
    assert "RETRY = 3" in text and "False\\nRETRY" in text
    assert "False\nRETRY" not in text


def test_the_added_examples_stay_small(tools: dict[str, Any]) -> None:
    from nerdvana_cli.core.token_estimator import approx_tokens

    for name in WITH_EXAMPLES:
        assert approx_tokens(_example_text(tools[name].description_text)) <= 160, name

