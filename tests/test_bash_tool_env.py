"""bash_tool 서브프로세스 환경 구성 테스트."""

from __future__ import annotations

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool, _build_env


def test_build_env_excludes_credential_named_vars(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("MY_TOKEN", "t")
    monkeypatch.setenv("GITHUB_TOKEN", "t2")
    monkeypatch.setenv("TOKEN", "t3")
    monkeypatch.setenv("DB_SECRET", "s")
    monkeypatch.setenv("USER_PASSWORD", "p")
    monkeypatch.setenv("AWS_CREDENTIALS", "c")
    env = _build_env(cwd="/tmp")
    for name in ("OPENAI_API_KEY", "MY_TOKEN", "GITHUB_TOKEN", "TOKEN", "DB_SECRET", "USER_PASSWORD", "AWS_CREDENTIALS"):
        assert name not in env


def test_build_env_keeps_ordinary_vars_and_sets_pwd(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PROJECT_MODE", "dev")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    env = _build_env(cwd="/workdir")
    assert env["PATH"] == "/usr/bin"
    assert env["PROJECT_MODE"] == "dev"
    assert env["TOKENIZERS_PARALLELISM"] == "false"
    assert env["PWD"] == "/workdir"


async def test_call_subprocess_does_not_see_credential_vars(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    tool = BashTool()
    ctx = ToolContext(cwd="/tmp")
    result = await tool.call(
        BashArgs(command="python3 -c \"import os; print('OPENAI_API_KEY' in os.environ)\"", timeout=30),
        ctx,
    )
    assert "False" in result.content
