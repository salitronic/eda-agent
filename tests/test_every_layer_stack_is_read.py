# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""A rigid-flex board's every layer stack is read, not only the first.

A rigid-flex board has one layer stack per kind of board region, each with
its own layers, via spans and impedance lines. The stack readers reported
the first stack only, and pcb.layer_stack_manager's drill pairs answered
the first stack's pair again for a pair of another stack.

Measured on AD 25.8.1, two stacks: the board's substacks
(MasterLayerStack.GetState_Substacks(K)) each answer their own impedance
lines; a manager stack's ``Layers`` lists every layer of the board, and a
manager layer's ``Id`` stops the script with an access violation, so a
stack's own layers are read from the manager's model (StackupDocument XML).
The substacks come in the model's stack order.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from eda_agent.layout import stack_model as sm
from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"

_NS = "http://altium.com/ns/LayerStackManager"
_COPPER = "f4eccd87-2cfb-4f37-be50-4f3a272b4d01"
_CORE = "136c62ef-1fa6-4897-ae71-7e797b632b92"
_COVER = "7b384237-13d8-4318-8bcb-accd8d9a51e7"  # solder mask: a coverlay


def _layer(guid, type_id, name, thick="1.4mil", enabled=None):
    on = "" if enabled is None else ' IsEnabled="%s"' % enabled
    return ('<Layer Id="%s" TypeId="%s" Name="%s"%s><Properties>'
            '<Property Name="Thickness" Type="LengthValue">%s</Property>'
            '</Properties></Layer>' % (guid, type_id, name, on, thick))


#: Rigid: four copper layers. Flex (a board-region stack): the two inner
#: layers between coverlays. One profile with a line on each stack.
MODEL = (
    '<?xml version="1.0"?><StackupDocument xmlns="{NS}"><Stackup Type="Standard"><Stacks>'
    '<Stack Id="a0" Name="Rigid" IsFlex="True"><Layers>'
    + _layer("l1", _COPPER, "Top Layer")
    + _layer("d1", _CORE, "Core 1", "0.1mm")
    + _layer("l2", _COPPER, "Mid-Layer 1", enabled="False")
    + _layer("d2", _CORE, "Core 2", "25um")
    + _layer("l3", _COPPER, "Mid-Layer 2")
    + _layer("l4", _COPPER, "Bottom Layer")
    + '</Layers><ViaSpans><ViaSpan AutoName="Thru 1:4" Type="ThruVia" '
    'StartLayerId="l1" StopLayerId="l4"/></ViaSpans><DrillSpans/></Stack>'
    '<Stack Id="b0" Name="Flex" Type="BoardRegionLayerStack" IsFlex="True"><Layers>'
    + _layer("c1", _COVER, "Top Coverlay")
    + _layer("l2", _COPPER, "Mid-Layer 1")
    + _layer("d2", _CORE, "Core 2", "25um")
    + _layer("l3", _COPPER, "Mid-Layer 2")
    + '</Layers><ViaSpans><ViaSpan AutoName="Thru 2:3" CustomName="Flex thru" Type="ThruVia" '
    'StartLayerId="l2" StopLayerId="l3"/></ViaSpans><DrillSpans/></Stack></Stacks>'
    '<ImpedanceProfiles><ImpedanceProfile AutoName="S50" CustomName="SE-50" Type="Single" '
    'Impedance="50ohm" Tolerance="10%"><TransmissionLines>'
    '<TransmissionLine LayerId="l1" StackId="a0" TLTypeName="Coated Microstrip" '
    'BottomRefLayerId="l2" TraceWidth="6mil" TraceGap="5mil" ImpedanceError="1.5%">'
    '<ElectricParameters Z="50.7ohm"/></TransmissionLine>'
    '<TransmissionLine LayerId="l3" StackId="b0" TLTypeName="Offset Stripline" '
    'TopRefLayerId="l2" TraceWidth="0.0762mm" ImpedanceError="2%">'
    '<ElectricParameters Z="51ohm"/></TransmissionLine>'
    '</TransmissionLines></ImpedanceProfile></ImpedanceProfiles>'
    '<Branches><Branch Name="Board"><Sections>'
    '<BranchSection Id="s1" Name="Section-1"><Stacks>'
    '<BranchSectionStack LayerStackId="a0"/></Stacks></BranchSection>'
    '<BranchSection Id="s2" Name="Section-2" ParentSectionId="s1"><Stacks>'
    '<BranchSectionStack LayerStackId="b0"/></Stacks></BranchSection>'
    '</Sections></Branch></Branches></Stackup></StackupDocument>').replace("{NS}", _NS)


