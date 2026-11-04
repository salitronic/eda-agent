# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The KiCad schematic and library tools on a small project in a temp folder.

No KiCad needed: the tools work on the files. Library nicknames resolve
from the project's own table; the global table is pointed at an empty
folder so the user's KiCad settings never take part.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

LIB = """(kicad_symbol_lib
\t(version 20241209)
\t(generator "test")
\t(symbol "R"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(property "Reference" "R"
\t\t\t(at 2.032 0 90)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "R"
\t\t\t(at 0 0 90)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at 0 0 0)
\t\t\t(hide yes)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "ki_keywords" "res"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(symbol "R_1_1"
\t\t\t(pin passive line
\t\t\t\t(at 0 3.81 270)
\t\t\t\t(length 1.27)
\t\t\t\t(name "~" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27))))
\t\t\t)
\t\t\t(pin passive line
\t\t\t\t(at 0 -3.81 90)
\t\t\t\t(length 1.27)
\t\t\t\t(name "~" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "2" (effects (font (size 1.27 1.27))))
\t\t\t)
\t\t)
\t)
\t(symbol "GND"
\t\t(power)
\t\t(property "Reference" "#PWR"
\t\t\t(at 0 -6.35 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Value" "GND"
\t\t\t(at 0 -3.81 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(symbol "GND_1_1"
\t\t\t(pin power_in line
\t\t\t\t(at 0 0 270)
\t\t\t\t(length 0)
\t\t\t\t(name "~" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27))))
\t\t\t)
\t\t)
\t)
)
"""

