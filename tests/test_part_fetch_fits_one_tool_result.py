# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""part_fetch keeps its reply to one tool result.

A demo run fetched an EasyEDA part by LCSC id and got 89 KB back, the raw
shapes included. Claude Code set it aside in a file under the user's
profile and printed the path.
"""
from __future__ import annotations

import json

from eda_agent.tools import parts


def test_a_small_detail_goes_as_it_is():
    assert parts._fit_detail({"mpn": "X"}, "easyeda", "C1") == {}


def test_a_big_detail_is_outlined_and_written_out(tmp_path, monkeypatch):
    from eda_agent import config

    class _Cfg:
        workspace_dir = tmp_path

    monkeypatch.setattr(config, "get_config", lambda: _Cfg())
    detail = {"title": "USB-C", "lcsc": {"number": "C165948"},
              "shapes": ["PAD~" + "x" * 200] * 500, "dataStr": "y" * 50_000}
    out = parts._fit_detail(detail, "easyeda", "C165948")
    assert out["detail"]["title"] == "USB-C"
    assert out["detail"]["lcsc"] == {"number": "C165948"}
    assert out["detail"]["shapes"] == "<500 entries>"
    assert len(json.dumps(out)) < 5_000
    assert json.loads((tmp_path / "parts" / "easyeda_C165948.json").read_text())["title"] == "USB-C"