# --- the model -----------------------------------------------------------

def test_every_stack_with_its_own_layers_and_spans():
    m = sm.read_stack_model(MODEL)
    assert [s["name"] for s in m["stacks"]] == ["Rigid", "Flex"]
    rigid, flex = m["stacks"]
    assert [l["name"] for l in rigid["layers"]] == [
        "Top Layer", "Core 1", "Mid-Layer 1", "Core 2", "Mid-Layer 2", "Bottom Layer"]
    assert [l["name"] for l in flex["layers"]] == [
        "Top Coverlay", "Mid-Layer 1", "Core 2", "Mid-Layer 2"]
    assert (rigid["region"], flex["region"]) == (False, True)
    assert flex["layers"][0]["type"] == "solder_mask"
    assert flex["layers"][0]["type_id"] == _COVER
    assert rigid["layers"][2]["enabled"] is False
    assert rigid["via_spans"] == [{"name": "Thru 1:4", "type": "ThruVia",
                                   "start_layer": "Top Layer", "stop_layer": "Bottom Layer"}]
    assert flex["via_spans"][0]["name"] == "Flex thru"
    assert (flex["via_spans"][0]["start_layer"], flex["via_spans"][0]["stop_layer"]) == (
        "Mid-Layer 1", "Mid-Layer 2")


def test_an_unknown_layer_type_is_other_with_its_id():
    odd = "b0827674-798c-4cf8-807c-8e6c2a11c145"
    m = sm.read_stack_model(MODEL.replace(_COVER, "{%s}" % odd.upper()))
    top = m["stacks"][1]["layers"][0]
    assert (top["type"], top["type_id"]) == ("other", odd)


def test_lengths_are_mils_whatever_the_unit():
    assert sm.to_mils("6.063mil") == 6.063
    assert sm.to_mils("0.1mm") == round(100 / 25.4, 6)
    assert sm.to_mils("25um") == round(25 / 25.4, 6)
    assert sm.to_mils("0m") == 0
    assert sm.to_mils("") is None and sm.to_mils("4furlong") is None


def test_branch_sections_name_their_stack():
    (branch,) = sm.read_stack_model(MODEL)["branches"]
    assert branch["sections"] == [
        {"name": "Section-1", "parent": "", "stacks": ["Rigid"]},
        {"name": "Section-2", "parent": "Section-1", "stacks": ["Flex"]}]


def test_each_impedance_line_names_its_stack_and_references():
    (prof,) = sm.read_stack_model(MODEL)["impedance_lines"]
    assert (prof["name"], prof["impedance_ohms"], prof["tolerance_percent"]) == ("SE-50", 50, 10)
    top, flex = prof["lines"]
    assert (top["stack"], top["layer"], top["bottom_reference"], top["top_reference"]) == (
        "Rigid", "Top Layer", "Mid-Layer 1", "")
    assert (flex["stack"], flex["layer"], flex["top_reference"]) == ("Flex", "Mid-Layer 2", "Mid-Layer 1")
    assert flex["trace_width_mils"] == round(76.2 / 25.4, 6)
    assert (top["impedance_error"], top["impedance_ohms"]) == (1.5, 50.7)


# --- the impedance rows, one set per substack ------------------------------

