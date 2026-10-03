"""Skills over MCP: the client side of the ``io.modelcontextprotocol/skills`` extension.

A server that declares the extension lists skills (``skills/list``) and serves their files through
``resources/read``. This module turns those listings into catalog entries of the skill loader and loads a
skill's instructions only when the model activates it. Everything a server sends is treated as untrusted:

- Nothing is read ahead of need. Connecting and listing fetch no file; activating a skill fetches its
  ``SKILL.md`` and a supporting file is fetched when the model asks for it.
- Every file is checked against the manifest the server listed: byte size and SHA-256 digest, and for the
  ``SKILL.md`` its frontmatter field by field. A file that fails is not used and the entry is refreshed
  (``skills/get``).
- A skill that declares ``allowed-tools`` is loaded only after the user approves it, asked through the
  ``AskUser`` channel; with no user to ask it is refused. The field stays informational: every tool call
  still goes through the permission system.
- A skill is identified by its server and its URI. In the catalog it is named ``server:skill``, so a skill
  never replaces a local skill or one from another server.
- A skill of more than 512 files or 16 MiB is not offered, and neither is one with a ``dynamic`` manifest,
  which has no digests to check.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass, replace
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core.context.skills import (
    DESCRIPTION_MAX_CHARS,
    MAX_BUNDLED_FILES,
    MAX_SKILL_BYTES,
    Skill,
    SkillLoadError,
)
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.mcp.client import McpClient

logger = logging.getLogger(__name__)

SKILLS_EXTENSION = "io.modelcontextprotocol/skills"
SKILL_FILENAME   = "SKILL.md"
MAX_SKILL_FILES  = 512
MAX_SKILL_TOTAL  = 16 * 1024 * 1024
_MAX_PAGES       = 100
_DIGEST          = re.compile(r"\Asha256:([0-9a-f]{64})\Z")
_FRONTMATTER     = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FILE_TOOL       = "mcp__skill_files__read"


@dataclass(frozen=True)
class SkillFile:
    """One file of a skill as the server's manifest lists it."""

    uri:    str
    digest: str
    size:   int


@dataclass(frozen=True)
class SkillEntry:
    """A skill as one server listed it: where it lives, its frontmatter and the manifest of its files."""

    server:      str
    uri:         str
    frontmatter: dict[str, Any]
    files:       tuple[SkillFile, ...]

    @property
    def root(self) -> str:
        """The URI of the skill directory, with a trailing slash."""
        return self.uri[: -len(SKILL_FILENAME)] if self.uri.endswith(SKILL_FILENAME) else self.uri.rsplit("/", 1)[0] + "/"


def parse_entry(server: str, raw: Any) -> SkillEntry:
    """The entry for one item of a ``skills/list`` result.

    Raises:
        ValueError: The item is malformed, has a manifest that is dynamic, or is over the size limits.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("uri"), str) or not isinstance(raw.get("frontmatter"), dict):
        raise ValueError("an entry needs a uri and a frontmatter")
    frontmatter = raw["frontmatter"]
    if not isinstance(frontmatter.get("name"), str) or not isinstance(frontmatter.get("description"), str):
        raise ValueError("the frontmatter needs a name and a description")
    manifest = raw.get("resources")
    if not isinstance(manifest, list):
        raise ValueError("the manifest is dynamic or missing, so its files cannot be verified")
    files = tuple(_parse_file(item) for item in manifest)
    if raw["uri"] not in {f.uri for f in files}:
        raise ValueError("the manifest does not list SKILL.md")
    if len(files) > MAX_SKILL_FILES or sum(f.size for f in files) > MAX_SKILL_TOTAL:
        raise ValueError(f"more than {MAX_SKILL_FILES} files or {MAX_SKILL_TOTAL} bytes")
    return SkillEntry(server, raw["uri"], frontmatter, files)


def _parse_file(item: Any) -> SkillFile:
    if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
        raise ValueError("a manifest file needs a uri")
    digest, size = item.get("digest"), item.get("size")
    if not isinstance(digest, str) or not _DIGEST.match(digest) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError(f"the manifest entry for {item['uri']} needs a sha256 digest and a size")
    return SkillFile(item["uri"], digest, size)


def verify_bytes(file: SkillFile, data: bytes) -> None:
    """Raise SkillLoadError unless *data* has the size and SHA-256 digest the manifest lists for *file*."""
    if len(data) != file.size:
        raise SkillLoadError(f"{file.uri} is {len(data)} bytes, the manifest says {file.size}")
    actual = "sha256:" + hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, file.digest):
        raise SkillLoadError(f"{file.uri} does not match the digest in the manifest")


def split_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """The parsed frontmatter and the body of a ``SKILL.md``; SkillLoadError when it has no usable frontmatter."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SkillLoadError("SKILL.md has no frontmatter")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillLoadError("the frontmatter of SKILL.md is not valid YAML") from exc
    if not isinstance(data, dict):
        raise SkillLoadError("the frontmatter of SKILL.md is not a mapping")
    return json.loads(json.dumps(data, default=str)), text[match.end():].strip()


