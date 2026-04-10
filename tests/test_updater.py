from nerdvana_cli.core.updater import compare_versions, parse_version


def test_parse_version_basic():
    assert parse_version("v0.1.1") == (0, 1, 1)
    assert parse_version("0.1.1") == (0, 1, 1)
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_with_suffix():
    assert parse_version("v0.1.1-beta") == (0, 1, 1)
    assert parse_version("v1.0.0rc1") == (1, 0, 0)


def test_compare_newer():
    assert compare_versions("0.1.1", "0.1.2") == -1


def test_compare_same():
    assert compare_versions("0.1.1", "0.1.1") == 0


def test_compare_older():
    assert compare_versions("0.2.0", "0.1.1") == 1


def test_compare_major():
    assert compare_versions("0.9.9", "1.0.0") == -1


def test_parse_invalid():
    assert parse_version("invalid") == (0, 0, 0)