def _rows():
    # what pcb.get_impedance_profiles reads: every copper layer of the
    # board, once per substack; the flex substack answers for the outer
    # layers too (enabled, width 0).
    rows = []
    for k in (0, 1):
        for name in ("Top Layer", "Mid-Layer 1", "Mid-Layer 2", "Bottom Layer"):
            rows.append({"layer": name, "stack_index": k, "enabled": name != "Mid-Layer 1",
                         "trace_width_mils": 0 if (k and "Layer" in name and "Mid" not in name) else 3})
    return [{"name": "SE-50", "layers": rows}]


def test_a_row_is_kept_only_where_its_stack_has_the_layer():
    (prof,) = sm.impedance_by_stack(_rows(), sm.read_stack_model(MODEL))
    got = [(r["stack"], r["layer"]) for r in prof["layers"]]
    assert got == [("Rigid", "Top Layer"), ("Rigid", "Mid-Layer 1"), ("Rigid", "Mid-Layer 2"),
                   ("Rigid", "Bottom Layer"), ("Flex", "Mid-Layer 1"), ("Flex", "Mid-Layer 2")]


def test_a_kept_row_gets_its_line_type_and_references():
    rows = sm.impedance_by_stack(_rows(), sm.read_stack_model(MODEL))[0]["layers"]
    flex = next(r for r in rows if (r["stack"], r["layer"]) == ("Flex", "Mid-Layer 2"))
    assert (flex["line_type"], flex["top_reference"], flex["bottom_reference"]) == (
        "Offset Stripline", "Mid-Layer 1", "")
    # a layer the profile is switched off on stays, with no line
    off = next(r for r in rows if (r["stack"], r["layer"]) == ("Rigid", "Mid-Layer 1"))
    assert off["enabled"] is False and off["line_type"] == ""


def test_drill_pairs_get_the_stack_whose_span_they_are():
    pairs = [{"start_layer_name": "Top Layer", "stop_layer_name": "Bottom Layer"},
             {"start_layer_name": "Mid-Layer 2", "stop_layer_name": "Mid-Layer 1"},
             {"start_layer_name": "Top Layer", "stop_layer_name": "Mid-Layer 2"}]
    sm.drill_pair_stacks(pairs, sm.read_stack_model(MODEL)["stacks"])
    assert [p["stacks"] for p in pairs] == [["Rigid"], ["Flex"], []]
    # a pair the script left unnamed takes its span's name; none stays blank
    assert [p.get("name", "") for p in pairs] == ["Thru 1:4", "Flex thru", ""]


# --- the script --------------------------------------------------------------

def test_impedance_walks_every_substack_and_says_which():
    body = load(_PCB)["PCB_GetImpedanceProfiles"]
    assert "SubCount := BoardLayerStackCount(Board);" in body
    assert "For K := 0 To SubCount - 1 Do" in body
    assert "Sub := Stack.GetState_Substacks(K);" in body
    assert "GetState_Substacks(0)" not in body
    assert "JsonInt('stack_index', K)" in body


def test_the_stack_count_is_defined_before_its_callers_and_read_off_the_board():
    text = _PCB.read_text(encoding="utf-8")
    at = text.index("Function BoardLayerStackCount(")
    assert at < text.index("Function PCB_GetImpedanceProfiles(")
    assert at < text.index("Function PCB_PhysicalStack(")
    body = load(_PCB)["BoardLayerStackCount"]
    # read off the board: the manager's cached provider is left alone, and
    # nothing here halts the script on an older build
    assert "Result := MLS.SubstackCount;" in body
    assert "LayerStackProvider" not in body and "AltiumVersionCode" not in body


def test_physical_stack_says_how_many_stacks_there_are():
    assert "JsonInt('stacks_count', BoardLayerStackCount(Board))" in load(_PCB)["PCB_PhysicalStack"]


