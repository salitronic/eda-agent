# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_export_step and pcb_repour_polygons report what happened.

Both answered success whatever happened. proj_export_step returned
success:true without looking for the file, and from a schematic tab the
export went to a view that was not the board. pcb_repour_polygons ran a
process name nothing documents and returned repoured:true, while a pour
past a corrected edge clearance stayed as it was. pcb_get_polygons gave
the outline's area as the copper's, so a repour could not be seen in it.

These handlers need Altium to run, so the shape of what they decide on
is checked here, with comments stripped so an explanation cannot pass
for the code. Release verification step 26 runs them live.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _routine(unit: str, name: str) -> str:
    code = (SCRIPTS / unit).read_text(encoding="utf-8", errors="replace")
    start = re.search(rf"(?mi)^(?:Function|Procedure) {name}\b", code)
    assert start, f"missing routine {name}"
    end = re.search(r"(?m)^End;", code[start.start():])
    body = code[start.start():start.start() + end.end()]
    return re.sub(r"'(?:(?:'')|[^'])*'|\{[^}]*\}|//[^\n]*",
                  lambda m: m[0] if m[0].startswith("'") else " ", body)


def test_step_export_claims_a_file_only_when_a_new_one_is_there():
    body = _routine("Project.pas", "Proj_ExportSTEP")
    compare = body.index("RunProcess('PCB:Export')")
    # The registered exporter, as a filter of PCB:Export. The old name was
    # not a process at all and did nothing.
    assert "AddStringParameter('Format', 'STEP 3D')" in body
    assert "ExportSTEP3D'" not in body
    assert body.index("ResolvePCBBoard(") < compare, "the board is focused first"
    assert body.index("AgeBefore := FileAge(OutputPath)") < compare
    check = body.index("(AgeAfter <> -1) And (AgeAfter <> AgeBefore)")
    assert check > compare
    assert body.count('"generated":true') == 1
    assert body.index('"generated":true') > check
    assert '"generated":false' in body
    assert '{"success":true}' not in body


def test_repour_goes_through_the_api_and_counts_what_it_rebuilt():
    body = _routine("PCB.pas", "PCB_RepourPolygons")
    assert "RunProcess(" not in body
    for step in ("Poly.SetState_CopperPourInvalid", "Poly.Rebuild",
                 "PolygonCopper(Poly, AreaBefore, ExactBefore)", "PolygonCopper(Poly, AreaAfter, ExactAfter)",
                 "If Not Poly.Poured Then", ".PourIndex >"):
        assert step in body, step
    # The option is set for the repour and put back whatever happens.
    finally_at = body.index("Finally", body.index("PolygonRepour := eAlwaysRepour"))
    assert "PolygonRepour := RepourMode" in body[finally_at:]
    assert '"repoured":true' not in body
    assert "BoolToJsonStr((Rebuilt > 0) And (Failed = 0))" in body


def test_the_teardrop_command_leaves_the_selection_alone():
    """The dialog has its own scope; selecting everything first replaced
    whatever the user had selected."""
    body = _routine("PCB.pas", "PCB_Teardrops")
    # MEASURED: PCB:Teardrop is not a registered process; RunProcess
    # returned at once and nothing opened.
    assert "RunProcess('PCB:TearDropSelectedPads')" in body
    assert "PCB:Select" not in body and "Selected :=" not in body


def test_a_clean_repour_empties_every_pour_before_any_is_rebuilt():
    """MEASURED: after a geometry map moved the pours, small pours rebuilt
    first flowed around the later ring pours' stale copper and came out
    empty, and the rings then closed over them for good."""
    body = _routine("PCB.pas", "PCB_RepourPolygons")
    clean = body.index("If Clean Then")
    rebuild = body.index("Poly.Rebuild")
    assert clean < rebuild
    part = body[clean:rebuild]
    assert "Poly.GroupIterator_Create" in part
    assert "MkSet(eRegionObject, eTrackObject, eArcObject)" in part
    # Collected first, removed after the walk, never freed.
    assert part.index("Poly.GroupIterator_Destroy(GIter)") < part.index(
        "Board.RemovePCBObject(Kids[K])")
    assert "Kids.Free" not in body
    assert "If Poly.Poured Then" in part
    assert "LowerCase(ExtractJsonValue(Params, 'clean')) = 'true'" in body


def test_an_empty_only_repour_clears_the_pours_and_rebuilds_none():
    """Teardrops skips a teardrop that would hit pour copper, so the pours
    are emptied first and poured around the teardrops afterwards."""
    body = _routine("PCB.pas", "PCB_RepourPolygons")
    assert "Clean := Empty Or (" in body, "empty runs the clean step"
    empty_arm = body.index("Else If Empty Then")
    assert empty_arm < body.index("Poly.Rebuild"), "it is checked before the rebuild arm"
    arm = body[empty_arm:body.index("End", body.index("Begin", empty_arm))]
    assert "Rebuild" not in arm and "SetState_CopperPourInvalid" not in arm
    assert "(Rebuilt > 0) Or (Cleared > 0)" in body, "an emptied board is marked changed"


def test_a_board_path_resolves_only_to_that_board():
    """OpenDocument on a caller's path loaded it with no project (a free
    document), and a path that did not resolve fell back to whatever
    board was reachable."""
    body = _routine("Main.pas", "ResolvePCBBoard")
    by_path = body.index("Client.GetDocumentByPath(Path)")
    opened = body.index("Client.OpenDocument('PCB', Path)")
    assert by_path < opened
    # Outside every project only a file that exists is opened.
    assert body.index("If Not FileExists(Path) Then Exit;") < opened
    # Past the empty-path branch nothing falls back to another board.
    assert body.count("GetPCBBoardAnywhere(0)") == 1
    assert "UpperCase(Result.FileName) <> UpperCase(Path) Then Result := Nil" in body


def test_focus_board_says_focused_only_when_the_board_is_in_front():
    body = _routine("PCB.pas", "PCB_FocusBoard")
    check = body.index("If UpperCase(Front) <> UpperCase(Board.FileName) Then")
    assert body.index("Client.ShowDocument(ServerDoc)") < check
    assert "'FOCUS_FAILED'" in body[check:]
    assert body.index('"focused":true') > check


def test_polygon_rows_carry_the_poured_copper_beside_the_outline():
    body = _routine("PCB.pas", "PCB_GetPolygons")
    assert "PolygonCopper(Polygon, CopperSqMils, CopperExact)" in body
    assert '"copper_area_mm2":' in body and '"copper_pieces":' in body
    assert '"copper_area_exact":' in body, "an area whose holes could not be read says so"
    assert '"area_mm2":' in body, "the outline's area stays, under its own name"
