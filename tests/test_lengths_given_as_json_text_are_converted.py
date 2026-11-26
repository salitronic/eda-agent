# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A list given as its JSON text has its lengths converted like a list.

In a mil session ``footprint_check_3d_model`` was given a footprint's
pads and courtyard in mils, each as the JSON text of its list (a client
often sends a list that way). The length conversion saw two strings and
left them; the server's argument check then read the text into lists and
handed them to the tool, which works in millimetres. The reply was
converted back to mils: the courtyard given as 62.99 came back as 2480,
no pad lay under a terminal, and the reason said the model "may be of
another package". The same call with ``length_units: "mm"`` passed.

The text is now read into its value before the lengths are converted,
for a list, an object and a number, on every tool and through
tool_invoke. Text an argument takes as text stays text.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Optional

import pytest

PADS = [{"designator": "1", "x": -39.3701, "y": -17.2244,
         "x_size": 7.874, "y_size": 26.5748}]
YARD = [-62.99, -51.18, 62.99, 51.18]
TOOL = "footprint_check_3d_model"


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    return tmp_path


@pytest.fixture
def seen(monkeypatch, workspace):
    """The 3D check with the STEP reading replaced: what reached it."""
    from eda_agent.design import model_fit
    got: dict[str, Any] = {}

    def fit_report(step_path, pads, **kw):
        got.update(pads=pads, **kw)
        return {"ok": True, "courtyard": kw.get("courtyard"),
                "body_box_mm": [0, 0, 2.54, 2.54]}

    monkeypatch.setattr(model_fit, "fit_report", fit_report)
    return got


def _call(name: str, arguments: dict) -> dict:
    from eda_agent.server import mcp
    out = asyncio.run(mcp.call_tool(name, arguments))
    body = out[1] if isinstance(out, tuple) else json.loads(out[0].text)
    return body.get("result", body) if name == "tool_invoke" else body


def _in_mm(seen: dict) -> None:
    assert seen["pads"][0]["x"] == pytest.approx(-1.0, abs=1e-5)
    assert seen["pads"][0]["y_size"] == pytest.approx(0.675, abs=1e-5)
    assert seen["courtyard"] == pytest.approx([-1.6, -1.3, 1.6, 1.3], abs=1e-3)


def test_lists_reach_the_tool_in_its_unit(seen):
    out = _call(TOOL, {"step_path": "a.step", "pads": PADS, "courtyard": YARD})
    _in_mm(seen)
    assert out["courtyard"] == pytest.approx(YARD) and out["length_units"] == "mil"


def test_the_same_lists_as_json_text_do_too(seen):
    out = _call(TOOL, {"step_path": "a.step", "pads": json.dumps(PADS),
                       "courtyard": json.dumps(YARD)})
    _in_mm(seen)
    # It came back as 2480 mil: given in mil, taken as mm, returned in mil.
    assert out["courtyard"] == pytest.approx(YARD)
    assert out["body_box_mils"] == pytest.approx([0, 0, 100, 100])


def test_through_tool_invoke(seen):
    out = _call("tool_invoke", {"name": TOOL, "arguments": {
        "step_path": "a.step", "pads": json.dumps(PADS), "courtyard": json.dumps(YARD)}})
    _in_mm(seen)
    assert out["courtyard"] == pytest.approx(YARD)


def test_tool_invoke_given_its_arguments_as_text(seen):
    out = _call("tool_invoke", {"name": TOOL, "arguments": json.dumps({
        "step_path": "a.step", "pads": PADS, "courtyard": YARD})})
    _in_mm(seen)
    assert out["courtyard"] == pytest.approx(YARD) and out["length_units"] == "mil"


def test_a_number_given_as_text(seen):
    _call(TOOL, {"step_path": "a.step", "pads": PADS, "offset_x": "100",
                 "tolerance": "3.937"})
    assert seen["offset_x"] == pytest.approx(2.54)
    assert seen["tolerance"] == pytest.approx(0.1, abs=1e-4)


def test_in_the_tools_own_unit_the_text_is_read_and_nothing_scaled(seen):
    mm = [{"designator": "1", "x": -1.0, "y": -0.4375, "x_size": 0.2, "y_size": 0.675}]
    out = _call(TOOL, {"step_path": "a.step", "pads": json.dumps(mm),
                       "courtyard": "[-1.6, -1.3, 1.6, 1.3]", "length_units": "mm"})
    assert seen["pads"] == mm and seen["courtyard"] == [-1.6, -1.3, 1.6, 1.3]
    assert out["courtyard"] == [-1.6, -1.3, 1.6, 1.3] and out["length_units"] == "mm"


# ------------------------------------------------------------ which text


def _server(fn):
    """A server that knows one tool, ``t``."""
    return SimpleNamespace(hidden={"t": SimpleNamespace(fn=fn)})


def _read(fn, **args):
    from eda_agent.server import _text_as_values
    return _text_as_values(_server(fn), "t", args)


def test_text_an_argument_takes_as_text_stays_text():
    def t(spec_json: str = "", note: Optional[str] = None, vertices: "str" = ""):
        pass

    args = {"spec_json": '{"pitch": 0.5}', "note": "[1, 2]", "vertices": "0,0,100,0"}
    assert _read(t, **args) == args


def test_a_list_an_object_and_a_number_are_read():
    def t(pads: list[dict[str, Any]], box: Optional[dict] = None,
          package: Any = None, x: float = 0.0, n: Optional[int] = None,
          names: "list[str]" = ()):
        pass

    got = _read(t, pads='[{"x": 1}]', box='{"left": 0}', package='{"pitch": 0.5}',
                x="2.5", n="3", names='["a"]')
    assert got == {"pads": [{"x": 1}], "box": {"left": 0}, "package": {"pitch": 0.5},
                   "x": 2.5, "n": 3, "names": ["a"]}


@pytest.mark.parametrize("value", ["true", "null", '"text"', "R1", "", "1e", "[1, 2"])
def test_text_that_is_no_list_object_or_number_is_left(value):
    def t(x: float = 0.0, flag: bool = False, pads: Optional[list] = None):
        pass

    for key in ("x", "flag", "pads"):
        assert _read(t, **{key: value}) == {key: value}


def test_a_number_as_text_is_left_where_text_is_also_taken():
    def t(value: "float | str" = 0.0, anything: Any = None):
        pass

    args = {"value": "10", "anything": "10"}
    assert _read(t, **args) == args


def test_values_and_unknown_names_pass_through_untouched():
    def t(pads: Optional[list] = None, x: float = 0.0):
        pass

    args = {"pads": [{"x": 1}], "x": 2.0, "other": "[1]"}
    from eda_agent.server import _text_as_values
    assert _text_as_values(_server(t), "t", args) is args
    assert _text_as_values(_server(t), "unknown", args) is args
