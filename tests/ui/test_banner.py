"""The banner picks the widest logo that fits, so a narrow window never wraps the art."""

from __future__ import annotations

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.ui.banner import LOGOS, build_banner, logo_for


def _width(logo: tuple[str, ...]) -> int:
    return max(len(line) for line in logo)


def test_each_logo_is_chosen_where_it_fits() -> None:
    full, small, text = LOGOS
    assert logo_for(0) is full
    assert logo_for(_width(full)) is full
    assert logo_for(_width(full) - 1) is small
    assert logo_for(_width(small) - 1) is text
    assert logo_for(5) is text


def test_every_line_of_a_logo_has_the_same_width_so_centering_keeps_it_aligned() -> None:
    for logo in LOGOS:
        assert len({len(line) for line in logo}) == 1


def test_the_banner_does_not_wrap() -> None:
    banner = build_banner(NerdvanaSettings(), tool_count=3, parism=False, width=40)
    assert banner.no_wrap
    assert banner.plain.splitlines()[:4] == list(LOGOS[1])
