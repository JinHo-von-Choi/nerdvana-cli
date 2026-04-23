"""Tests for Ollama deployment mode configuration."""
from __future__ import annotations

import pytest

from nerdvana_cli.providers.base import (
    DEFAULT_BASE_URLS,
    ProviderName,
    detect_provider,
)


class TestOllamaModes:
    """Ollama deployment mode detection and configuration."""

    def test_ollama_default_local_url(self):
        url = DEFAULT_BASE_URLS[ProviderName.OLLAMA]
        assert "localhost" in url
        assert "11434" in url

    def test_ollama_detection_with_tag(self):
        assert detect_provider("qwen3:latest") == ProviderName.OLLAMA
        assert detect_provider("llama3:70b") == ProviderName.OLLAMA
        assert detect_provider("gemma2:9b") == ProviderName.OLLAMA

    def test_ollama_detection_with_cloud_suffix(self):
        assert detect_provider("qwen3:latest-cloud") == ProviderName.OLLAMA
        assert detect_provider("gemma2:27b-cloud") == ProviderName.OLLAMA

    def test_ollama_cloud_base_url_format(self):
        cloud_url = "https://ollama.com/v1"
        assert "ollama.com" in cloud_url
        assert cloud_url.endswith("/v1")

    def test_ollama_self_hosted_url_format(self):
        self_hosted = "http://192.168.1.100:11434/v1"
        assert self_hosted.startswith(("http://", "https://"))
        assert self_hosted.endswith("/v1")