def test_drill_pairs_are_the_boards_own():
    body = load(_PCB)["PCB_LayerStackManager"]
    assert "For I := 0 To Board.DrillLayerPairsCount - 1 Do" in body
    assert "Pair := Board.LayerPair[I];" in body
    # the stack's list is only searched for a pair on the same two layers
    assert "DP := LSI.GetDrillPairInfo(J);" in body
    assert "GetDrillPairInfo(I)" not in body
    assert "((StartId = Low) And (StopId = High)) Or ((StartId = High) And (StopId = Low))" in body


# --- the tools ---------------------------------------------------------------

class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self, refuse_manager=False, stacks=2):
        self.sent = []
        self.refuse_manager = refuse_manager
        self.stacks = stacks

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        if command == "pcb.layer_stack_manager":
            if self.refuse_manager:
                from eda_agent.bridge.exceptions import AltiumCommandError
                raise AltiumCommandError("not on this version", code="UNSUPPORTED_VERSION")
            reply = {"layers": [], "stacks_count": self.stacks, "drill_pairs": [
                {"name": "Thru 1:4", "start_layer_name": "Top Layer", "stop_layer_name": "Bottom Layer"},
                {"name": "", "start_layer_name": "Mid-Layer 1", "stop_layer_name": "Mid-Layer 2"}]}
            if params.get("include_model"):
                reply["model"] = (MODEL if params["include_model"] == "xml"
                                  else "|RECORDS=1|")
            return reply
        if command == "pcb.get_impedance_profiles":
            return {"profiles": _rows()}
        return {"layers": []}


def _tool(monkeypatch, name, bridge, **kw):
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    return asyncio.run(mcp.tools[name](**kw))


def test_the_manager_reply_lists_every_stack_and_drops_the_model(monkeypatch):
    bridge = _Bridge()
    out = _tool(monkeypatch, "pcb_layer_stack_manager", bridge, compare=False)
    assert bridge.sent == [("pcb.layer_stack_manager", {}),
                           ("pcb.layer_stack_manager", {"include_model": "xml"})]
    assert [s["name"] for s in out["stacks"]] == ["Rigid", "Flex"]
    assert out["branches"][0]["sections"][1]["stacks"] == ["Flex"]
    assert [p["stacks"] for p in out["drill_pairs"]] == [["Rigid"], ["Flex"]]
    assert "model" not in out


def test_one_stack_reads_no_model(monkeypatch):
    bridge = _Bridge(stacks=1)
    out = _tool(monkeypatch, "pcb_layer_stack_manager", bridge, compare=False)
    assert bridge.sent == [("pcb.layer_stack_manager", {})]
    assert "stacks" not in out and "model" not in out


def test_an_asked_for_model_is_still_handed_back(monkeypatch):
    bridge = _Bridge()
    out = _tool(monkeypatch, "pcb_layer_stack_manager", bridge,
                include_model="xml", compare=False)
    assert out["model"] == MODEL and len(out["stacks"]) == 2
    assert len(bridge.sent) == 1
    bridge = _Bridge()
    out = _tool(monkeypatch, "pcb_layer_stack_manager", bridge,
                include_model="records", compare=False)
    assert out["model"] == "|RECORDS=1|" and len(out["stacks"]) == 2
    assert [p.get("include_model") for _, p in bridge.sent] == ["records", "xml"]


def test_impedance_rows_are_named_by_stack(monkeypatch):
    out = _tool(monkeypatch, "pcb_get_impedance_profiles", _Bridge())
    stacks = {(r["stack"], r["layer"]) for r in out["profiles"][0]["layers"]}
    assert ("Flex", "Mid-Layer 2") in stacks and ("Flex", "Top Layer") not in stacks


def test_impedance_still_answers_where_the_manager_cannot_be_read(monkeypatch):
    out = _tool(monkeypatch, "pcb_get_impedance_profiles", _Bridge(refuse_manager=True))
    assert "not on this version" in out["stacks_note"]
    assert len(out["profiles"][0]["layers"]) == 8   # untouched, stack_index only
