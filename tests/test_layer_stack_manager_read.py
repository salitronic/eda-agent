# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""pcb_layer_stack_manager reads the Layer Stack Manager's own data.

pcb_physical_stack reads the board's layer objects; the manager holds
more (layer type, every named material / process property, via spans,
drill pairs, impedance profiles, its own model). Measured on AD 25.8.1:
the manager, its document and the provider are not in the script
engine's identifier table and must be held in Variants; the provider is
cached and returns old values after a stack change unless it is dropped
first; CreateDocument stops the script. The read must write nothing.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from eda_agent.tools import pcb as pcb_tools

from tests.pascal_source import load

_PCB = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    return load(_PCB)["PCB_LayerStackManager"]


def test_late_bound_objects_are_held_in_variants():
    body = _body()
    assert re.search(r"LSM, Doc, LSP, LSI, LI, DP : Variant;", body)
    assert "St : ILsmSdkLayerStack;" in body
    assert "Sts : ILsmSdkLayerStackCollection;" in body


def test_the_cached_provider_is_dropped_before_the_read():
    body = _body()
    drop = body.index("LSM.RemoveLayerStackProvider(Board);")
    assert drop < body.index("GetLayerStackProvider(Board);", drop + 1)
    # and nothing before the drop reads the provider
    assert "GetLayerStackProvider" not in body[:drop].replace(
        "RemoveLayerStackProvider", "")


def test_nothing_is_written_and_no_document_is_created():
    body = _body()
    assert "ApplyToPcb" not in body
    assert "CreateDocument" not in body
    assert "SetState_" not in body
    assert "OpenDocumentFromPcb(Board)" in body


def test_custom_properties_are_read_by_name():
    body = _body()
    assert "PropName := LSI.GetLayerProperty(I);" in body
    assert "PropValue := LI.GetState_CustomProperty(PropName);" in body
    assert "If PropValue <> '' Then" in body


def test_model_formats():
    body = _body()
    assert "If Model = 'xml' Then Extra := ',' + JsonStr('model', Doc.SerializeToString(1))" in body
    assert "Else If Model = 'records' Then Extra := ',' + JsonStr('model', Doc.SerializeToString(0));" in body


def test_via_span_layers_are_matched_without_case():
    body = _body()
    assert "ByGuid.Values[LsmGuidKey(LGuid)] := LName;" in body
    assert "ByGuid.Values[LsmGuidKey(VSp.StartLayerId)]" in body
    assert "ByGuid.Values[LsmGuidKey(VSp.StopLayerId)]" in body
    key = load(_PCB)["LsmGuidKey"]
    assert "Result := UpperCase(R);" in key


def test_every_layer_type_of_a_stack_has_a_name():
    # A new board's dielectric is Altium's plain Dielectric type, neither
    # core nor prepreg; it read as "other" before it had a name.
    names = load(_PCB)["LsmTypeName"]
    for guid, name in (("F4ECCD87-2CFB-4F37-BE50-4F3A272B4D01", "copper"),
                       ("136C62EF-1FA6-4897-AE71-7E797B632B92", "core"),
                       ("1A79611A-039D-4D40-A204-53C26C50F8B5", "prepreg"),
                       ("92B02D5E-8D69-48A8-880E-AC4B77DB099D", "dielectric"),
                       ("7B384237-13D8-4318-8BCB-ACCD8D9A51E7", "solder_mask"),
                       ("C7EF040E-8D00-490B-B00C-A7E7823FF174", "overlay")):
        assert f"T = '{{{guid}}}' Then Result := '{name}'" in names


def test_the_command_is_routed():
    text = _PCB.read_text(encoding="utf-8")
    assert re.search(r"'layer_stack_manager':\s+Result := PCB_LayerStackManager\(Params, RequestId\);",
                     text)


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _call(monkeypatch, **kw):
    bridge = _Bridge()
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_layer_stack_manager"](**kw))
    return out, bridge.sent


def test_no_model_sends_no_parameter(monkeypatch):
    _, sent = _call(monkeypatch)
    assert sent == [("pcb.layer_stack_manager", {})]


def test_model_formats_reach_the_script(monkeypatch):
    _, sent = _call(monkeypatch, include_model="xml")
    assert sent == [("pcb.layer_stack_manager", {"include_model": "xml"})]
    _, sent = _call(monkeypatch, include_model=" Records ")
    assert sent == [("pcb.layer_stack_manager", {"include_model": "records"})]


