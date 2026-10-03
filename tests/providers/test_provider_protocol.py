from nerdvana_cli.providers.base import BaseProvider


def test_base_provider_importable():
    assert BaseProvider is not None


def test_protocol_has_required_methods():
    required = ["stream", "send", "list_models"]
    for method in required:
        assert hasattr(BaseProvider, method)
