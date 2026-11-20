# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""zoom_to_xy on a schematic sheet or symbol library must move the view.

The schematic server has no Sch:ZoomToLocation, and RunProcess of an
unknown process returns without a word, so a reply of success says
nothing about the view.

The view is fitted to the same square of +-radius mils round x,y as on a
board, through Sch:Zoom with Action=ZoomArea and the two corners as
Location1.x / .y and Location2.x / .y in internal units.
"""

from __future__ import annotations

from pathlib import Path

GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def _body() -> str:
    src = GENERIC.read_text(encoding="latin-1")
    start = src.index("Function Gen_ZoomToXY(")
    return src[start:src.index("\nFunction ", start + 1)]


def _sch_branch() -> str:
    body = _body()
    start = body.index("Else If SchDoc <> Nil Then")
    return body[start:body.index("'NO_DOCUMENT'", start)]


def test_no_process_the_schematic_server_lacks():
    assert "ZoomToLocation" not in _body()


def test_the_view_is_fitted_to_the_square_round_the_point():
    branch = _sch_branch()
    assert "AddStringParameter('Action', 'ZoomArea');" in branch
    assert "RunProcess('Sch:Zoom');" in branch
    # Every corner is given: the process prompts for corners it lacks, and a
    # prompt waiting for a click would hold the polling loop.
    for name, value in (("Location1.x", "Cx - Rad"), ("Location1.y", "Cy - Rad"),
                        ("Location2.x", "Cx + Rad"), ("Location2.y", "Cy + Rad")):
        assert f"AddStringParameter('{name}', IntToStr({value}));" in branch, name


def test_the_corners_are_in_internal_units():
    # Measured: corners given in mils were read as internal units and the
    # view went to the sheet origin at full zoom.
    body = _body()
    assert (body.index("Cx := MilsToCoordF(StrToFloatDef(XStr, 0))")
            < body.index("If Board <> Nil Then"))
    branch = _sch_branch()
    assert "XStr" not in branch.split("Result :=")[0]


def test_the_parameters_are_reset_before_the_zoom():
    branch = _sch_branch()
    assert (branch.index("ResetParameters;")
            < branch.index("AddStringParameter('Action', 'ZoomArea');"))
