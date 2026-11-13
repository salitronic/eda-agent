# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A record a failed read never assigned is not written field by field.

In DelphiScript a field write on a TCoordRect that was never assigned
from an API call raises "Undeclared identifier: Left", and Try/Except
does not catch it. Four places wrote fields after a read that can fail:
three inside the Except of that read (pad-centre audit, room listing),
one before a second Try (invalid-regions audit). They now keep the
values in plain TCoord locals.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load, strip_comments

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_EXCEPT_FIELD_WRITE = re.compile(
    r"\bExcept\b(?:(?!\bEnd\b).)*?\b\w+\.(?:Left|Right|Top|Bottom|X1|Y1|X2|Y2)\s*:=",
    re.S | re.I)


def test_no_except_block_writes_a_rectangle_field():
    hits = []
    for f in sorted(_DIR.glob("*.pas")):
        text = strip_comments(f.read_text(encoding="utf-8-sig"))
        for m in _EXCEPT_FIELD_WRITE.finditer(text):
            hits.append("%s:%d" % (f.name, text[:m.start()].count("\n") + 1))
    assert not hits, hits


def test_pad_centre_audit_filters_by_locals():
    body = load(_DIR / "Audit.pas")["Audit_FindPadsCenterNotConnected"]
    assert body.count("SIter.AddFilter_Area(RL, RB, RR, RT);") == 2
    assert "AddFilter_Area(Rect." not in body
    assert body.count("RL := Rect.Left;") == 2


def test_invalid_regions_audit_reports_from_locals():
    body = load(_DIR / "Audit.pas")["Audit_FindInvalidRegions"]
    assert "BBox.X1 :=" not in body and "BBox.Y1 :=" not in body
    assert "AtX := BBox.X1; AtY := BBox.Y1;" in body
    assert "CoordToMilsF(AtX)" in body and "CoordToMilsF(AtY)" in body


def test_room_listing_reports_from_locals():
    fns = load(_DIR / "PCB.pas")
    body = next(b for b in fns.values() if "Room := Rule;" in b)
    assert "BR.Left :=" not in body
    assert "BL := BR.Left; BB := BR.Bottom; BRt := BR.Right; BT := BR.Top;" in body
    for v in ("BL", "BB", "BRt", "BT"):
        assert "CoordToMilsF(%s)" % v in body
