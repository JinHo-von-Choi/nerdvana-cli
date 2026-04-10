from nerdvana_cli.ui.clipboard import copy_to_clipboard


def test_copy_returns_bool():
    result = copy_to_clipboard("test")
    assert isinstance(result, bool)
