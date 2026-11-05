# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Libraries made from a design, the PCB library check, pad/via
libraries, supplier links, pin package length and delay, and the
library report.

What each tool sends, what it refuses before Altium sees it, how it
reads the result back, and the Pascal shapes measured live on AD 26.11.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from eda_agent.export.library_report import render_library_report
from eda_agent.tools import library, pcb, project

ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _body(unit: str, name: str) -> str:
    text = (ALTIUM / unit).read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;", start)]


class _Bridge:
    def __init__(self, replies=None):
        self.sent = []
        self.replies = replies or {}

    async def send_command_async(self, command, params=None, **kwargs):
        self.sent.append((command, params))
        reply = self.replies.get(command, {"success": True})
        return reply(params) if callable(reply) else reply


def _tools(module, register, monkeypatch, replies=None):
    bridge = _Bridge(replies)
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    getattr(module, register)(_Mcp())
    return captured, bridge


def _lib(monkeypatch, replies=None):
    return _tools(library, "register_library_tools", monkeypatch, replies)


def _pcb(monkeypatch, replies=None):
    return _tools(pcb, "register_pcb_tools", monkeypatch, replies)


# --- the PCB library check ---------------------------------------------------

def test_pcblib_check_sends_the_checks_and_refuses_bad_input(monkeypatch, tmp_path):
    lib = tmp_path / "Parts.PcbLib"
    lib.write_bytes(b"")
    tools, bridge = _lib(monkeypatch)
    asyncio.run(tools["lib_check_pcblib"](
        library_path=str(lib), checks=["shorted_copper", "duplicate_pads"],
        footprint_name=" R0402 "))
    assert bridge.sent == [("library.check_pcblib", {
        "checks": "shorted_copper\tduplicate_pads", "library_path": str(lib),
        "footprint_name": "R0402"})]
    other = tmp_path / "Parts.SchLib"
    other.write_bytes(b"")
    for kwargs in ({"checks": ["everything"]}, {"checks": []},
                   {"library_path": str(tmp_path / "None.PcbLib")},
                   {"library_path": str(other)}):
        tools, bridge = _lib(monkeypatch)
        result = asyncio.run(tools["lib_check_pcblib"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


def test_pcblib_check_runs_every_named_check_and_puts_the_footprint_back():
    body = _body("Library.pas", "Lib_CheckPcbLib")
    for check in library._PCBLIB_CHECKS:
        assert f"#9 + '{check}' + #9" in body, check
        assert f"PcbCheckProblem('{check}'" in body, check
    # Copper on two layers never touches: the layer guard comes first.
    assert body.index("PcbCheckSpansLayers(Prim) Or PcbCheckSpansLayers(P2)") \
        < body.index("Board.PrimPrimDistance(Prim, P2)")
    finally_part = body[body.rindex("Finally"):]
    assert "PcbLib.CurrentComponent := WasCurrent" in finally_part
    # The group iterator can yield one object twice; that is not a duplicate.
    assert body.index("Seen := Seen + Key") < body.index("PcbCheckPrimKey(Prim)")


# --- pin package length and delay --------------------------------------------

def test_pin_timing_goes_as_one_list_and_refuses_bad_values(monkeypatch):
    tools, bridge = _lib(monkeypatch)
    asyncio.run(tools["lib_set_pin_timing"](
        component_name="U1", library_path="C:/x/Parts.SchLib",
        pins={"1": {"package_length_mil": 120.5, "propagation_delay_ps": 35},
              "A3": {"propagation_delay_ps": 0}}))
    assert bridge.sent == [("library.set_pin_timing", {
        "component_name": "U1", "library_path": "C:/x/Parts.SchLib",
        "pins": "1=120.5,35.0~~A3=,0.0"})]
    for pins in ({}, {"1": {}}, {"1=2": {"package_length_mil": 1}},
                 {"1": {"package_length_mil": -1}},
                 {"1": {"package_length_mil": float("nan")}},
                 {"1": {"length": 3}}, {"1": {"propagation_delay_ps": "fast"}}):
        tools, bridge = _lib(monkeypatch)
        result = asyncio.run(tools["lib_set_pin_timing"](component_name="U1", pins=pins))
        assert bridge.sent == [] and result["ok"] is False, pins


def test_pin_timing_uses_the_names_the_script_engine_declares():
    # MEASURED: ISch_Pin.PinPropagationDelay is undeclared to the script
    # engine and halts the loop; the pin's members are PinPackageLength
    # and PropagationDelay (in seconds).
    for unit in ("Library.pas",):
        text = (ALTIUM / unit).read_text(encoding="utf-8-sig")
        assert "PinPropagationDelay" not in text
    timing = _body("Library.pas", "PinTimingJson")
    assert "Pin.PinPackageLength" in timing and "Pin.PropagationDelay * 1E12" in timing
    assert "PinTimingJson(Pin)" in _body("Library.pas", "Lib_GetPinList")
    setter = _body("Library.pas", "Lib_SetPinTiming")
    assert "Pin.PropagationDelay := StrToFloatDef(DelayStr, 0) * 1E-12" in setter


# --- supplier links ------------------------------------------------------------

def test_supplier_links_go_in_order_and_come_back_grouped(monkeypatch):
    reply = {"parameters": [
        {"field": "manufacturer", "link": 1, "name": "Manufacturer 1", "value": "ST"},
        {"field": "mpn", "link": 1, "name": "Manufacturer Part Number 1", "value": "X1"},
        {"field": "supplier", "link": 2, "name": "Supplier 2", "value": "LCSC"}]}
    tools, bridge = _lib(monkeypatch, {"library.set_supplier_links": reply})
    result = asyncio.run(tools["lib_set_supplier_links"](
        component_name="U1", links=[{"manufacturer": "ST", "mpn": "X1"},
                                    {"supplier": "LCSC", "supplier_part_number": "C1"}]))
    assert bridge.sent == [("library.set_supplier_links", {
        "component_name": "U1", "links": "ST^^X1^^^^~~^^^^LCSC^^C1"})]
    assert result["links"] == [{"link": 1, "manufacturer": "ST", "mpn": "X1"},
                               {"link": 2, "supplier": "LCSC"}]
    tools, bridge = _lib(monkeypatch, {"library.set_supplier_links": reply})
    asyncio.run(tools["lib_set_supplier_links"](component_name="U1", links=[]))
    assert bridge.sent[0][1]["links"] == ""
    tools, bridge = _lib(monkeypatch, {"library.get_supplier_links": reply})
    read = asyncio.run(tools["lib_get_supplier_links"](component_name="U1"))
    assert bridge.sent == [("library.get_supplier_links", {"component_name": "U1"})]
    assert len(read["links"]) == 2


@pytest.mark.parametrize("links", [
    [{}], [{"manufacturer": ""}], [{"price": "1"}], [{"mpn": "A^^B"}],
    [{"supplier": "x~~y"}], ["ST"]])
def test_supplier_links_refuse_what_would_corrupt_the_list(monkeypatch, links):
    tools, bridge = _lib(monkeypatch)
    result = asyncio.run(tools["lib_set_supplier_links"](component_name="U1", links=links))
    assert bridge.sent == [] and result["ok"] is False


def test_supplier_link_names_are_cut_at_their_own_length():
    body = _body("Library.pas", "SupplierLinkSlot")
    for prefix in ("Manufacturer Part Number ", "Manufacturer ",
                   "Supplier Part Number ", "Supplier "):
        assert f"Pos('{prefix}', Name) = 1" in body
        assert f"Copy(Name, {len(prefix) + 1}, Length(Name))" in body
    # The longer name is tried first, or "Manufacturer Part Number 1"
    # would read as manufacturer "Part Number 1".
    assert body.index("'Manufacturer Part Number '") < body.index("'Manufacturer '")
    assert body.index("'Supplier Part Number '") < body.index("'Supplier '")
    # Removed by the symbol that owns them, collected first.
    setter = _body("Library.pas", "Lib_SetSupplierLinks")
    assert setter.index("Victims.Add(Param)") < setter.index("Comp.RemoveSchObject(Param)")


# --- libraries made from a design --------------------------------------------

def test_pcb_library_from_a_board_is_saved_closed_and_tidied(monkeypatch, tmp_path):
    made = tmp_path / "Board1.PcbLib"
    made.write_bytes(b"")
    out = tmp_path / "Parts.PcbLib"

    def make(params):
        out.write_bytes(b"lib")
        return {"library": str(out), "made_as": str(made), "saved": True,
                "footprint_count": 2}
    tools, bridge = _pcb(monkeypatch, {
        "pcb.make_library": make,
        "application.close_document": {"closed": True}})
    result = asyncio.run(tools["pcb_make_library"](
        out_path=str(out), add_to_project=True, board_path="C:/b.PcbDoc"))
    assert bridge.sent[0] == ("pcb.make_library", {
        "add_to_project": "true", "out_path": str(out), "board_path": "C:/b.PcbDoc"})
    assert bridge.sent[1] == ("application.close_document", {"file_path": str(out)})
    assert result["closed"] is True and result["bytes"] == 3
    assert not made.exists() and result["removed_empty_file"] == str(made)
    for kwargs in ({"out_path": "Parts.PcbLib"}, {"out_path": str(out)},
                   {"out_path": str(tmp_path / "x.SchLib")}):
        tools, bridge = _pcb(monkeypatch)
        result = asyncio.run(tools["pcb_make_library"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


def test_a_library_altium_made_with_content_is_never_removed(monkeypatch, tmp_path):
    made = tmp_path / "Board1.PcbLib"
    made.write_bytes(b"not empty")
    out = tmp_path / "Parts.PcbLib"
    tools, _ = _pcb(monkeypatch, {
        "pcb.make_library": {"library": str(out), "made_as": str(made), "saved": False}})
    result = asyncio.run(tools["pcb_make_library"](out_path=str(out)))
    assert made.exists() and "removed_empty_file" not in result


def test_integrated_library_is_built_only_when_a_new_file_appears(monkeypatch, tmp_path):
    prj = tmp_path / "Board.PrjPcb"
    intlib = tmp_path / "Board.IntLib"
    intlib.write_bytes(b"old")
    os.utime(intlib, (1, 1))

    def run(params):
        intlib.write_bytes(b"new library")
        return {"project": str(prj), "focused_document": "x"}
    tools, bridge = _tools(project, "register_project_tools", monkeypatch,
                           {"project.make_integrated_library": run})
    result = asyncio.run(tools["proj_make_integrated_library"](project_path=str(prj)))
    assert result["built"] is True and result["intlib"] == str(intlib)
    assert result["bytes"] == len(b"new library")
    assert bridge.sent == [("project.make_integrated_library",
                            {"project_path": str(prj)})]


def test_integrated_library_unchanged_is_not_built(monkeypatch, tmp_path):
    prj = tmp_path / "Board.PrjPcb"
    tools, _ = _tools(project, "register_project_tools", monkeypatch, {
        "project.make_integrated_library": {"project": str(prj)}})
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    result = asyncio.run(tools["proj_make_integrated_library"](project_path=str(prj)))
    assert result["built"] is False and "Messages" in result["reason"]


async def _no_sleep(_seconds):
    return None


def test_make_library_handlers_run_altiums_own_commands():
    assert "RunProcess('PCB:BuildPCBLibrary');" in _body("PCB.pas", "PCB_MakeLibrary")
    assert "AddStringParameter('AddLibraryToProject', 'False');" in \
        _body("PCB.pas", "PCB_MakeLibrary")
    body = _body("Project.pas", "Proj_MakeIntegratedLibrary")
    assert body.index("ServerDoc.Focus") < \
        body.index("RunProcess('IntegratedLibrary:CreateLibraryFromProject');")
    assert "'NOT_FOCUSED'" in body


# --- pad/via libraries --------------------------------------------------------

def test_pad_via_template_takes_a_pad_or_a_via(monkeypatch):
    tools, bridge = _pcb(monkeypatch)
    asyncio.run(tools["pcb_add_pad_via_template"](component="U1", pad="1"))
    asyncio.run(tools["pcb_add_pad_via_template"](x=100, y=-50.5))
    assert bridge.sent == [
        ("pcb.add_pad_via_template", {"component": "U1", "pad": "1"}),
        ("pcb.add_pad_via_template", {"x": "100.0", "y": "-50.5"})]
    for kwargs in ({}, {"component": "U1"}, {"x": 1},
                   {"component": "U1", "pad": "1", "x": 1, "y": 2}):
        tools, bridge = _pcb(monkeypatch)
        result = asyncio.run(tools["pcb_add_pad_via_template"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


def test_pad_via_library_install_is_read_back_from_the_list(monkeypatch, tmp_path):
    out = tmp_path / "Pads.PvLib"
    listings = iter([[], [], [{"path": str(out)}]])
    tools, bridge = _pcb(monkeypatch, {
        "pcb.make_pad_via_library": {"board": "C:/b.PcbDoc", "saved": True, "copied": 2},
        "application.close_document": {"closed": True},
        "pcb.get_pad_via_library": lambda p: {"installed_libraries": next(listings)}})
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    result = asyncio.run(tools["pcb_make_pad_via_library"](out_path=str(out), install=True))
    assert result["installed"] is True and result["closed"] is True
    commands = [c for c, _ in bridge.sent]
    assert commands[:2] == ["pcb.make_pad_via_library", "application.close_document"]
    assert commands.count("library.install_library") == 3
    for kwargs in ({"out_path": "Pads.PvLib"}, {"out_path": str(tmp_path / "x.PcbLib")}):
        tools, bridge = _pcb(monkeypatch)
        result = asyncio.run(tools["pcb_make_pad_via_library"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs


def test_pad_via_library_install_that_never_lists_is_reported(monkeypatch, tmp_path):
    out = tmp_path / "Pads.PvLib"
    tools, bridge = _pcb(monkeypatch, {
        "pcb.make_pad_via_library": {"saved": True},
        "pcb.get_pad_via_library": {"installed_libraries": []}})
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    result = asyncio.run(tools["pcb_make_pad_via_library"](out_path=str(out), install=True))
    assert result["installed"] is False


def test_pad_via_calls_are_the_measured_ones():
    # MEASURED: AddTemplate with a template adds nothing to another
    # library; AddExternalTemplate copies it and the copy is saved.
    maker = _body("PCB.pas", "PCB_MakePadViaLibrary")
    assert "DestLib.AddExternalTemplate(T)" in maker
    assert "DestLib.AddTemplate(" not in maker
    assert "PvLib.AddTemplateByPrimitive(Found)" in _body("PCB.pas", "PCB_AddPadViaTemplate")
    for name in ("Lib_InstallLibrary", "Lib_UninstallLibrary"):
        body = _body("Library.pas", name)
        assert "UpperCase(ExtractFileExt(Path)) = '.PVLIB'" in body, name
        assert "PCBServer.GetState_PadViaLibraryManager" in body, name


# --- the library report -------------------------------------------------------

def test_library_report_page_escapes_and_names_what_was_not_read():
    page = render_library_report("C:/x/<Parts>.SchLib", "schlib", [
        {"name": "U<1>", "description": "a & b", "designator": "U?",
         "part_count": 2, "pin_count": 8,
         "models": [{"model_type": "PCBLIB", "model_name": "SO8"}],
         "parameters": {"Value": "<1k>"}}], unread=["Q&A"])
    assert "U&lt;1&gt;" in page and "a &amp; b" in page and "&lt;1k&gt;" in page
    assert "PCBLIB: SO8" in page and "Not read: Q&amp;A" in page
    assert "<p>1 symbols</p>" in page
    pads = render_library_report("C:/x/P.PcbLib", "pcblib", [
        {"name": "R0402", "description": "", "pad_count": 2, "pad_names": ["1", "2"]}])
    assert "<td>1, 2</td>" in pads and "Not read" not in pads
    with pytest.raises(ValueError):
        render_library_report("x", "intlib", [])


def test_library_report_reads_each_entry_and_lists_failures(monkeypatch, tmp_path):
    lib = tmp_path / "Parts.SchLib"
    lib.write_bytes(b"")

    def details(params):
        if params["component_name"] == "BAD":
            return {"success": False}
        return {"pins": [], "pin_count": 2, "description": "d",
                "designator": {"text": "R?"}, "part_count": 1,
                "parameters": {"Value": "1k"}, "models": []}
    tools, bridge = _lib(monkeypatch, {
        "library.get_components": {"components": [{"name": "RES"}, {"name": "BAD"}]},
        "library.get_component_details": details})
    result = asyncio.run(tools["lib_library_report"](library_path=str(lib)))
    page = Path(result["report"]).read_text(encoding="utf-8")
    assert result["entries"] == 1 and result["unread"] == ["BAD"]
    assert Path(result["report"]).name == "Parts Library Report.html"
    assert "<td>R?</td>" in page and "Value = 1k" in page
    for kwargs in ({"library_path": str(tmp_path / "x.IntLib")},
                   {"library_path": str(tmp_path / "None.SchLib")},
                   {"library_path": str(lib), "out_path": str(tmp_path / "r.txt")}):
        tools, bridge = _lib(monkeypatch)
        result = asyncio.run(tools["lib_library_report"](**kwargs))
        assert bridge.sent == [] and result["ok"] is False, kwargs
