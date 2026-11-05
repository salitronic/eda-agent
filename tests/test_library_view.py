# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The Library page: the design's parts grouped by what they are, traced
to the library their footprint comes from, with what is wrong with them."""
from __future__ import annotations

import threading

from eda_agent.web import library_view as lv


def _fp(lib, found_at="", integrated=False):
    return [{"name": "X", "type": "PCBLIB", "current": True, "integrated": integrated,
             "datafiles": [{"library": lib, "entity": "X", "kind": "PCBLIB",
                            "found_at": found_at}]}]


BOM = [
    {"designator": "R10", "comment": "10k", "lib_ref": "RES", "footprint": "0402"},
    {"designator": "R2", "comment": "10k", "lib_ref": "RES", "footprint": "0402"},
    {"designator": "R3", "comment": "10k", "lib_ref": "RES", "footprint": "0603"},
    {"designator": "R4", "comment": "10k", "lib_ref": "RES_ALT", "footprint": "0402"},
    {"designator": "U1", "comment": "LM358", "lib_ref": "OPAMP", "footprint": "SOIC8"},
    {"designator": "J1", "comment": "HDR", "lib_ref": "HDR", "footprint": ""},
]
MODELS = {"R10": _fp("Passives.PcbLib", "C:/lib/Passives.PcbLib"),
          "R2": _fp("Passives.PcbLib", "C:/lib/Passives.PcbLib"),
          "R3": _fp("Passives.PcbLib", "C:/lib/Passives.PcbLib"),
          "R4": _fp("Passives.PcbLib", "C:/lib/Passives.PcbLib"),
          "U1": _fp("Gone.PcbLib")}
PARAMS = {"R10": {"Manufacturer Part Number": "RC0402-10K", "Datasheet": "https://x/r.pdf",
                  "Library Name": "Passives.SchLib"},
          "U1": {"MPN": "LM358DR", "LCSC": "C7950", "Manufacturer": "TI",
                 "Datasheet": "*"}}


def test_parts_group_into_bom_lines_with_their_source():
    out = lv.build(BOM, MODELS, PARAMS)
    by = {tuple(r["designators"]): r for r in out["rows"]}
    r = by[("R2", "R10")]                                 # natural order
    assert r["qty"] == 2 and r["library_file"] == "C:/lib/Passives.PcbLib" and r["found"]
    # Parameters are read from the first part of the line, whatever their names.
    u = by[("U1",)]
    assert (u["mpn"], u["maker"], u["supplier"], u["datasheet"]) == ("LM358DR", "TI", "C7950", "")
    assert out["parts"] == 6 and out["kinds"] == 5
    assert r["source"] == "Passives.PcbLib" and by[("J1",)]["source"] == "(no library)"
    assert r["symbol_library"] == "Passives.SchLib"


def test_each_line_says_what_is_wrong():
    out = lv.build(BOM, MODELS, PARAMS)
    issues = {r["designators"][0]: [i["text"] for i in r["issues"]] for r in out["rows"]}
    assert "Altium does not find X in Gone.PcbLib" in issues["U1"]
    assert "no datasheet link" in issues["U1"]
    assert issues["J1"][0] == "no footprint"
    assert "RES is used with 2 footprints" in issues["R3"]
    assert any("could be one" in t for t in issues["R4"])
    # Errors first.
    assert out["rows"][0]["worst"] == 0
    assert out["counts"]["error"] == 2
    lib = {l["name"]: l for l in out["libraries"]}
    assert lib["Passives.PcbLib"]["parts"] == 4 and lib["Gone.PcbLib"]["missing"] == 1


def test_the_read_runs_in_the_background_and_keeps_its_result():
    sent = []

    def send(cmd, params, timeout):
        sent.append(cmd)
        if cmd == "project.get_bom":
            return {"components": BOM}
        if cmd == "project.component_models":
            return {"parts": [{"designator": d, "models": m} for d, m in MODELS.items()]}
        return {"components": [{"designator": d, "parameters": p} for d, p in PARAMS.items()]}

    view = lv.LibraryView(send=send)
    assert view.read("A.PcbDoc")["ok"]
    for _ in range(200):
        if view.snapshot()["state"] != "reading":
            break
        threading.Event().wait(0.01)
    snap = view.snapshot()
    assert snap["state"] == "ready" and snap["kinds"] == 5 and snap["models_read"]
    assert sent == ["project.get_bom", "project.component_models",
                    "project.get_component_info_batch"]


def test_a_project_with_no_parts_says_so():
    view = lv.LibraryView(send=lambda c, p, t: {"components": []})
    view._read("")
    assert view.snapshot()["state"] == "failed" and "no parts" in view.snapshot()["error"]


def test_a_footprint_inside_an_intlib_is_named_by_its_intlib():
    at = "SELibrary_CAPACITORS:" + chr(92) + "PCBLib" + chr(92) + "0.pcblib"
    src = lv._footprint_source(_fp("SELibrary_CAPACITORS.PcbLib", at))
    assert src["source"] == "SELibrary_CAPACITORS.IntLib" and src["integrated"] and src["found"]
    # An IntLib model names no separate library, and is not a missing link.
    src = lv._footprint_source([{"type": "PCBLIB", "current": True, "datafiles": [
        {"library": "", "entity": "X", "found_at": at}]}])
    assert src["linked"] and src["found"]
    # A drive letter is not an IntLib.
    assert lv._footprint_source(_fp("A.PcbLib", "C:/lib/A.PcbLib"))["source"] == "A.PcbLib"
