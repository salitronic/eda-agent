# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The workbench has a dark theme: following the system, or chosen.

Every page colour is a palette variable, so the dark palette covers the
whole page; the sheet around the board follows it; the theme is set
before the first paint, so the page never flashes light.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agent.web import i18n

PAGE = Path(i18n.__file__).resolve().parent / "dashboard_static" / "workbench.html"


def _css() -> str:
    page = PAGE.read_text(encoding="utf-8")
    return page[page.index("<style>"):page.index("</style>")]


def _block(css: str, selector: str) -> dict[str, str]:
    body = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S).group(1)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))


def test_the_theme_is_set_before_the_first_paint():
    page = PAGE.read_text(encoding="utf-8")
    head = page[:page.index("<style>")]
    assert 'setAttribute("data-theme"' in head and "prefers-color-scheme: dark" in head
    assert 'localStorage.getItem("wb.theme")' in head


def test_no_page_colour_is_hard_coded():
    css = _css()
    rules = css[css.index("* { box-sizing"):]
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(", rules), \
        "a colour outside the palette stays light in the dark theme"


def test_the_dark_palette_sets_every_surface_and_text_colour():
    css = _css()
    light, dark = _block(css, ":root"), _block(css, ':root[data-theme="dark"]')
    surfaces = {"--film", "--paper", "--paper2", "--ink", "--ink2", "--ink3", "--rule",
                "--rule2", "--field", "--sel", "--sel2", "--pick", "--glass", "--shade",
                "--scrim", "--go-hover", "--red", "--blue", "--amber", "--green"}
    assert surfaces <= set(light) and surfaces <= set(dark)


def _luminance(hex_colour: str) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_dark_text_meets_aa_on_every_surface():
    dark = _block(_css(), ':root[data-theme="dark"]')
    for text in ("--ink", "--ink2", "--ink3", "--red", "--blue", "--amber", "--green"):
        for surface in ("--film", "--paper", "--paper2"):
            assert _contrast(dark[text], dark[surface]) >= 4.5, (text, surface)


def test_the_sheet_follows_the_theme_and_the_choice_is_offered():
    page = PAGE.read_text(encoding="utf-8")
    assert 'id="theme"' in page and 'value="system"' in page and 'value="dark"' in page
    assert re.search(r'C\.film = C\.hole = v\("--film"\); C\.frame = v\("--ink"\)', page)
    assert "ctx.fillStyle = C.tip" in page
