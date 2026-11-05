# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe
"""A keepout's restrictions can be read and written through the generic layer.

IsKeepout could be read but not written, and what a keepout keeps out (the
Keepout Restrictions in Altium's Properties panel: Copper, Track, Via, SMD
Pad, TH Pad) could not be reached at all. A keepout restricted to Copper is
a wall a polygon stops at while routing may still cross it, which is how a
pour is shaped by hand. Measured on AD 25.8: obj_modify on a placed track
or arc with IsKeepout=true|IsKeepoutForCopper=true|IsKeepoutForTrack=false
... reads back exactly that, and the repour stops at the wall.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_UNIT = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCBGeneric.pas"
_FLAGS = ("IsKeepoutForCopper", "IsKeepoutForTrack", "IsKeepoutForVia",
          "IsKeepoutForSMDPad", "IsKeepoutForTHPad")


def _fn(name):
    return load(_UNIT, minimum=20)[name]


def test_every_restriction_is_read():
    getter = _fn("GetPCBProperty")
    for flag in _FLAGS:
        assert f"Result := BoolToJsonStr(Obj.{flag})" in getter, flag


def test_every_restriction_and_the_keepout_flag_are_written():
    setter = _fn("SetPCBProperty")
    for flag in ("IsKeepout",) + _FLAGS:
        assert f"Obj.{flag} := StrToBool(Value)" in setter, flag


def test_the_write_is_refused_on_objects_that_cannot_be_keepouts():
    setter = _fn("SetPCBProperty")
    branch = setter[setter.index("(PropName = 'IsKeepoutForCopper') Or"):]
    branch = branch[:branch.index("Else If PropName = 'RemoveDead'")]
    guard = re.search(r"If \(Oid = eTrackObject\) Or \(Oid = eArcObject\) Or "
                      r"\(Oid = eFillObject\) Or\s+\(Oid = eRegionObject\) Then", branch)
    assert guard, "the write is limited to tracks, arcs, fills and regions"
    assert "Else Matched := False;" in branch[guard.end():]


def test_the_restrictions_are_known_and_advertised():
    known = _fn("IsKnownPCBProperty")
    listed = _fn("KnownPCBPropertyList")
    for flag in _FLAGS:
        assert f"(PropName = '{flag}')" in known, flag
        assert flag in listed, flag


def test_the_restrictions_are_only_read_on_objects_that_have_them():
    # Asked of a via, Obj.IsKeepoutForCopper raised "Undeclared identifier"
    # as a modal (AD 25.8): the name is resolved on the object at run time.
    getter = _fn("GetPCBProperty")
    branch = getter[getter.index("(PropName = 'IsKeepoutForCopper') Or"):]
    branch = branch[:branch.index("Else If PropName = 'TearDrop'")]
    guard = re.search(r"If \(Oid = eTrackObject\) Or \(Oid = eArcObject\) Or "
                      r"\(Oid = eFillObject\) Or\s+\(Oid = eRegionObject\) Then", branch)
    assert guard, "the read is limited to tracks, arcs, fills and regions"
    for flag in _FLAGS:
        assert branch.index(f"Obj.{flag}") > guard.end(), flag
