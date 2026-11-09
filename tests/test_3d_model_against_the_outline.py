# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A hand-drawn 3D model is checked against the outline drawing's totals."""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.design.model_drawing import check_model_dims

_BODY = {"kind": "box", "role": "body", "x0": -2.45, "y0": -1.95, "z0": 0.1,
         "x1": 2.45, "y1": 1.95, "z1": 1.65}
_LEAD_L = {"kind": "box", "role": "metal", "x0": -3.0, "y0": -0.2, "z0": 0,
           "x1": -2.45, "y1": 0.2, "z1": 0.3}
_LEAD_R = {**_LEAD_L, "x0": 2.45, "x1": 3.0}


def test_a_model_matching_the_drawing_passes():
    out = check_model_dims([_BODY, _LEAD_L, _LEAD_R], {
        "body_x": [4.8, 5.0], "body_y": 3.9, "height": [1.35, 1.75], "span_x": 6.0})
    assert out["ok"], out
    assert {r["dim"]: r["model"] for r in out["rows"]}["span_x"] == 6.0


def test_a_body_a_millimetre_long_is_refused_and_named():
    out = check_model_dims([_BODY], {"body_x": 3.9, "body_y": 3.9, "height": 1.65})
    assert not out["ok"] and "body_x drawing 3.9, model 4.9" in out["reason"]


def test_a_single_value_allows_two_percent_or_fifty_microns():
    long = {**_BODY, "x1": 2.5}
    assert check_model_dims([long], {"body_x": 4.9, "body_y": 3.9,
                                     "height": 1.65})["ok"]
    assert not check_model_dims([{**_BODY, "x1": 2.6}], {
        "body_x": 4.9, "body_y": 3.9, "height": 1.65})["ok"]


@pytest.mark.parametrize("drawing,words", [
    ({}, "give drawing"),
    ({"body_x": 1, "body_y": 1}, "needs height"),
    ({"body_x": 1, "body_y": 1, "height": 1, "depth": 2}, "not known"),
    ({"body_x": [1, 2, 3], "body_y": 1, "height": 1}, "[min, max]"),
])
def test_missing_or_unknown_dimensions_are_refused(drawing, words):
    out = check_model_dims([_BODY], drawing)
    assert not out["ok"] and words in out["reason"]


def test_round_and_swept_solids_are_measured():
    cyl = {"kind": "cylinder", "role": "body", "cx": 0, "cy": 0, "r": 2.5,
           "z0": 0, "z1": 5}
    prism = {"kind": "prism", "role": "metal", "profile": [[3, 0], [4, 0], [4, 1]],
             "y0": -0.5, "y1": 0.5}
    out = check_model_dims([cyl, prism], {"body_x": 5, "body_y": 5, "height": 5,
                                          "span_x": 6.5})
    assert out["ok"], out


def test_the_panel_can_switch_the_body_check_off(monkeypatch, tmp_path):
    from eda_agent import preferences
    from tests.test_3d_terminals_come_from_the_footprint import _LANDS, _tool
    monkeypatch.setattr(preferences, "_read",
                        lambda workspace=None: {"checks_off": "model_body"})
    run, sent, written = _tool(monkeypatch, tmp_path, _LANDS)
    out = asyncio.run(run("LGA3", [_BODY], "drawing p.1"))
    assert out["ok"] and out["unchecked"]["check"] == "model_body"


def test_without_the_drawing_the_tool_writes_nothing(monkeypatch, tmp_path):
    from tests.test_3d_terminals_come_from_the_footprint import _LANDS, _tool
    run, sent, written = _tool(monkeypatch, tmp_path, _LANDS)
    out = asyncio.run(run("LGA3", [_BODY], "drawing p.1"))
    assert not out["ok"] and out["check"] == "model_body"
    assert not written and "library.link_3d_model" not in [c for c, _ in sent]
