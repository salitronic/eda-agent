# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A copied OutJob plots the board's own copper layers, planes included.

Altium's shipped Fabrication.OutJob plots top, mid 1, mid 2 and bottom
(16777217..16777219, 16842751) and no internal plane. A demo board with
two planes came out without their Gerbers, and the agent edited the
OutJob file by hand on camera to add them.
"""
from __future__ import annotations

from eda_agent import outjob

# The Gerber line of the shipped template, as measured (AD 26.11).
_PLOT = ("Plot.Set=SerializeLayerHash.Version~2,ClassName~TLayerToBoolean,"
         "16973830~1,16973832~1,16973834~1,16777217~1,16777218~1,16777219~1,"
         "16842751~1,16973835~1,16973833~1,16973831~1,16908289~1,16908295~1,"
         "16908301~1,16908303~1,16908304~1,16973837~1,16973848~1,16973849~1")
_SRC = ("[OutputGroup1]\r\nOutputType1=Gerber\r\nOutputDocumentPath1=Old.PcbDoc\r\n"
        "Configuration1_Item1=AddToAllPlots.Set=x|" + _PLOT + "|PlotPositivePlaneLayers=False\r\n"
        "OutputType2=NC Drill\r\nConfiguration2_Item1=BoardEdgeRoutToolDia=2000000\r\n"
        ).encode("utf-8")


def _plotted(text):
    s = text[text.index("ClassName~TLayerToBoolean,") + len("ClassName~TLayerToBoolean,"):]
    s = s.split("|", 1)[0].split("\r\n", 1)[0]
    return [int(e.split("~")[0]) for e in s.split(",")]


def test_layer_ids_map_to_outjob_codes():
    # top, mid 1, plane 1, plane 2, bottom; a dielectric (0) dropped.
    assert outjob.copper_layer_codes([1, 0, 2, 39, 40, 32]) == [
        16777217, 16777218, 16842753, 16842754, 16842751]


def test_a_board_with_two_planes_plots_them():
    codes = outjob.copper_layer_codes([1, 39, 40, 32])
    data, report = outjob.clone(_SRC, r"C:\N\N.PrjPcb", [r"C:\N\N.PcbDoc"], r"C:\N\Out", codes)
    plotted = _plotted(data.decode("utf-8"))
    assert [c for c in plotted if outjob._is_copper_code(c)] == [
        16777217, 16842753, 16842754, 16842751]
    # The masks, pastes, overlays and mechanical layers stay as they were.
    assert plotted[:3] == [16973830, 16973832, 16973834]
    assert plotted[-3:] == [16973837, 16973848, 16973849]
    assert report["copper_layer_sets"] == 1


def test_without_the_board_the_template_is_kept():
    data, report = outjob.clone(_SRC, r"C:\N\N.PrjPcb", [r"C:\N\N.PcbDoc"], r"C:\N\Out")
    assert _PLOT in data.decode("utf-8")
    assert report["copper_layer_sets"] == 0


def test_the_stackup_reports_layer_ids():
    from pathlib import Path
    pas = (Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas").read_text(encoding="utf-8")
    body = pas[pas.index("Function PCB_GetLayerStackup"):]
    body = body[:body.index("\nFunction ", 10)]
    assert '"layer_id":' in body and "LayerObj.LayerID" in body
