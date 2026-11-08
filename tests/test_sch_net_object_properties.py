# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""Net labels, power ports, ports and off-sheet connectors: what was missing.

MEASURED 2026-11-07 on AD 25.8.1, on a scratch sheet:
- obj_query Text on a round rectangle raised "Undeclared identifier: Text"
  and the polling loop stopped: SchObjectHasText left out the round
  rectangle, ellipse, polyline, polygon, arcs and bezier (none has Text).
- sch_place_cross_sheet_connector with side raised "Undeclared identifier:
  Side" and the loop stopped: an off-sheet connector's left / right is
  CrossSheetStyle (Side is a sheet entry's member).
- ShowNetName (power port, off-sheet connector, port), the connector's
  CrossSheetStyle and a port's Height had no route: refused as unknown,
  although Altium saves all three.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _source() -> str:
    return (SCRIPTS / "Generic.pas").read_text(encoding="utf-8", errors="replace")


def _routine(code: str, name: str) -> str:
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    return code[start.start():start.start() + end.end()]


def test_shapes_without_text_refuse_it():
    body = _routine(_source(), "SchObjectHasText")
    denied = set(re.findall(r"Obj\.ObjectId = (e\w+)", body))
    assert {"eRoundRectangle", "eEllipse", "ePolyline", "ePolygon", "eArc",
            "eEllipticalArc", "eBezier"} <= denied
    # Notes, text frames and labels do have Text.
    assert not denied & {"eNote", "eTextFrame", "eLabel", "eNetLabel", "ePowerObject"}


def test_the_off_sheet_placer_writes_cross_sheet_style_not_side():
    body = _routine(_source(), "Gen_PlaceCrossSheetConnector")
    assert not re.search(r"\.Side\s*:=", body)
    assert "CSC : ISch_CrossSheetConnector;" in body
    assert body.index("CSC := Conn;") < body.index("CSC.CrossSheetStyle := eCrossSheetLeft")
    assert "CSC.CrossSheetStyle := eCrossSheetRight" in body


def test_new_extras_belong_to_the_types_that_carry_them():
    code = _source()
    owner = _routine(code, "SchExtraOwner")
    assert "Else If PropName = 'ShowNetName' Then Result := 8" in owner
    assert "Else If PropName = 'CrossSheetStyle' Then Result := 9" in owner
    assert "Else If PropName = 'Height' Then Result := 10" in owner
    fits = _routine(code, "SchExtraFits")
    eight = fits.split("(Owner = 8)", 1)[1].split("(Owner = 9)", 1)[0]
    assert set(re.findall(r"Obj\.ObjectId = (e\w+)", eight)) == {
        "ePowerObject", "eCrossSheetConnector", "ePort"}
    assert "((Owner = 9) And (Obj.ObjectId = eCrossSheetConnector))" in fits
    assert "((Owner = 10) And (Obj.ObjectId = ePort))" in fits
    assert "(PropName = 'ShowNetName')" in _routine(code, "SchExtraIsBool")
    assert "(PropName = 'CrossSheetStyle')" in _routine(code, "SchExtraIsInt")
    assert "(PropName = 'Height')" in _routine(code, "SchExtraIsMils")


def test_new_extras_go_through_typed_interfaces():
    code = _source()
    for name in ("SchGetExtra", "SchSetExtra"):
        body = _routine(code, name)
        assert "PowerObj : ISch_PowerObject;" in body
        assert "CSC : ISch_CrossSheetConnector;" in body
        power = body.split("Else If Obj.ObjectId = ePowerObject Then", 1)[1]
        power = power.split("Else If Obj.ObjectId = eCrossSheetConnector Then", 1)[0]
        assert power.index("PowerObj := Obj") < power.index("PowerObj.ShowNetName")
        conn = body.split("Else If Obj.ObjectId = eCrossSheetConnector Then", 1)[1]
        assert conn.index("CSC := Obj") < conn.index("CSC.ShowNetName") < conn.index("CSC.CrossSheetStyle")
        assert "Port.ShowNetName" in body and "Port.Height" in body
        assert not re.search(r"\bObj\.(ShowNetName|CrossSheetStyle|Height)\b", body)
        # IsCustomStyle is listed on ISch_Port, but writing it stopped the
        # polling loop on AD 25.8.1: it must stay out.
        assert "IsCustomStyle" not in body
    assert "FloatToJsonStr(CoordToMilsF(Port.Height))" in _routine(code, "SchGetExtra")
    setter = _routine(code, "SchSetExtra")
    assert "Port.Height := MilsToCoordF(Mils)" in setter
    # Left (0) or right (1) only, refused before anything is written.
    assert setter.index("If (PropName = 'CrossSheetStyle') And (N > 1) Then Exit;") < setter.index("CSC := Obj")
