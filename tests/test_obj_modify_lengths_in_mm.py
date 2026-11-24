# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""obj_modify writes a board length in mm when it is named with _mm (#444).

obj_query reads a board property in mm when it is asked for with _mm
(X_mm, Width_mm). obj_modify had no such name: in a mm session a plain
Y=37.6 was written as 37.6 mil (0.955 mm) and the reply said
length_units mm, and Y_mm=44.55 was refused as an unknown property. Now a
board length named with its unit (Y_mm, Width_mils) is written by
Altium's own name in mils; a plain name is mils in any session, and a mm
call's reply says so. The set is Altium's own text, so the server's unit
conversion leaves it as given: a lower-case y in a dict was converted to
mils there while Y was not.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from eda_agent import lengths
from eda_agent.tools import generic
from eda_agent.tools.generic import _set_in_mils

from tests.pascal_source import load

ROOT = Path(__file__).resolve().parents[1]


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, dict(params or {})))
        return {"success": True, "matched": 1, "objects": [], "count": 0}


def _tools(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    generic.register_generic_tools(_Mcp())
    return captured, bridge


def test_a_board_length_named_with_its_unit_is_written_in_mils():
    assert _set_in_mils("Y_mm=44.55", "eTextObject") == ("Y=1753.937008", "")
    assert _set_in_mils("Width_mils=10|Net=GND", "track") == ("Width=10|Net=GND", "")
    # Any case of the name and the unit; Altium's spelling goes out.
    assert _set_in_mils("y_MM=1", "eTrackObject") == ("Y=39.370079", "")
    assert _set_in_mils("Text=a_mm|X_mm=-0.5", "eTextObject") == (
        "Text=a_mm|X=-19.685039", "")
    # A plain name is Altium's own, in mils: as given.
    assert _set_in_mils("Y=37.6|Text=H", "eTextObject") == ("Y=37.6|Text=H", "")


def test_a_schematic_set_goes_as_given():
    assert _set_in_mils("Location.X_mm=1", "ePort") == ("Location.X_mm=1", "")


@pytest.mark.parametrize("set_str, words", [
    ("Rotation_mm=90", ("Rotation", "not a board length")),
    ("Y_mm=abc", ("Y_mm=abc", "not a number")),
    ("Y_mm=inf", ("not a number",)),
    ("Y=1|Y_mm=1", ("Y is given twice",)),
])
def test_a_length_that_cannot_be_written_is_refused(set_str, words):
    sent, why = _set_in_mils(set_str, "eTextObject")
    assert sent == set_str
    for w in words:
        assert w in why


def test_obj_modify_sends_the_mil_value_by_altiums_name(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["obj_modify"](object_type="eTextObject",
                                    filter="Layer=BottomOverlay|Text=H",
                                    set="Y_mm=44.55"))
    command, params = bridge.sent[-1]
    assert command == "generic.modify_objects"
    assert (params["object_type"], params["filter"], params["set"]) == (
        "eTextObject", "Layer=BottomOverlay|Text=H", "Y=1753.937008")
    asyncio.run(tools["obj_modify"](object_type="eTrackObject", filter="Net=GND",
                                    set={"Width_mm": 0.254, "Locked": True}))
    assert bridge.sent[-1][1]["set"] == "Width=10|Locked=true"


def test_each_item_is_converted_by_its_own_type(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    asyncio.run(tools["obj_modify"](items=[
        {"object_type": "eViaObject", "filter": "Net=GND", "set": {"HoleSize_mm": 0.3}},
        {"object_type": "ePort", "filter": "Text=A", "set": "Location.X=100"},
    ]))
    ops = bridge.sent[-1][1]["operations"].split("~~")
    assert ops[0].endswith(";set=HoleSize=11.811024")
    assert ops[1].endswith(";set=Location.X=100")


def test_a_refused_set_sends_nothing(monkeypatch):
    tools, bridge = _tools(monkeypatch)
    out = asyncio.run(tools["obj_modify"](object_type="eTextObject",
                                          filter="Text=H", set="Rotation_mm=90"))
    assert out["error"] == "BAD_SET" and out["matched"] == 0
    assert "nothing was written" in out["reason"]
    out = asyncio.run(tools["obj_modify"](items=[
        {"object_type": "eTextObject", "filter": "Text=H", "set": "Y_mm=1"},
        {"object_type": "eTextObject", "filter": "Text=J", "set": "Y_mm=x"},
    ]))
    assert out["error"] == "BAD_SET" and "operations[1].set" in out["reason"]
    assert bridge.sent == []


def test_the_length_list_is_the_scripts_own():
    body = load(ROOT / "scripts" / "altium" / "PCBGeneric.pas", minimum=20)
    names = set(re.findall(r"PropName\s*=\s*'([^']+)'", body["IsLengthProperty"]))
    assert len(names) > 30
    assert names == set(lengths.BOARD_LENGTH_PROPERTIES)


# ------------------------------------------------- through the server's boundary

@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from eda_agent.config import get_config
    monkeypatch.setattr(get_config(), "workspace_dir", tmp_path, raising=False)
    return tmp_path


def _server(monkeypatch):
    from eda_agent.server import _CompactFastMCP
    from eda_agent.tools.folds import FoldingServer
    from eda_agent.tools.length_units import register_length_units_tools
    bridge = _Bridge()
    monkeypatch.setattr(generic, "get_bridge", lambda: bridge)
    server = _CompactFastMCP("obj-mm-test")
    mcp = FoldingServer(server)
    generic.register_generic_tools(mcp)
    register_length_units_tools(mcp)
    mcp.finish()
    return server, bridge


def _call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    blocks, _ = result if isinstance(result, tuple) else (result, None)
    return json.loads(blocks[0].text)


def test_a_mm_session_writes_mm_by_name_and_says_when_a_length_went_in_mils(
        workspace, monkeypatch):
    server, bridge = _server(monkeypatch)
    _call(server, "length_units", {"to": "mm"})
    out = _call(server, "obj_modify", {"object_type": "eTextObject",
                                       "filter": "Text=H", "set": "Y_mm=44.55"})
    assert bridge.sent[-1][1]["set"] == "Y=1753.937008"
    assert out["length_units"] == "mm" and "length_note" not in out
    out = _call(server, "obj_modify", {"object_type": "eTextObject",
                                       "filter": "Text=H", "set": "Y=37.6"})
    assert bridge.sent[-1][1]["set"] == "Y=37.6"
    assert "Y in mils" in out["length_note"] and "Y_mm" in out["length_note"]


def test_a_dict_set_is_altiums_text_whatever_the_case(workspace, monkeypatch):
    server, bridge = _server(monkeypatch)
    _call(server, "length_units", {"to": "mm"})
    # y and width are lengths in the server's vocabulary; here they are
    # Altium's names and went in another unit than Y and Width.
    _call(server, "obj_modify", {"object_type": "eTrackObject", "filter": "Net=GND",
                                 "set": {"y": 37.6, "width": 0.2}})
    assert bridge.sent[-1][1]["set"] == "y=37.6|width=0.2"
    out = _call(server, "obj_modify", {"object_type": "eTrackObject",
                                       "filter": "Net=GND", "set": {"Width": 10}})
    assert out["length_note"].startswith("Width in mils")
    _call(server, "obj_modify", {"object_type": "eTrackObject", "filter": "Net=GND",
                                 "set": {"Y_mm": 1}})
    assert bridge.sent[-1][1]["set"] == "Y=39.370079"
    _call(server, "obj_modify", {"items": [{"object_type": "eTrackObject",
                                            "filter": "Net=GND",
                                            "set": {"x": 1, "X1_mm": 1}}]})
    assert bridge.sent[-1][1]["operations"].endswith(";set=x=1|X1=39.370079")


def test_a_plain_length_read_in_a_mm_session_is_named(workspace, monkeypatch):
    server, _ = _server(monkeypatch)
    _call(server, "length_units", {"to": "mm"})
    out = _call(server, "obj_query", {"object_type": "eTrackObject",
                                      "properties": "X_mm,Width,Net"})
    assert out["length_note"].startswith("Width in mils")
    out = _call(server, "obj_query", {"object_type": "eTrackObject",
                                      "properties": "X_mm,Net"})
    assert "length_note" not in out
    # Schematic lengths are not board lengths.
    out = _call(server, "obj_query", {"object_type": "ePort", "properties": "X,Y"})
    assert "length_note" not in out


def test_a_mil_session_needs_no_note(workspace, monkeypatch):
    server, bridge = _server(monkeypatch)
    _call(server, "length_units", {"to": "mil"})
    out = _call(server, "obj_modify", {"object_type": "eTextObject",
                                       "filter": "Text=H", "set": "Y=37.6"})
    assert bridge.sent[-1][1]["set"] == "Y=37.6" and "length_note" not in out
