# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""obj_zoom(action="object") centres a named object on a schematic sheet.

A name is looked up among a sheet's parts (designator), net labels, ports,
power ports, off-sheet connectors and harness connectors (type label),
without case. The view is fitted round the match's BoundingRectangle, the
box Altium draws round a selected object, so that box fills scale % of
the window along the side that limits it. The zoom is Sch:Zoom
Action=ZoomArea with the corners in internal units. Every match is listed;
index picks the one the view goes to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agent.tools import generic as generic_tools
from tests.altium_simulator import AltiumSimulator

GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _routine(name: str) -> str:
    src = GENERIC.read_text(encoding="latin-1")
    start = src.index("Function " + name + "(")
    return src[start:src.index("\nFunction ", start + 1)]


# ------------------------------------------------------------------ the Pascal

def test_the_command_is_routed():
    src = GENERIC.read_text(encoding="latin-1")
    assert "'zoom_to_object':   Result := Gen_ZoomToObject(Params, RequestId);" in src


def test_the_walk_is_filtered_to_the_six_kinds():
    body = _routine("Gen_ZoomToObject")
    at = body.index("SchIter.AddFilter_ObjectSet(MkSet(")
    kinds = body[at:body.index("));", at)]
    for kind in ("eSchComponent", "eNetLabel", "ePort", "ePowerObject",
                 "eCrossSheetConnector", "eHarnessConnector"):
        assert kind in kinds, kind
    assert at < body.index("SchIter.FirstSchObject")


def test_each_kind_is_named_by_its_own_member():
    body = _routine("SchZoomObjectName")
    assert "GetSchComponentSubText(Obj, 'Designator')" in body
    # A port has no Text: asking stops the script. It answers to Name, and
    # its branch comes before the Text one.
    port = body.index("Obj.ObjectId = ePort")
    assert "Result := Obj.Name" in body[port:body.index("eHarnessConnector", port)]
    harness = body.index("Obj.ObjectId = eHarnessConnector")
    branch = body[harness:body.index("SchObjectHasText(Obj)", harness)]
    assert "Harness.HarnessConnectorType" in branch
    assert "Result := TypeLabel.Text" in branch
    assert body.index("If SchObjectHasText(Obj) Then") > harness
    assert "Result := Obj.Text" in body[body.index("If SchObjectHasText(Obj) Then"):]


def test_names_compare_without_case():
    body = _routine("Gen_ZoomToObject")
    assert "NameUpper := UpperCase(NameStr);" in body
    assert "(UpperCase(ObjName) = NameUpper)" in body


def test_the_box_is_the_bounding_rectangle():
    body = _routine("Gen_ZoomToObject")
    for corner in ("X1", "Y1", "X2", "Y2"):
        assert f"{corner} := Obj.BoundingRectangle.{corner};" in body, corner


def test_the_box_fills_scale_percent_of_the_view_centred():
    body = _routine("Gen_ZoomToObject")
    assert "Cx := (BX1 + BX2) Div 2;" in body
    assert "Cy := (BY1 + BY2) Div 2;" in body
    assert "Hw := Round(Abs(BX2 - BX1) * 50 / Scale);" in body
    assert "Hh := Round(Abs(BY2 - BY1) * 50 / Scale);" in body
    assert "If Hw < MilsToCoordF(1) Then Hw := MilsToCoordF(1);" in body
    assert "If Hh < MilsToCoordF(1) Then Hh := MilsToCoordF(1);" in body
    zoom = body[body.index("ResetParameters;"):body.index("RunProcess('Sch:Zoom');")]
    assert "AddStringParameter('Action', 'ZoomArea');" in zoom
    for name, value in (("Location1.x", "Cx - Hw"), ("Location1.y", "Cy - Hh"),
                        ("Location2.x", "Cx + Hw"), ("Location2.y", "Cy + Hh")):
        assert f"AddStringParameter('{name}', IntToStr({value}));" in zoom, name