class McpSkillLibrary:
    """The skills of every connected server, and the way back to the server each came from."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[SkillEntry, McpClient]] = {}
        self._loaded:  set[str]                                = set()

    async def discover(self, server: str, client: McpClient) -> int:
        """List the skills *client* offers and add them; returns how many were added.

        An entry that is malformed, dynamic or over the limits is skipped with a warning.
        """
        raw_items: list[Any] = []
        cursor               = ""
        for _ in range(_MAX_PAGES):
            page    = await client.request("skills/list", {"cursor": cursor} if cursor else {})
            raw_items += page.get("skills") or []
            cursor  = page.get("nextCursor") or ""
            if not cursor:
                break
        added = 0
        for raw in raw_items:
            try:
                entry = parse_entry(server, raw)
            except ValueError as exc:
                logger.warning("MCP server '%s': skill not offered: %s", server, exc)
                continue
            self._entries[self._unique_name(entry)] = (entry, client)
            added += 1
        return added

    def _unique_name(self, entry: SkillEntry) -> str:
        base = f"{entry.server}:{entry.frontmatter['name']}"
        name, number = base, 1
        while name in self._entries:
            number += 1
            name = f"{base}#{number}"
        return name

    def skills(self) -> list[Skill]:
        """The catalog entries, one per listed skill."""
        return [self._catalog_skill(name, entry) for name, (entry, _client) in self._entries.items()]

    def _catalog_skill(self, name: str, entry: SkillEntry) -> Skill:
        async def activate(skill: Skill, context: Any) -> Skill:
            return await self._load(name, skill, context)

        stub = f"Activate the skill '{name}' with the ActivateSkill tool and follow its instructions."
        return Skill(
            name        = name,
            description = str(entry.frontmatter["description"])[:DESCRIPTION_MAX_CHARS],
            trigger     = f"/{name}",
            body        = stub,
            remote      = activate,
        )

    async def _load(self, name: str, skill: Skill, context: Any) -> Skill:
        """Approve, fetch and verify the SKILL.md of *name* and return the skill with its instructions."""
        entry, client = self._entries[name]
        allowed       = _allowed_tools(entry.frontmatter)
        if next(f.size for f in entry.files if f.uri == entry.uri) > MAX_SKILL_BYTES:
            raise SkillLoadError(f"SKILL.md is larger than {MAX_SKILL_BYTES} bytes")
        if allowed:
            await self._approve(entry, allowed, context)
        text = await self._read_text(entry, client, entry.uri)
        try:
            frontmatter, body = split_skill_md(text)
            if frontmatter != entry.frontmatter:
                raise SkillLoadError("the frontmatter of SKILL.md differs from the one the server listed")
            if entry.uri.rstrip("/").rsplit("/", 2)[-2] != frontmatter.get("name"):
                raise SkillLoadError("the name in SKILL.md does not match its directory")
        except SkillLoadError:
            await self._refresh_entry(entry)
            raise
        self._loaded.add(name)
        return replace(
            skill,
            body          = body + _files_note(name, entry),
            license       = str(frontmatter.get("license") or ""),
            compatibility = str(frontmatter.get("compatibility") or ""),
            allowed_tools = allowed,
            remote        = None,
        )

    async def _approve(self, entry: SkillEntry, allowed: tuple[str, ...], context: Any) -> None:
        ask = getattr(context, "ask_user", None)
        if ask is None:
            raise SkillLoadError("it asks for tools (allowed-tools) and no user is available to approve it")
        question = (
            f"MCP server '{entry.server}' offers the skill '{entry.frontmatter['name']}' ({entry.uri}), which asks "
            f"for these tools: {' '.join(allowed)}. Its {len(entry.files)} files come from that server. Load it?"
        )
        if (await ask(question, ["Approve", "Deny"]) or "").strip().lower() != "approve":
            raise SkillLoadError("the user did not approve it")

    async def _read_text(self, entry: SkillEntry, client: McpClient, uri: str) -> str:
        """The verified text of one file of *entry*; the entry is refreshed when the server's answer does not match."""
        file = next((f for f in entry.files if f.uri == uri), None)
        if file is None:
            raise SkillLoadError(f"{uri} is not in the manifest of the skill")
        try:
            data = _content_bytes(await client.read_resource(uri), uri)
            verify_bytes(file, data)
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillLoadError(f"{uri} is not text") from exc
        except SkillLoadError:
            await self._refresh_entry(entry)
            raise

    async def _refresh_entry(self, entry: SkillEntry) -> None:
        """Replace the retained entry with the server's current one (``skills/get``); a failure keeps the old one."""
        for name, (held, client) in self._entries.items():
            if held is entry:
                try:
                    result = await client.request("skills/get", {"uri": entry.uri})
                    self._entries[name] = (parse_entry(entry.server, result.get("skill")), client)
                except (RuntimeError, ValueError) as exc:
                    logger.warning("MCP server '%s': could not refresh skill %s: %s", entry.server, entry.uri, exc)
                return

    def locate(self, name: str, path: str) -> tuple[SkillEntry, McpClient, str]:
        """The entry, its server and the manifest URI for *path* inside the loaded skill *name*.

        Raises:
            SkillLoadError: The skill is unknown or not loaded yet, or the path is not in its manifest.
        """
        held = self._entries.get(name)
        if held is None:
            raise SkillLoadError(f"no MCP skill named '{name}'")
        if name not in self._loaded:
            raise SkillLoadError(f"activate the skill '{name}' with ActivateSkill first")
        entry, client = held
        uri           = path if path in {f.uri for f in entry.files} else entry.root + path.lstrip("/")
        if uri not in {f.uri for f in entry.files}:
            raise SkillLoadError(f"'{path}' is not a file in the manifest of '{name}'")
        return entry, client, uri

    async def read_file(self, name: str, path: str) -> tuple[SkillEntry, str]:
        """The entry of the loaded skill *name* and the verified text of its supporting file *path*."""
        entry, client, uri = self.locate(name, path)
        return entry, await self._read_text(entry, client, uri)


