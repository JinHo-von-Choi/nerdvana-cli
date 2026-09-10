#!/usr/bin/env python3
"""Verify that the prose documentation still matches the code it describes.

Every claim checked here is one that has silently rotted before: environment
variable names, the built-in tool roster, the provider roster, the runtime
paths, the CLI subcommand surface, and the built-in agent tool budgets.
Hand-editing the documents restores them for one commit; this script keeps
them restored.

Run from the repository root:

    python scripts/check_docs_consistency.py

Exit status is 0 when every claim matches, 1 otherwise. Each mismatch is
printed as `file: message`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DOC_FILES: tuple[str, ...] = (
    "README.md",
    "README.ko.md",
    "NIRNA.md",
    "nerdvana.yml.example",
    "docs/configuration.md",
    "docs/hooks.md",
    "docs/agents.md",
)

LEGACY_CONFIG_DIR = "~/.config/nerdvana-cli"

# A line may name the pre-migration location only while explaining that it is
# the pre-migration location.
LEGACY_CONTEXT = re.compile(r"legacy|migrat|deprecat|마이그레이션|이전 위치|구 경로|예전", re.IGNORECASE)


@dataclass
class Report:
    """Accumulated mismatches, keyed by the document they were found in."""

    problems: list[tuple[str, str]] = field(default_factory=list)

    def add(self, doc: str, message: str) -> None:
        self.problems.append((doc, message))

    def __len__(self) -> int:
        return len(self.problems)


# ---------------------------------------------------------------------------
# Facts read out of the code
# ---------------------------------------------------------------------------


def code_env_vars() -> set[str]:
    """Every `NERDVANA_*` name a user can actually set.

    Two sources: string literals in the package and helper scripts, plus the
    names pydantic-settings derives from `NerdvanaSettings` scalar fields.
    Nested model fields are excluded on purpose: their env override expects a
    JSON document, so documenting them as plain strings would be a lie.
    """
    from nerdvana_cli.core.settings import NerdvanaSettings

    literal = re.compile(r"NERDVANA_[A-Z0-9_]+")
    names: set[str] = set()

    sources: list[Path] = [
        *(REPO_ROOT / "nerdvana_cli").rglob("*.py"),
        *(REPO_ROOT / "scripts").glob("*.py"),
        REPO_ROOT / "install.sh",
    ]
    for path in sources:
        if not path.is_file():
            continue
        names.update(literal.findall(path.read_text(encoding="utf-8")))

    scalars = (str, int, float, bool)
    for name, info in NerdvanaSettings.model_fields.items():
        if info.annotation in scalars:
            names.add(f"NERDVANA_{name.upper()}")

    return names


def code_tool_names() -> set[str]:
    """Every built-in tool name the registry can hand to a model.

    LSP and symbol tools are pulled in unconditionally here; at runtime they
    appear only when a language server is installed, but the documentation
    describes them either way.
    """
    from nerdvana_cli.core.lsp_client import LspClient
    from nerdvana_cli.core.settings import NerdvanaSettings
    from nerdvana_cli.tools.parism_tool import ParismTool
    from nerdvana_cli.tools.registry import create_tool_registry
    from nerdvana_cli.tools.symbol_tools import create_symbol_tools

    settings = NerdvanaSettings(external_projects_enabled=True)

    # Pin the language-server probe so the result does not depend on which
    # binaries happen to sit on the machine running the check.
    with patch.object(LspClient, "has_any_server", return_value=False):
        registry = create_tool_registry(settings=settings)
    names = {tool.name for tool in registry.all_tools()}

    with patch.object(LspClient, "has_any_server", return_value=True):
        names.update(tool.name for tool in LspClient().available_tools())

    names.update(
        tool.name
        for tool in create_symbol_tools(
            client=MagicMock(), retriever=MagicMock(), editor=MagicMock()
        )
    )
    names.add(ParismTool().name)
    return names


def code_provider_names() -> list[str]:
    from nerdvana_cli.providers.base import ProviderName

    return [member.value for member in ProviderName]


def code_openai_compatible_count() -> int:
    """How many providers are served by the shared OpenAI-compatible client."""
    from nerdvana_cli.providers.factory import _PROVIDER_CLASSES
    from nerdvana_cli.providers.openai_provider import OpenAIProvider

    return sum(1 for cls in _PROVIDER_CLASSES.values() if cls is OpenAIProvider)


def code_agent_definitions() -> dict[str, Any]:
    from nerdvana_cli.agents.builtin import BUILTIN_AGENTS

    return {agent.agent_type: agent for agent in BUILTIN_AGENTS}


def code_paths() -> dict[str, str]:
    """Canonical user-data paths, written the way documentation writes them."""
    from nerdvana_cli.core import paths as core_paths

    home = str(Path.home())

    def tilde(value: Path) -> str:
        return str(value).replace(home, "~", 1)

    return {
        "config":   tilde(core_paths.user_config_path()),
        "nirnamd":  tilde(core_paths.user_nirnamd_path()),
        "mcp":      tilde(core_paths.user_mcp_json()),
        "hooks":    tilde(core_paths.user_hooks_dir()) + "/",
        "skills":   tilde(core_paths.user_skills_dir()) + "/",
        "sessions": tilde(core_paths.user_sessions_dir()) + "/",
        "agents":   tilde(core_paths.user_agents_dir()) + "/",
    }


def code_command_tree() -> dict[str, set[str]]:
    """Map every typer command group to its child command names.

    The root group is keyed by the empty string. A leaf command maps to an
    empty set, which is what lets the reverse check tell `nerdvana mcp add`
    from `nerdvana mcp nonsense`.
    """
    import typer

    from nerdvana_cli.main import app

    tree: dict[str, set[str]] = {}

    def command_name(info: Any) -> str:
        if info.name:
            return str(info.name)
        return str(info.callback.__name__).replace("_", "-")

    def walk(instance: typer.Typer, prefix: str) -> None:
        children: set[str] = set()
        for info in instance.registered_commands:
            children.add(command_name(info))
        for group in instance.registered_groups:
            sub = group.typer_instance
            name = str(group.name or (sub.info.name if sub else "") or "")
            children.add(name)
            if sub is not None:
                walk(sub, f"{prefix} {name}".strip())
        tree[prefix] = children

    walk(app, "")
    return tree


def command_paths(tree: dict[str, set[str]]) -> set[str]:
    """Flatten the command tree into space-joined invocation paths."""
    out: set[str] = set()
    for prefix, children in tree.items():
        for child in children:
            out.add(f"{prefix} {child}".strip())
    return out


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def table_rows(lines: list[str]) -> Iterator[tuple[int, list[str]]]:
    """Yield (line number, cells) for every pipe-table row in `lines`."""
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") and cell for cell in cells):
            continue
        yield number, cells


def section_lines(text: str, headings: tuple[str, ...]) -> list[str]:
    """Return the lines under the first heading matching any of `headings`."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("#") and any(head in line for head in headings):
            start = index + 1
            break
    if start is None:
        return []
    depth = len(lines[start - 1]) - len(lines[start - 1].lstrip("#"))
    out: list[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            here = len(line) - len(line.lstrip("#"))
            if here <= depth:
                break
        out.append(line)
    return out


def backticked(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_env_vars(docs: dict[str, str], report: Report) -> None:
    known = code_env_vars()
    pattern = re.compile(r"NERDVANA_[A-Z0-9_]+")
    for name, text in docs.items():
        for var in sorted(set(pattern.findall(text))):
            if var not in known:
                report.add(name, f"documents environment variable {var}, which no code reads")


TOOL_SECTIONS = ("Built-in Tools", "내장 도구")


def check_builtin_tools(docs: dict[str, str], report: Report) -> None:
    known = code_tool_names()

    for name in ("README.md", "README.ko.md"):
        text = docs[name]
        lines = section_lines(text, TOOL_SECTIONS)
        if not lines:
            report.add(name, "has no built-in tool section")
            continue

        documented: set[str] = set()
        for _, cells in table_rows(lines):
            first = cells[0].strip().strip("`")
            if first.lower() in {"tool", "도구"}:
                continue
            documented.add(first)

        for missing in sorted(known - documented):
            report.add(name, f"built-in tool table omits {missing}")
        for extra in sorted(documented - known):
            report.add(name, f"built-in tool table lists {extra}, which the registry never registers")

    count_patterns = (r"(\d+)개 내장 도구", r"(\d+) built-in tools")
    for name, text in docs.items():
        for pattern in count_patterns:
            for found in re.findall(pattern, text):
                if int(found) != len(known):
                    report.add(name, f"claims {found} built-in tools; the registry defines {len(known)}")


def check_providers(docs: dict[str, str], report: Report) -> None:
    providers = code_provider_names()
    total = len(providers)

    count_patterns = (
        r"providers-(\d+)-",
        r"(\d+) AI platforms",
        r"(\d+) AI providers",
        r"(\d+)개 AI 플랫폼",
        r"(\d+)개 AI 제공자",
    )
    for name, text in docs.items():
        for pattern in count_patterns:
            for found in re.findall(pattern, text):
                if int(found) != total:
                    report.add(name, f"claims {found} providers; ProviderName defines {total}")

        for found in re.findall(r"OpenAIProvider covers (\d+)", text):
            expected = code_openai_compatible_count()
            if int(found) != expected:
                report.add(name, f"claims OpenAIProvider covers {found}; the factory maps {expected}")

    example = docs["nerdvana.yml.example"]
    for line in example.splitlines():
        if not line.startswith("  # Provider:"):
            continue
        listed = {token.strip() for token in line.split(":", 1)[1].split(",")}
        for missing in sorted(set(providers) - listed):
            report.add("nerdvana.yml.example", f"provider comment omits {missing}")
        break
    else:
        report.add("nerdvana.yml.example", "has no `# Provider:` comment listing the provider names")


def check_paths(docs: dict[str, str], report: Report) -> None:
    expected = code_paths()

    for name, text in docs.items():
        for number, line in enumerate(text.splitlines(), start=1):
            if LEGACY_CONFIG_DIR in line and not LEGACY_CONTEXT.search(line):
                report.add(name, f"line {number} points at {LEGACY_CONFIG_DIR}, not the path core.paths returns")

    joined = "\n".join(docs.values())
    for key, path in expected.items():
        if path not in joined:
            report.add("docs", f"no document mentions the canonical {key} path {path}")


def check_subcommands(docs: dict[str, str], report: Report) -> None:
    tree = code_command_tree()
    paths = command_paths(tree)
    root = tree[""]

    for name in ("README.md", "README.ko.md"):
        text = docs[name]
        for path in sorted(paths):
            if f"`nerdvana {path}" not in text:
                report.add(name, f"does not document the `nerdvana {path}` subcommand")

        for first, second in re.findall(r"`nerdvana ([a-z][a-z0-9-]*)(?: ([a-z][a-z0-9-]*))?", text):
            if first not in root:
                report.add(name, f"documents `nerdvana {first}`, which is not a registered command")
                continue
            children = tree.get(first, set())
            if second and children and second not in children:
                report.add(name, f"documents `nerdvana {first} {second}`, which is not a registered subcommand")


def check_agent_definitions(docs: dict[str, str], report: Report) -> None:
    agents = code_agent_definitions()
    tools = code_tool_names()

    for name, text in docs.items():
        lines = text.splitlines()

        # Table form: the agent type sits in the first cell.
        header: list[str] = []
        for _, cells in table_rows(lines):
            lowered = [cell.lower() for cell in cells]
            is_header = (
                len(cells) > 1
                and any("agent" in cell for cell in lowered)
                and not any(cell.strip().strip("`") in agents for cell in cells)
            )
            if is_header:
                header = lowered
                continue
            agent_type = cells[0].strip().strip("`")
            agent = agents.get(agent_type)
            if agent is None:
                continue
            row = " ".join(cells[1:])
            if "*" not in row:
                listed = {token for token in backticked(row) if token in tools}
                allowed = set(agent.allowed_tools) & tools
                if listed != allowed:
                    report.add(
                        name,
                        f"agent {agent_type} is documented with tools {sorted(listed)}; "
                        f"builtin.py grants {sorted(allowed)}",
                    )
            for index, head in enumerate(header):
                if "max" in head and "turn" in head and index < len(cells):
                    cell = cells[index].strip()
                    if cell.isdigit() and int(cell) != agent.max_turns:
                        report.add(
                            name,
                            f"agent {agent_type} is documented with {cell} max turns; "
                            f"builtin.py sets {agent.max_turns}",
                        )

        # Bullet form: `### \`Explore\`` followed by `- **Allowed tools:** ...`.
        current: str | None = None
        for line in lines:
            heading = re.match(r"^#+\s+`([^`]+)`", line)
            if heading:
                current = heading.group(1)
                continue
            if current is None or current not in agents:
                continue
            agent = agents[current]
            allowed_match = re.match(r"^-\s+\*\*Allowed tools:\*\*(.*)", line)
            if allowed_match and "*" not in allowed_match.group(1).replace("**", ""):
                listed = {token for token in backticked(allowed_match.group(1)) if token in tools}
                allowed = set(agent.allowed_tools) & tools
                if listed != allowed:
                    report.add(
                        name,
                        f"agent {current} is documented with tools {sorted(listed)}; "
                        f"builtin.py grants {sorted(allowed)}",
                    )
            turns_match = re.match(r"^-\s+\*\*Max turns:\*\*\s*(\d+)", line)
            if turns_match and int(turns_match.group(1)) != agent.max_turns:
                report.add(
                    name,
                    f"agent {current} is documented with {turns_match.group(1)} max turns; "
                    f"builtin.py sets {agent.max_turns}",
                )


CHECKS = (
    check_env_vars,
    check_builtin_tools,
    check_providers,
    check_paths,
    check_subcommands,
    check_agent_definitions,
)


def load_docs() -> dict[str, str]:
    docs: dict[str, str] = {}
    for name in DOC_FILES:
        path = REPO_ROOT / name
        if not path.is_file():
            raise FileNotFoundError(f"documented file is missing: {name}")
        docs[name] = path.read_text(encoding="utf-8")
    return docs


def run_checks() -> Report:
    docs = load_docs()
    report = Report()
    for check in CHECKS:
        check(docs, report)
    return report


def main() -> int:
    report = run_checks()
    for doc, message in report.problems:
        print(f"{doc}: {message}")
    if report.problems:
        print(f"\n{len(report)} documentation claim(s) do not match the code.")
        return 1
    print("Documentation matches the code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
