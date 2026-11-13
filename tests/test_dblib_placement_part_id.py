# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A DbLib placement puts down part 1 of a multi-part symbol unless asked.

Without part_id the loader's own pick landed: on a two-part symbol that
was part 2, and the reply said nothing. The DbLib placer now
takes the part as the .SchLib placer does: part 1 when not given,
written before the component reaches the sheet, written again on the
sheet inside the modify bracket, read back, and named PART_REFUSED
when it did not take.
"""
from __future__ import annotations

from pathlib import Path

from tests.pascal_source import functions, strip_comments

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body():
    return functions(strip_comments(_PAS.read_text(encoding="utf-8")))["DbLibPlaceOne"]


def test_part_one_is_the_default():
    body = _body()
    assert "If PartId < 1 Then PartId := 1;" in body
    # The default is set before anything is written.
    assert body.index("If PartId < 1 Then PartId := 1;") < body.index(
        "Comp.CurrentPartID := PartId;")


def test_the_part_is_written_before_the_add_and_again_on_the_sheet():
    body = _body()
    pre = body.index("Comp.CurrentPartID := PartId;")
    add = body.index("SchDoc.AddSchObject(Comp);")
    register = body.index("SchRegisterObject(SchDoc, Comp);")
    bracket = body.index("SchBeginModify(Comp);")
    again = body.index("Comp.CurrentPartID := PartId;", bracket)
    close = body.index("SchEndModify(Comp);")
    readback = body.index("PartBack := Comp.CurrentPartID;")
    assert pre < add < register < bracket < again < close < readback


def test_a_part_that_did_not_take_is_said():
    body = _body()
    assert "BoolToJsonStr(PartBack = PartId)" in body
    assert "(PartId = 0) Or" not in body
    assert '"reason":"PART_REFUSED"' in body
