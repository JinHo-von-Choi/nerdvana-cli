"""create_provider의 미등록 provider 폴백 경고 테스트."""

from __future__ import annotations

import logging

from nerdvana_cli.providers import factory
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.openai_provider import OpenAIProvider


def test_unregistered_provider_falls_back_with_warning(monkeypatch, caplog) -> None:
    trimmed = {k: v for k, v in factory._PROVIDER_CLASSES.items() if k is not ProviderName.GROQ}
    monkeypatch.setattr(factory, "_PROVIDER_CLASSES", trimmed)
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.providers.factory"):
        provider = factory.create_provider(provider=ProviderName.GROQ, api_key="k")
    assert isinstance(provider, OpenAIProvider)
    assert any("groq" in rec.message.lower() for rec in caplog.records)


def test_registered_provider_emits_no_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.providers.factory"):
        factory.create_provider(provider=ProviderName.OPENAI, api_key="k")
    assert not caplog.records