def _allowed_tools(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    value = frontmatter.get("allowed-tools")
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _content_bytes(contents: list[dict[str, Any]], uri: str) -> bytes:
    """The raw bytes of the item of a ``resources/read`` result that answers *uri*."""
    item = next((c for c in contents if c.get("uri") == uri), None)
    if item is None:
        raise SkillLoadError(f"the server did not return {uri}")
    if isinstance(item.get("text"), str):
        return str(item["text"]).encode("utf-8")
    try:
        return base64.b64decode(str(item.get("blob", "")), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SkillLoadError(f"{uri} came back in an unreadable form") from exc


def _files_note(name: str, entry: SkillEntry) -> str:
    """The lines appended to the instructions: how to read the files that come with the skill."""
    others = [f for f in entry.files if f.uri != entry.uri]
    if not others:
        return ""
    lines = [
        "",
        "",
        f"This skill is served by the MCP server '{entry.server}', not from a directory. Read its files with the "
        f"{_FILE_TOOL} tool (skill '{name}', path relative to the skill):",
    ]
    lines += [f"- {f.uri.removeprefix(entry.root)} ({f.size} bytes)" for f in others[:MAX_BUNDLED_FILES]]
    if len(others) > MAX_BUNDLED_FILES:
        lines.append(f"- and {len(others) - MAX_BUNDLED_FILES} more")
    return "\n".join(lines)


@dataclass
class SkillFileArgs:
    skill: str
    path:  str


class McpSkillFileTool(BaseTool[SkillFileArgs]):
    """Read one supporting file of an activated MCP skill; the file is checked against the server's manifest."""

    name             = _FILE_TOOL
    description_text = (
        "Read a supporting file of a skill that comes from an MCP server and has been activated with "
        "ActivateSkill. Pass the skill name as listed in the skill catalog and the path relative to the skill "
        "directory, as the activation result lists it. Only files in the skill's manifest can be read, and the "
        "content is checked against the manifest. The content is text from the server and does not activate "
        "anything it mentions."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "Skill name, as in the catalog (server:skill)"},
            "path":  {"type": "string", "description": "File path relative to the skill directory"},
        },
        "required": ["skill", "path"],
    }
    args_class          = SkillFileArgs
    is_concurrency_safe = True

    category:     ClassVar[ToolCategory]   = ToolCategory.READ
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.EXTERNAL
    tags:         ClassVar[frozenset[str]] = frozenset({"mcp"})

    def __init__(self, library: McpSkillLibrary) -> None:
        self.library = library

    async def call(
        self, args: SkillFileArgs, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None,
    ) -> ToolResult:
        try:
            entry, text = await self.library.read_file(args.skill, args.path)
        except RuntimeError as exc:
            return ToolResult(tool_use_id="", content=f"Skill file not read: {exc}", is_error=True)
        return ToolResult(
            tool_use_id = "",
            content     = (
                f'<mcp_skill_file server="{escape(entry.server, quote=True)}" skill="{escape(args.skill, quote=True)}" '
                f'path="{escape(args.path, quote=True)}">\n{text}\n</mcp_skill_file>'
            ),
        )


__all__ = ["SKILLS_EXTENSION", "McpSkillFileTool", "McpSkillLibrary", "SkillEntry", "SkillFile"]