SCH = """(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(uuid "11111111-1111-1111-1111-111111111111")
\t(paper "A4")
\t(lib_symbols
\t)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "demo.kicad_pro").write_text("{}", encoding="utf-8")
    (tmp_path / "demo.kicad_sch").write_text(SCH, encoding="utf-8")
    (tmp_path / "my.kicad_sym").write_text(LIB, encoding="utf-8")
    (tmp_path / "sym-lib-table").write_text(
        '(sym_lib_table\n\t(version 7)\n\t(lib (name "my") (type "KiCad") '
        '(uri "${KIPRJMOD}/my.kicad_sym") (options "") (descr ""))\n)\n', encoding="utf-8")
    settings = tmp_path / "settings"
    settings.mkdir()
    monkeypatch.setenv("APPDATA", str(settings))
    import eda_agent.tools.kicad_lib as kl
    import eda_agent.tools.kicad_sch as ks
    monkeypatch.setattr(ks, "_cli", lambda: None)
    # A running KiCad must never be the project a test writes to.
    monkeypatch.setattr(ks, "_root_schematic", lambda: str(tmp_path / "demo.kicad_sch"))
    monkeypatch.setattr(kl, "_cli", lambda: None)
    monkeypatch.setattr(kl, "_project_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture(scope="module")
def mcp():
    from eda_agent.tools.kicad_lib import register_kicad_lib_tools
    from eda_agent.tools.kicad_sch import register_kicad_sch_tools
    m = FastMCP("t")
    register_kicad_sch_tools(m)
    register_kicad_lib_tools(m)
    return m


def _call(mcp, tool, **kwargs):
    r = asyncio.run(mcp.call_tool(tool, kwargs))
    if isinstance(r, tuple):
        r = r[0]
    return json.loads(r[0].text)


def test_placed_symbol_pins_land_where_wires_can_end(mcp, project):
    sheet = str(project / "demo.kicad_sch")
    r = _call(mcp, "kicad_sch_place_symbols", sheet=sheet, items=[
        {"lib_id": "my:R", "x_mm": 50.8, "y_mm": 50.8, "value": "10k"},
        {"lib_id": "my:R", "x_mm": 76.2, "y_mm": 50.8, "angle_deg": 90}])
    assert r["ok"] and "reopen" in r["note"]
    first, turned = r["items"]
    assert first["reference"] == "R1" and turned["reference"] == "R2"
    pins = {p["number"]: p["position_mm"] for p in first["pins"]}
    assert pins == {"1": [50.8, 46.99], "2": [50.8, 54.61]}
    turned_pins = sorted(p["position_mm"] for p in turned["pins"])
    assert turned_pins == [[72.39, 50.8], [80.01, 50.8]]
    text = (project / "demo.kicad_sch").read_text(encoding="utf-8")
    assert text.count('(symbol "my:R"') == 1, "the library symbol is embedded once"
    assert "ki_keywords" not in text.split("(lib_symbols")[1].split("\n\t)\n")[1]


def test_power_wires_labels_and_annotation(mcp, project):
    sheet = str(project / "demo.kicad_sch")
    _call(mcp, "kicad_sch_place_symbols", sheet=sheet, lib_id="my:R",
          items=[{"reference": "R?", "x_mm": 10.16, "y_mm": 10.16}])
    p = _call(mcp, "kicad_sch_place_power", sheet=sheet, items=[
        {"name": "GND", "lib_id": "my:GND", "x_mm": 10.16, "y_mm": 16.51}])
    assert p["ok"] and p["items"][0]["reference"].startswith("#PWR")
    w = _call(mcp, "kicad_sch_place_wires", sheet=sheet, items=[
        {"points": [[10.16, 13.97], [10.16, 16.51]]},
        {"points": [[0, 20], [20, 20]]}, {"points": [[10, 20], [10, 30]]}])
    assert w["ok"] and w["items"][2]["junctions_added"] == [[10.0, 20.0]]
    lab = _call(mcp, "kicad_sch_place_labels", sheet=sheet, kind="global",
                items=[{"text": "SDA", "x_mm": 0, "y_mm": 20, "shape": "input"}])
    assert lab["ok"]
    ann = _call(mcp, "kicad_sch_annotate")
    assert ann["renames"] == [{"from": "R?", "to": "R1", "file": "demo.kicad_sch"}]
    items = _call(mcp, "kicad_sch_get_items", sheet=sheet)
    assert [s["reference"] for s in items["symbols"] if not s["power"]] == ["R1"]
    assert items["labels"][0]["kind"] == "global" and len(items["wires"]) == 3
    assert len(items["junctions"]) == 1


def test_modify_checks_the_change_and_delete_removes(mcp, project):
    sheet = str(project / "demo.kicad_sch")
    _call(mcp, "kicad_sch_place_symbols", sheet=sheet, lib_id="my:R",
          items=[{"x_mm": 20, "y_mm": 20}])
    r = _call(mcp, "kicad_sch_modify_symbols", sheet=sheet, items=[
        {"reference": "R1", "value": "4k7", "x_mm": 25.4, "mirror": "y", "dnp": True,
         "properties": {"MPN": "X1"}, "new_reference": "R9"}])
    item = r["items"][0]
    assert r["ok"] and item["reference"] == "R9" and item["value"] == "4k7"
    assert item["position_mm"] == [25.4, 20.0] and item["mirror"] == "y" and item["dnp"]
    gone = _call(mcp, "kicad_sch_delete_items", sheet=sheet, references=["R9"])
    assert gone["ok"]
    assert not _call(mcp, "kicad_sch_get_items", sheet=sheet, kinds=["symbols"])["symbols"]


def test_page_and_title_block(mcp, project):
    sheet = str(project / "demo.kicad_sch")
    r = _call(mcp, "kicad_sch_set_page", sheet=sheet, paper="A3", title="T", revision="B",
              comments={"1": "c"})
    assert r["paper"] == "A3" and r["title_block"] == {"title": "T", "rev": "B", "comment1": "c"}


def test_unknown_library_is_named(mcp, project):
    r = _call(mcp, "kicad_sch_place_symbols", sheet=str(project / "demo.kicad_sch"),
              items=[{"lib_id": "nope:R", "x_mm": 0, "y_mm": 0}])
    assert r["ok"] is False and "nope" in r["items"][0]["reason"]


def test_symbol_library_edits(mcp, project):
    lib = str(project / "my.kicad_sym")
    assert _call(mcp, "kicad_lib_copy", library="my", name="R", new_name="R2")["ok"]
    assert _call(mcp, "kicad_lib_rename", library=lib, name="R2", new_name="R3")["ok"]
    props = _call(mcp, "kicad_lib_set_symbol_properties", library=lib, items=[
        {"name": "R3", "properties": {"MPN": "RC0603", "Footprint": "R:0603"}}])
    assert props["ok"] and props["items"][0]["properties"]["MPN"] == "RC0603"
    pins = _call(mcp, "kicad_lib_add_pins", library=lib, name="R3", pins=[
        {"number": "3", "name": "TAP", "type": "input", "x_mm": -2.54, "y_mm": 0}])
    assert pins["ok"] and pins["pin_count"] == 3
    dup = _call(mcp, "kicad_lib_add_pins", library=lib, name="R3", pins=[{"number": "1"}])
    assert dup["ok"] is False and "already exists" in dup["reason"]
    sym = _call(mcp, "kicad_lib_get_symbol", library=lib, name="R3")
    assert {p["number"] for p in sym["pins"]} == {"1", "2", "3"}
    assert _call(mcp, "kicad_lib_delete", library=lib, names=["R3"])["ok"]
    names = [i["name"] for i in _call(mcp, "kicad_lib_list_items", library=lib)["items"]]
    assert names == ["R", "GND"]


def test_footprint_library_edits_and_check(mcp, project):
    pretty = project / "fp.pretty"
    pretty.mkdir()
    (pretty / "P.kicad_mod").write_text(
        '(footprint "P"\n\t(version 20241229)\n\t(layer "F.Cu")\n'
        '\t(property "Reference" "REF**"\n\t\t(at 0 -1 0)\n\t\t(layer "F.SilkS")\n'
        '\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t)\n)\n', encoding="utf-8")
    r = _call(mcp, "kicad_lib_add_footprint_items", library=str(pretty), name="P", items=[
        {"kind": "pad", "number": "1", "position_mm": [-1, 0], "size_mm": [1, 1]},
        {"kind": "pad", "number": "2", "type": "pth", "shape": "circle",
         "position_mm": [1, 0], "size_mm": [1.6, 1.6], "drill_mm": 0.8},
        {"kind": "rect", "points": [[-2, -1], [2, 1]], "layer": "F.CrtYd", "width_mm": 0.05}])
    assert r["ok"] and r["pad_count"] == 2
    fp = _call(mcp, "kicad_lib_get_footprint", library=str(pretty), name="P")
    assert fp["pads"][1]["drill_mm"] == [0.8]
    props = _call(mcp, "kicad_lib_set_footprint_properties", library=str(pretty), name="P",
                  description="d", mounting="smd", add_models=[{"path": "m.step"}])
    assert props["attributes"] == ["smd"] and props["models"] == ["m.step"]
    check = _call(mcp, "kicad_lib_check", library=str(pretty), kind="footprint")
    found = {f["finding"] for f in check["findings"]}
    assert "no F.Fab drawing" in found and "no courtyard" not in found
