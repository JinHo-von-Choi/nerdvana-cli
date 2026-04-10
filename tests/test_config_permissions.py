

def test_provider_config_repr_masks_key():
    from nerdvana_cli.providers.base import ProviderConfig
    cfg = ProviderConfig(
        provider="anthropic",
        model="test",
        api_key="sk-ant-api03-very-secret-key-12345",
    )
    repr_str = repr(cfg)
    assert "very-secret-key" not in repr_str
    assert "sk-a" in repr_str or "****" in repr_str


def test_provider_config_repr_short_key():
    from nerdvana_cli.providers.base import ProviderConfig
    cfg = ProviderConfig(provider="anthropic", model="test", api_key="short")
    repr_str = repr(cfg)
    assert "short" not in repr_str
    assert "****" in repr_str


def test_provider_config_repr_no_key():
    from nerdvana_cli.providers.base import ProviderConfig
    cfg = ProviderConfig(provider="anthropic", model="test", api_key="")
    repr_str = repr(cfg)
    assert "api_key=''" in repr_str
