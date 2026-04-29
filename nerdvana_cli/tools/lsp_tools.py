"""LSP server lifecycle management tool: RestartLanguageServer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.lsp_client import LspClient


class RestartLanguageServerArgs:
    def __init__(self, language: str | None = None) -> None:
        self.language = language


class RestartLanguageServerTool(BaseTool[RestartLanguageServerArgs]):
    """Restart one or all language server processes."""

    name             = "restart_language_server"
    description_text = (
        "Restart the language server process for a specific language (e.g. 'python', "
        "'typescript') or all servers when language is omitted. Use after installing "
        "packages or when the server becomes unresponsive."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "language": {
                "type":        "string",
                "description": "Language identifier: 'python', 'typescript', 'go', 'rust'. "
                               "Omit to restart all servers.",
            },
        },
        "required": [],
    }
    is_concurrency_safe                       = False
    args_class                                = RestartLanguageServerArgs
    category:              ClassVar[ToolCategory]    = ToolCategory.META
    side_effects:          ClassVar[ToolSideEffect]  = ToolSideEffect.EXTERNAL
    tags:                  ClassVar[frozenset[str]]  = frozenset({"lsp", "symbol"})
    requires_confirmation: ClassVar[bool]            = False

    _LANG_TO_EXT: ClassVar[dict[str, str]] = {
        "python":     ".py",
        "typescript": ".ts",
        "javascript": ".js",
        "go":         ".go",
        "rust":       ".rs",
    }

    def __init__(self, client: LspClient) -> None:
        super().__init__()
        self._client = client

    async def call(
        self,
        args:         RestartLanguageServerArgs,
        context:      ToolContext,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        if args.language is not None:
            ext = self._LANG_TO_EXT.get(args.language.lower())
            if ext is None:
                return ToolResult(
                    tool_use_id="",
                    content=f"Unknown language {args.language!r}. "
                            f"Supported: {', '.join(self._LANG_TO_EXT)}",
                    is_error=True,
                )
            try:
                await self._client.shutdown_server(ext)
            except Exception as e:
                return ToolResult(
                    tool_use_id="",
                    content=f"Error restarting {args.language} server: {e}",
                    is_error=True,
                )
            self._client._disabled.discard(ext)   # noqa: SLF001
            return ToolResult(
                tool_use_id="",
                content=f"Language server for {args.language!r} restarted successfully.",
            )
        else:
            restarted: list[str] = []
            for lang, ext in self._LANG_TO_EXT.items():
                try:
                    await self._client.shutdown_server(ext)
                    self._client._disabled.discard(ext)   # noqa: SLF001
                    restarted.append(lang)
                except Exception:
                    pass
            return ToolResult(
                tool_use_id="",
                content=f"Restarted language servers: {', '.join(restarted) or 'none'}",
            )