def test_scale_and_index_are_checked_before_the_walk():
    body = _routine("Gen_ZoomToObject")
    walk = body.index("SchDoc.SchIterator_Create")
    assert "If ScaleStr = '' Then Scale := 50" in body
    assert body.index("If (Scale <= 0) Or (Scale > 100) Then") < walk
    # StrToIntDef raises past 32 bits in Altium: the text is checked first.
    guard = body.index("(Length(IndexStr) > 6) Or (Not IsIntStr(IndexStr))")
    assert guard < body.index("StrToIntDef(IndexStr, 0)") < walk
    assert body.index("If Want < 1 Then") < walk


def test_only_a_schematic_sheet_is_searched():
    body = _routine("Gen_ZoomToObject")
    assert "If FocusedKind <> 'SCH' Then" in body
    assert body.index("If FocusedKind <> 'SCH' Then") < body.index("SchDoc.SchIterator_Create")
    # The code whose hint says the tool acts on an open .SchDoc.
    refusal = body[body.index("If FocusedKind <> 'SCH' Then"):]
    assert refusal.index("'NO_SCHDOC'") < refusal.index("End;")


def test_a_missing_name_and_a_short_list_are_refused():
    body = _routine("Gen_ZoomToObject")
    assert "'NOT_FOUND'" in body
    assert "If Want > Count Then" in body
    # The view goes to the index-th match, not a later one.
    picked = body[body.index("If Count = Want Then"):]
    assert picked.index("BX1 := X1;") < picked.index("End;")
    assert body.index("If Want > Count Then") < body.index("RunProcess('Sch:Zoom');")


# ------------------------------------------------------------------ the tool

class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params):
        self.sent.append((command, params))
        return {"success": True}


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap


@pytest.fixture
def zoom(monkeypatch):
    bridge = _Bridge()
    monkeypatch.setattr(generic_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    generic_tools.register_generic_tools(mcp)
    return mcp.tools["obj_zoom"], bridge


@pytest.mark.asyncio
async def test_object_sends_the_name_scale_and_index(zoom):
    obj_zoom, bridge = zoom
    await obj_zoom(action="object", name=" NET_A ", scale=40, index=2)
    assert bridge.sent == [("generic.zoom_to_object",
                            {"name": "NET_A", "scale": "40.0", "index": "2"})]


@pytest.mark.asyncio
async def test_object_leaves_the_defaults_to_altium(zoom):
    obj_zoom, bridge = zoom
    await obj_zoom(action="object", name="U1")
    assert bridge.sent == [("generic.zoom_to_object", {"name": "U1"})]


@pytest.mark.asyncio
async def test_object_without_a_name_is_refused(zoom):
    obj_zoom, bridge = zoom
    reply = await obj_zoom(action="object", name="  ")
    assert reply["success"] is False
    assert bridge.sent == []


# ------------------------------------------------------------------ the simulator

def _sim(tmp_path, params):
    sim = AltiumSimulator(str(tmp_path))
    return json.loads(sim._dispatch("generic.zoom_to_object", params, "r1"))


def test_the_simulator_finds_a_net_label_and_a_part_without_case(tmp_path):
    reply = _sim(tmp_path, {"name": "vcc"})
    assert reply["success"], reply
    data = reply["data"]
    assert data["count"] == 1
    assert data["zoomed_to"]["kind"] == "net_label"
    assert data["zoomed_to"]["name"] == "VCC"
    part = _sim(tmp_path, {"name": "r1", "scale": "25"})["data"]
    assert part["zoomed_to"]["kind"] == "part"
    assert part["scale"] == 25.0


def test_the_simulator_refuses_what_altium_refuses(tmp_path):
    assert _sim(tmp_path, {"name": "NOPE"})["error"]["code"] == "NOT_FOUND"
    assert _sim(tmp_path, {"name": ""})["error"]["code"] == "MISSING_PARAMS"
    assert _sim(tmp_path, {"name": "VCC", "scale": "0"})["error"]["code"] == "BAD_PARAMS"
    assert _sim(tmp_path, {"name": "VCC", "index": "2"})["error"]["code"] == "BAD_PARAMS"