def test_an_unknown_format_is_refused_before_sending(monkeypatch):
    out, sent = _call(monkeypatch, include_model="json")
    assert out["ok"] is False and not sent


def test_the_tool_is_classified_read_only():
    from eda_agent.tools import metadata
    assert metadata.INTERACTION_OVERRIDES["pcb_layer_stack_manager"] == metadata.READONLY


# --- the manager against the board's own stack -----------------------------
# Measured on AD 25.8.1: once a board carries the manager's own model, a
# script write to the board's stack (pcb_set_dielectric Dk 4 -> 4.35,
# pcb_modify_layer copper 1.4 -> 2.8 mil) does not reach it, and the
# Layer Stack Manager window goes on showing the old values.

def _manager(dk="4", thick=8.0, material="FR4", kind="core", copper=1.4):
    return {"layers": [
        {"name": "L1", "type": "copper", "thickness_mils": copper},
        {"name": "D1", "type": kind, "thickness_mils": thick,
         "dielectric_constant": dk, "material": material},
        {"name": "Top Overlay", "type": "overlay", "thickness_mils": 0}]}


def _physical(dk=4.0, thick=8.0, material="FR4", dtype=1, copper=1.4):
    return {"layers": [
        {"name": "Top Paste", "kind": "paste", "thickness_mils": 0.25},
        {"name": "Top Overlay", "kind": "overlay", "thickness_mils": 0.25},
        {"name": "L1", "kind": "copper", "thickness_mils": copper},
        {"name": "D1", "kind": "dielectric", "thickness_mils": thick,
         "constant": dk, "material": material, "dielectric_type": dtype}]}


def test_agreeing_stacks_give_no_difference():
    assert pcb_tools.stack_manager_out_of_step(_manager(), _physical()) == []


def test_each_field_that_differs_is_reported():
    fields = lambda m, p: sorted(d["field"] for d in pcb_tools.stack_manager_out_of_step(m, p))
    assert fields(_manager(), _physical(dk=4.35)) == ["dielectric_constant"]
    assert fields(_manager(), _physical(thick=10.0)) == ["thickness_mils"]
    assert fields(_manager(), _physical(copper=2.8)) == ["thickness_mils"]
    assert fields(_manager(), _physical(material="370HR")) == ["material"]
    assert fields(_manager(), _physical(dtype=2)) == ["type"]
    diff = pcb_tools.stack_manager_out_of_step(_manager(), _physical(copper=2.8))
    assert diff == [{"layer": "L1", "field": "thickness_mils", "board": 2.8, "stack_manager": 1.4}]


def test_a_layer_only_one_side_has_is_reported_but_paste_is_not():
    m = _manager()
    m["layers"].append({"name": "L2", "type": "copper", "thickness_mils": 1.4})
    diff = pcb_tools.stack_manager_out_of_step(m, _physical())
    assert diff == [{"layer": "L2", "field": "layer", "board": None, "stack_manager": "copper"}]


class _StackBridge(_Bridge):
    def __init__(self, physical):
        super().__init__()
        self.physical = physical

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, dict(params or {})))
        return dict(_manager()) if command == "pcb.layer_stack_manager" else self.physical


def test_the_tool_compares_with_the_board_by_default(monkeypatch):
    bridge = _StackBridge(_physical(dk=4.35))
    monkeypatch.setattr(pcb_tools, "get_bridge", lambda: bridge)
    mcp = _Mcp()
    pcb_tools.register_pcb_tools(mcp)
    out = asyncio.run(mcp.tools["pcb_layer_stack_manager"]())
    assert [c for c, _ in bridge.sent] == ["pcb.layer_stack_manager", "pcb.physical_stack"]
    assert out["in_step"] is False and out["out_of_step"][0]["field"] == "dielectric_constant"
    bridge.sent.clear()
    out = asyncio.run(mcp.tools["pcb_layer_stack_manager"](compare=False))
    assert [c for c, _ in bridge.sent] == ["pcb.layer_stack_manager"] and "out_of_step" not in out


def test_an_overlay_thickness_is_not_compared():
    # live: the board gives an overlay 0.25 mil, the manager 0
    assert pcb_tools.stack_manager_out_of_step(_manager(), _physical()) == []
