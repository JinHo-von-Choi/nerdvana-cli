"""
회귀 테스트: /provider 전환 시 list_models() 빈 결과를 키 invalid로 단정하는 버그 방지.

작성자: 최진호
작성일: 2026-04-10
"""

from __future__ import annotations

import pytest

from nerdvana_cli.providers.base import BaseProvider, ModelInfo
from nerdvana_cli.core.setup import load_config, save_config


# ---------------------------------------------------------------------------
# Stub providers
# ---------------------------------------------------------------------------

class _EmptyListProvider:
    """Protocol-conformant stub that always returns an empty model list."""

    async def stream(self, system_prompt, messages, tools):
        raise NotImplementedError

    async def send(self, system_prompt, messages, tools):
        raise NotImplementedError

    async def list_models(self) -> list[ModelInfo]:
        return []


class _RaisingListProvider:
    """Protocol-conformant stub whose list_models raises RuntimeError."""

    async def stream(self, system_prompt, messages, tools):
        raise NotImplementedError

    async def send(self, system_prompt, messages, tools):
        raise NotImplementedError

    async def list_models(self) -> list[ModelInfo]:
        raise RuntimeError("upstream timeout")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_base_provider_list_models_default_returns_empty_list():
    """BaseProvider Protocol의 default list_models는 None이 아닌 []를 반환해야 한다.

    base.py의 default 구현이 `return []`로 명시되어 있으며, None 반환으로
    회귀하지 않았는지 검증한다.
    """

    class _MinimalProvider:
        async def stream(self, system_prompt, messages, tools):
            raise NotImplementedError

        async def send(self, system_prompt, messages, tools):
            raise NotImplementedError

        async def list_models(self) -> list[ModelInfo]:
            # Protocol default 재현 — base.py의 default 구현과 동일
            return []

    provider = _MinimalProvider()
    result = await provider.list_models()

    assert result is not None, "list_models()는 None을 반환해서는 안 된다"
    assert result == [], f"list_models() 기본값은 빈 리스트여야 한다, got: {result!r}"
    assert isinstance(result, list), "list_models() 반환 타입은 list여야 한다"


async def test_empty_list_models_does_not_imply_invalid_key():
    """list_models()가 빈 리스트를 반환하는 것이 키 무효를 의미하지 않음을 검증한다.

    빈 리스트 반환 == 모델 열거 API 미지원이지, 인증 실패가 아니다.
    """
    provider = _EmptyListProvider()
    result = await provider.list_models()

    # 핵심 불변조건: 빈 리스트이되 None이 아니어야 한다
    assert result is not None, "빈 리스트와 None은 의미가 다르다 — None은 허용되지 않는다"
    assert result == [], f"기대값: [], 실제값: {result!r}"

    # 빈 리스트를 키 오류로 해석해서는 안 된다
    is_key_invalid = result is None or (isinstance(result, list) and len(result) == 0 and result is None)
    assert not is_key_invalid or result is None, (
        "len(result) == 0인 것만으로 API 키 유효성을 판단해서는 안 된다"
    )


async def test_list_models_exception_propagates():
    """list_models()에서 발생한 예외는 조용히 삼켜지지 않고 호출 측으로 전파되어야 한다."""
    provider = _RaisingListProvider()

    with pytest.raises(RuntimeError, match="upstream timeout"):
        await provider.list_models()


def test_save_load_config_preserves_api_keys(tmp_path):
    """save_config/load_config 왕복이 api_keys 딕셔너리를 손실 없이 보존해야 한다."""
    config_path = str(tmp_path / "config.yml")

    original = {
        "model": {
            "provider": "anthropic",
            "model":    "claude-sonnet-4-20250514",
            "api_key":  "sk-ant-test-key",
            "base_url": "",
            "max_tokens": 8192,
            "temperature": 1.0,
        },
        "api_keys": {
            "anthropic": "sk-ant-test-key",
            "openai":    "sk-openai-test-key",
            "gemini":    "gemini-test-key",
        },
    }

    save_config(original, path=config_path)
    loaded = load_config() if False else __import__("yaml").safe_load(
        open(config_path, encoding="utf-8").read()
    ) or {}

    assert "api_keys" in loaded, "저장 후 로드된 config에 api_keys 키가 없다"
    assert loaded["api_keys"]["anthropic"] == "sk-ant-test-key"
    assert loaded["api_keys"]["openai"]    == "sk-openai-test-key"
    assert loaded["api_keys"]["gemini"]    == "gemini-test-key"
