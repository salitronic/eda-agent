# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The window's Help button opens the help page: a labelled picture of the
window, then every setting on the Config tab."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from eda_agent.bridge import altium_bridge as ab
from eda_agent.web import dashboard
from eda_agent.web import help as H

ROOT = Path(__file__).resolve().parents[1]
DFM = (ROOT / "scripts" / "altium" / "StatusForm.dfm").read_text(encoding="utf-8")
PAS = (ROOT / "scripts" / "altium" / "StatusForm.pas").read_text(encoding="utf-8")
PAGE = H.PAGE.read_text(encoding="utf-8")


def _path(uri: str) -> Path:
    p = unquote(urlparse(uri).path)
    return Path(p[1:] if re.match(r"/[A-Za-z]:", p) else p)


def test_the_help_button_opens_the_page():
    assert "OnClick = btn_HelpClick" in DFM
    assert re.search(r"Procedure btn_HelpClick\(Sender : TObject\);\s*"
                     r"Begin OpenConfigHelp\(''\); End;", PAS)
    assert "'help:' + Setting" in PAS


def test_every_setting_on_the_tab_has_a_section():
    rows = set(re.findall(r"object lbl_Cfg(\w+): TLabel", DFM))
    settings = {r for r in rows if not r.startswith("Grp") and not r.endswith("Hint")}
    sections = re.findall(r'<section class="setting" id="([a-z0-9-]+)">', PAGE)
    # One section per setting, plus what to do when a check refuses.
    assert len(sections) == len(settings) + 1
    for s in sections:
        assert f'href="#{s}"' in PAGE, s


def test_every_section_of_the_window_is_pictured_and_labelled(tmp_path):
    from eda_agent.web.help_figures import FIGURES, HERE, svg

    for name, fig in FIGURES.items():
        assert f"<!-- figure:{name} -->" in PAGE, name
        raw = (HERE / fig["picture"]).read_bytes()
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        for title, text, x, y, where in fig["labels"]:
            assert title and text and where in ("above", "below", "left", "right")
            assert 0 <= x <= w and 0 <= y <= h, (name, title)
        drawn = svg(name)
        assert drawn.count('class="leader"') == len(fig["labels"])
    copy = _path(H.link_target("help:", tmp_path)).read_text(encoding="utf-8")
    assert "<!-- figure:" not in copy and copy.count("<svg") == len(FIGURES)
    assert copy.count("data:image/png;base64,") == len(FIGURES)


def test_help_link_opens_a_copy_that_jumps_to_the_setting(tmp_path):
    uri = H.link_target("help:routing\n", tmp_path)
    copy = _path(uri)
    assert copy.parent == tmp_path / "help"
    assert '<html lang="en" data-jump="routing">' in copy.read_text(encoding="utf-8")
    # An unknown setting opens the page at the top.
    top = _path(H.link_target("help:nope", tmp_path)).read_text(encoding="utf-8")
    assert "<html lang=\"en\">" in top


def test_other_link_text_is_left_alone(tmp_path):
    assert H.link_target("http://127.0.0.1:8766/", tmp_path) == "http://127.0.0.1:8766/"
    assert H.link_target("https://example.org/x", tmp_path) == "https://example.org/x"
    assert H.link_target("file:///c:/x.html", tmp_path) is None
    assert H.link_target('help:x" onload="y', tmp_path) is None
    assert not (tmp_path / "help").exists()


def test_both_watchers_open_help_links():
    for fn in (ab.AltiumBridge._maybe_open_dashboard_sentinel,
               dashboard._watch_open_dashboard_sentinel):
        assert "link_target(" in inspect.getsource(fn), fn.__name__


def test_the_dashboard_serves_the_page(tmp_path):
    app = dashboard.create_app(tmp_path)
    got = app.test_client().get("/help/config", headers={"Host": "127.0.0.1:8766"})
    assert got.status_code == 200
    body = got.get_data(as_text=True)
    assert 'id="routing"' in body and "data:image/png;base64," in body
    assert "<!-- figure:" not in body
