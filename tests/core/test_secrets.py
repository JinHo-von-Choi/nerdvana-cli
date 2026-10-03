"""Masking secret values in the output of commands and external tools.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.execution.tool_executor import ToolExecutor
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.safety.secrets import MARKER, SecretMasker
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.types import ToolResult

# Shapes only: none of these is a real credential.
OPENAI_LIKE    = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
ANTHROPIC_LIKE = "sk-ant-" + "api03-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2"
GITHUB_LIKE    = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
AWS_LIKE       = "AKIA" + "ABCDEFGHIJKLMNOP"
JWT_LIKE       = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEF123_-xyz456"
PEM_LIKE       = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabcdef\n-----END RSA PRIVATE KEY-----"


def _mask(text: str, **values: str) -> tuple[str, int]:
    result = SecretMasker(values).mask(text)
    return result.text, result.count


@pytest.mark.parametrize("secret", [OPENAI_LIKE, ANTHROPIC_LIKE, GITHUB_LIKE, AWS_LIKE, JWT_LIKE, PEM_LIKE])
def test_credential_shapes_are_replaced(secret: str) -> None:
    text, count = _mask(f"before {secret} after")
    assert secret not in text and MARKER in text and count == 1
    assert text.startswith("before ") and text.endswith(" after")


def test_a_bearer_header_keeps_its_scheme_and_loses_the_token() -> None:
    text, count = _mask("Authorization: Bearer abcdefghijklmnopqrstuvwx123456")
    assert text == f"Authorization: Bearer {MARKER}" and count == 1


def test_assignments_of_credential_named_values_lose_only_the_value() -> None:
    text, count = _mask("db_password = hunter2hunter2\nAPI_KEY: abcd1234efgh5678\n{\"token\": \"zzzzzzzzzzzz\"}")
    assert "hunter2hunter2" not in text and "abcd1234efgh5678" not in text and "zzzzzzzzzzzz" not in text
    assert "db_password = " in text and "API_KEY: " in text and count == 3


def test_exact_environment_values_are_replaced_wherever_they_occur() -> None:
    text, count = _mask("the value is s3cr3t-value-123 and again s3cr3t-value-123", DEPLOY_PASSWORD="s3cr3t-value-123")
    assert text == f"the value is {MARKER}:DEPLOY_PASSWORD and again {MARKER}:DEPLOY_PASSWORD" and count == 2


def test_the_longest_value_is_replaced_first() -> None:
    text, _ = _mask("abcdefgh12345678", SHORT="abcdefgh", LONG="abcdefgh12345678")
    assert text == f"{MARKER}:LONG"


@pytest.mark.parametrize("text", [
    "max_tokens: 4096 and tokens = many",
    "commit 3f2a9c1d4e5b6a7980123456789abcdef0123456 fixed it",
    "version 1.2.3-rc1 built from sk-learn docs",
    "the password field is required",
    "https://example.com/token/refresh",
    "key = short",
    "set TOKEN=abc",
])
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert _mask(text) == (text, 0)


def test_short_environment_values_are_not_masked() -> None:
    assert _mask("the answer is 1234 here", PIN="1234") == ("the answer is 1234 here", 0)


def test_extra_patterns_from_the_configuration_are_applied() -> None:
    masker = SecretMasker(extra_patterns=[r"ACME-\d{6}"])
    assert masker.mask("code ACME-123456 ok").text == f"code {MARKER} ok"


def test_the_environment_masker_takes_credential_named_variables_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SERVICE_TOKEN", "tok-1234567890-abc")
    monkeypatch.setenv("EDITOR_NAME", "vim-editor-long-name")
    masker = SecretMasker.from_environment()
    assert masker.mask("a tok-1234567890-abc b vim-editor-long-name c").text == f"a {MARKER}:MY_SERVICE_TOKEN b vim-editor-long-name c"


# ---------------------------------------------------------------------------
# In the executor
# ---------------------------------------------------------------------------


class _Echoing(BaseTool[Any]):
    """Returns whatever it was told to, as a command or a file tool would."""

    name             = "Bash"
    description_text = "runs"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"say": {"type": "string"}}}

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content=str(args["say"] if isinstance(args, dict) else args.say))

    def parse_args(self, tool_input: dict[str, Any]) -> Any:
        return type("A", (), {"say": tool_input["say"]})()


class _File(_Echoing):
    name = "FileRead"


class _Mcp(_Echoing):
    name = "mcp__srv__print"
    tags: ClassVar[frozenset[str]] = frozenset({"mcp"})


def _executor(settings: NerdvanaSettings | None = None) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in (_Echoing(), _File(), _Mcp()):
        registry.register(tool)
    return ToolExecutor(registry=registry, hooks=HookEngine(), settings=settings or NerdvanaSettings())


async def _run(executor: ToolExecutor, name: str, say: str, tmp_path: Path) -> ToolResult:
    (result,) = await executor.run_batch([{"id": "1", "name": name, "input": {"say": say}}], ToolContext(cwd=str(tmp_path)))
    return result


async def test_command_output_is_masked_and_says_so(tmp_path: Path) -> None:
    executor = _executor()
    result   = await _run(executor, "Bash", f"KEY={OPENAI_LIKE}", tmp_path)
    assert OPENAI_LIKE not in result.content
    assert "1 secret-like value(s) in this output were replaced" in result.content
    assert executor.signals["secret_masked"] == 1


async def test_external_tool_output_is_masked_too(tmp_path: Path) -> None:
    result = await _run(_executor(), "mcp__srv__print", f"token {GITHUB_LIKE}", tmp_path)
    assert GITHUB_LIKE not in result.content


async def test_file_tools_show_files_exactly_as_they_are(tmp_path: Path) -> None:
    result = await _run(_executor(), "FileRead", f"KEY={OPENAI_LIKE}", tmp_path)
    assert OPENAI_LIKE in result.content


async def test_the_api_key_in_use_is_masked_whatever_it_looks_like(tmp_path: Path) -> None:
    settings = NerdvanaSettings()
    settings.model.api_key = "plain-looking-secret-9999"
    result = await _run(_executor(settings), "Bash", "key is plain-looking-secret-9999", tmp_path)
    assert "plain-looking-secret-9999" not in result.content


async def test_masking_can_be_turned_off(tmp_path: Path) -> None:
    settings = NerdvanaSettings()
    settings.session.mask_secrets = False
    result = await _run(_executor(settings), "Bash", f"KEY={OPENAI_LIKE}", tmp_path)
    assert OPENAI_LIKE in result.content and "replaced with" not in result.content


async def test_output_without_secrets_is_untouched(tmp_path: Path) -> None:
    result = await _run(_executor(), "Bash", "hello world", tmp_path)
    assert result.content == "hello world"


def test_mask_text_serves_the_verification_output() -> None:
    executor = _executor()
    assert OPENAI_LIKE not in executor.mask_text(f"FAILED with {OPENAI_LIKE}")
    off = NerdvanaSettings()
    off.session.mask_secrets = False
    assert _executor(off).mask_text(OPENAI_LIKE) == OPENAI_LIKE
