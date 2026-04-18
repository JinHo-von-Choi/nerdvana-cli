"""Tests for secrets scanner in WriteMemory — Phase E.

Covers the 5 regex patterns defined in v3 §5.4.

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import pytest

from nerdvana_cli.tools.memory_tools import _scan_secrets


# ---------------------------------------------------------------------------
# Pattern 1: OpenAI/Anthropic key
# ---------------------------------------------------------------------------

def test_openai_key_detected() -> None:
    key     = "sk-" + "A" * 40
    hits    = _scan_secrets(f"API_KEY={key}")
    assert any("OpenAI" in h or "Anthropic" in h for h in hits)


def test_short_sk_not_detected() -> None:
    short = "sk-abc"  # too short (< 32 chars after prefix)
    hits  = _scan_secrets(f"key={short}")
    assert not any("OpenAI" in h or "Anthropic" in h for h in hits)


# ---------------------------------------------------------------------------
# Pattern 2: AWS Access Key ID
# ---------------------------------------------------------------------------

def test_aws_key_detected() -> None:
    aws  = "AKIA" + "A" * 16
    hits = _scan_secrets(aws)
    assert any("AWS" in h for h in hits)


def test_aws_key_wrong_prefix_not_detected() -> None:
    bad  = "BKIA" + "A" * 16
    hits = _scan_secrets(bad)
    assert not any("AWS" in h for h in hits)


# ---------------------------------------------------------------------------
# Pattern 3: GitHub PAT
# ---------------------------------------------------------------------------

def test_github_pat_detected() -> None:
    pat  = "ghp_" + "a" * 36
    hits = _scan_secrets(pat)
    assert any("GitHub" in h for h in hits)


def test_github_pat_short_not_detected() -> None:
    short = "ghp_abc"
    hits  = _scan_secrets(short)
    assert not any("GitHub" in h for h in hits)


# ---------------------------------------------------------------------------
# Pattern 4: API key env var
# ---------------------------------------------------------------------------

def test_api_key_env_var_detected() -> None:
    hits = _scan_secrets("OPENAI_API_KEY=sk-SOMEVALUE123")
    assert any("API key" in h for h in hits)


def test_service_api_key_env_var_detected() -> None:
    hits = _scan_secrets("STRIPE_API_KEY = my_secret_key")
    assert any("API key" in h for h in hits)


# ---------------------------------------------------------------------------
# Pattern 5: Authorization Bearer
# ---------------------------------------------------------------------------

def test_authorization_bearer_detected() -> None:
    hits = _scan_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature")
    assert any("Authorization" in h for h in hits)


# ---------------------------------------------------------------------------
# Clean content — no secrets
# ---------------------------------------------------------------------------

def test_clean_content_no_secrets() -> None:
    content = "Build: pytest tests/ -q\nArchitecture: layered\n"
    hits    = _scan_secrets(content)
    assert hits == []


# ---------------------------------------------------------------------------
# Multiple secrets in one string
# ---------------------------------------------------------------------------

def test_multiple_secrets_detected() -> None:
    key1 = "sk-" + "X" * 40
    key2 = "AKIA" + "B" * 16
    hits = _scan_secrets(f"first={key1} second={key2}")
    assert len(hits) >= 2
