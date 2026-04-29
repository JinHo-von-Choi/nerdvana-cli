"""skill sub-app — user skill management.

Commands:
  skill list     — list available skills
  skill show     — print a skill's content to stdout
  skill install  — copy a skill file/directory into ~/.nerdvana/skills/
  skill remove   — delete a skill from ~/.nerdvana/skills/

Storage backend: paths.user_skills_dir() (~/.nerdvana/skills/).
The same directory is read by core/skills.py SkillLoader.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console

from nerdvana_cli.core import paths as core_paths

console = Console()

skill_app = typer.Typer(
    name           = "skill",
    help           = "User skill management.",
    add_completion = False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skills_dir() -> Path:
    return core_paths.user_skills_dir()


def _list_skill_paths(skills_dir: Path) -> list[Path]:
    """Return all *.md files and sub-directories (named skills) in skills_dir."""
    if not skills_dir.is_dir():
        return []
    results: list[Path] = []
    # Single-file skills
    results.extend(sorted(skills_dir.glob("*.md")))
    # Directory-based skills
    results.extend(sorted(p for p in skills_dir.iterdir() if p.is_dir()))
    return results


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@skill_app.command("list")
def skill_list() -> None:
    """List installed skills from ~/.nerdvana/skills/."""
    skills_dir = _skills_dir()
    items      = _list_skill_paths(skills_dir)

    if not items:
        console.print("[dim]No skills installed. Use 'skill install <path>' to add one.[/dim]")
        return

    console.print(f"[bold]Skills ({len(items)})[/bold]")
    for item in items:
        kind = "dir" if item.is_dir() else "file"
        console.print(f"  [cyan]{item.stem}[/cyan]  [{kind}]  {item.name}")


@skill_app.command("show")
def skill_show(name: str = typer.Argument(..., help="Skill name (without .md extension).")) -> None:
    """Print a skill's content to stdout."""
    skills_dir = _skills_dir()

    # Try file first, then directory SKILL.md
    candidates = [
        skills_dir / f"{name}.md",
        skills_dir / name / "SKILL.md",
        skills_dir / name / f"{name}.md",
    ]

    for candidate in candidates:
        if candidate.is_file():
            console.print(candidate.read_text(encoding="utf-8"))
            return

    console.print(f"[red]Skill '{name}' not found.[/red]")
    raise typer.Exit(1)


@skill_app.command("install")
def skill_install(
    path:  Path = typer.Argument(..., exists=True, help="Path to skill file or directory."),  # noqa: B008
    force: bool = typer.Option(False, "--force", help="Overwrite if already installed."),
) -> None:
    """Install a skill from a file or directory into ~/.nerdvana/skills/."""
    skills_dir = _skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)

    dest = skills_dir / path.name

    if dest.exists() and not force:
        console.print(
            f"[yellow]Skill '{path.name}' already exists. Use --force to overwrite.[/yellow]"
        )
        raise typer.Exit(1)

    if dest.exists() and force:
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()

    if path.is_dir():
        shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)

    console.print(f"Installed skill '[cyan]{path.name}[/cyan]' → {dest}")


@skill_app.command("remove")
def skill_remove(name: str = typer.Argument(..., help="Skill name (without .md extension).")) -> None:
    """Remove a skill from ~/.nerdvana/skills/."""
    skills_dir = _skills_dir()

    candidates = [
        skills_dir / f"{name}.md",
        skills_dir / name,
    ]

    for candidate in candidates:
        if candidate.exists():
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
            console.print(f"Removed skill '[cyan]{name}[/cyan]'.")
            return

    console.print(f"[red]Skill '{name}' not found.[/red]")
    raise typer.Exit(1)
