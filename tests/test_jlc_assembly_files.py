# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""JLCPCB's BOM and CPL are written from the board, not by hand.

A take read proj_get_bom and pcb_export_coordinates and wrote both CSVs
with Write, the rotation and origin conventions unchecked.
"""

from __future__ import annotations

import asyncio
import csv

from eda_agent.tools import project


class _B:
    async def send_command_async(self, command, params=None, timeout=None):
        if command == "pcb.export_coordinates":
            return {"board_name": "RoundLED.PcbDoc", "placements": [
                {"designator": "C1", "footprint": "C0402", "comment": "100nF",
                 "x": 5100, "y": 5000, "rotation": 90.0, "side": "Bottom"},
                {"designator": "C2", "footprint": "C0402", "comment": "100nF",
                 "x": 5200, "y": 5000, "rotation": 450.0, "side": "Top"},
                {"designator": "U1", "footprint": "SOT23-5", "comment": "LDO",
                 "x": 5000, "y": 5100, "rotation": 0.0, "side": "Top"}]}
        if command == "project.get_board_info":
            return {"origin_x": 5000, "origin_y": 5000}
        if command == "project.get_component_info_batch":
            return {"components": [
                {"designator": "C1", "parameters": {"LCSC": "C307331"}},
                {"designator": "C2", "parameters": {"lcsc": "C307331"}},
                {"designator": "U1", "parameters": {"LCSC": "*"}}]}
        raise AssertionError(command)


def test_both_files_come_from_the_board(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "get_bridge", lambda: _B())
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    project.register_project_tools(_Mcp())
    out = asyncio.run(tools["proj_export_jlc_assembly"](str(tmp_path)))
    assert out["without_lcsc"] == ["U1"] and out["bom_rows"] == 2
    bom = list(csv.reader(open(out["bom_path"], encoding="utf-8")))
    assert bom[0] == ["Comment", "Designator", "Footprint", "LCSC Part #"]
    assert ["100nF", "C1,C2", "C0402", "C307331"] in bom
    cpl = list(csv.reader(open(out["cpl_path"], encoding="utf-8")))
    assert cpl[0] == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]
    assert cpl[1] == ["C1", "2.5400mm", "0.0000mm", "Bottom", "90"]
    assert cpl[2][4] == "90", "rotation kept within a turn"
    assert out["cpl_path"].endswith("RoundLED_CPL_JLCPCB.csv")
