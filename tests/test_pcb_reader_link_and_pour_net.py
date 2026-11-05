# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""obj_query reads a board part's schematic link and a poured region's net.

A placed part's link to its symbol could not be read, so a part could not
be replaced with its multi-channel link intact, and poured copper read as
netless because its net lives on the pour that made it.
"""
from __future__ import annotations

from pathlib import Path

_PAS = (Path(__file__).resolve().parents[1] / "scripts" / "altium"
        / "PCBGeneric.pas").read_text(encoding="utf-8")


def test_the_link_fields_are_read_and_listed():
    for field in ("SourceUniqueId", "SourceHierarchicalPath",
                  "SourceLibReference", "SourceFootprintLibrary",
                  "SourceComponentLibrary"):
        assert f"Result := Comp.{field};" in _PAS, field
        assert f"(PropName = '{field}')" in _PAS, field
        assert _PAS.count(field) >= 3, f"{field} missing from the known list"


def test_poured_copper_reads_its_pour_net():
    start = _PAS.index("Else If (PropName = 'Net') Or (PropName = 'Net.Name') Then")
    branch = _PAS[start:_PAS.index("Else If PropName = 'InComponent'", start)]
    assert "If Obj.InPolygon Then" in branch
    assert "Result := Obj.Polygon.Net.Name;" in branch


def test_an_arc_can_be_written_and_reads_exactly():
    """An arc's centre, radius and width read and could not be written, so
    a placed part's pin-1 dot could not be put right; and they read in
    whole mils, so a 2.46 mil radius read as 2."""
    start = _PAS.index("Function SetPCBProperty(")
    body = _PAS[start:_PAS.index("Function FilterValueMatchesPCB(", start)]
    for write in ("Arc.XCenter := ", "Arc.YCenter := ", "Arc.Radius := ",
                  "Arc.LineWidth := ", "Arc.StartAngle := ", "Arc.EndAngle := "):
        assert write in body, write
    for read in ("XCenter", "YCenter", "Radius"):
        assert f"Result := FloatToJsonStr(CoordToMilsF(Arc.{read}));" in _PAS, read
