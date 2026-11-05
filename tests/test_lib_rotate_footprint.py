# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_rotate_footprint turns a footprint about its origin, every primitive.

Asked for to turn house footprints to an assembler's default orientation;
nothing but the editor's own select-all-and-rotate could do it.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _code(text: str) -> str:
    """The source without { } comments."""
    return re.sub(r"\{[^}]*\}", "", text)


@pytest.fixture(scope="module")
def lib():
    return load(_ALTIUM / "Library.pas")


def test_it_is_dispatched():
    text = (_ALTIUM / "Library.pas").read_text(encoding="utf-8")
    assert "'rotate_footprint':   Result := Lib_RotateFootprint(Params, RequestId);" in text


@pytest.mark.parametrize("quarter,want", [
    (1, (-20, 10)), (2, (-10, -20)), (3, (20, -10)), (0, (10, 20)),
])
def test_quarter_turns_counter_clockwise(lib, quarter, want):
    """RotCoordX/Y, read as Python: (10, 20) about the origin."""
    fx = _code(lib["RotCoordX"])
    fy = _code(lib["RotCoordY"])

    def ev(body, x, y, q):
        for line in body.splitlines():
            m = re.search(r"Quarter = (\d) Then Result := (.+)", line)
            if m and int(m.group(1)) == q:
                return eval(m.group(2).rstrip(";").replace("OX", "0").replace("OY", "0"),
                            {}, {"X": x, "Y": y})
        return {"x": x, "y": y}["x" if body is fx else "y"]

    assert (ev(fx, 10, 20, quarter), ev(fy, 10, 20, quarter)) == want


def test_every_kind_walks_into_its_own_typed_local(lib):
    """A primitive held in a list keeps only the base interface: no x, no
    Rotation. Each kind is walked from the iterator into its own type."""
    body = _code(lib["Lib_RotateFootprint"])
    for local, kind in (("Trk", "eTrackObject"), ("Pad", "ePadObject"),
                        ("Arc", "eArcObject"), ("Fill", "eFillObject"),
                        ("Rgn", "eRegionObject"), ("Via", "eViaObject"),
                        ("Txt", "eTextObject"), ("Body", "eComponentBodyObject")):
        assert f"GrpIter.AddFilter_ObjectSet(MkSet({kind}));" in body
        assert f"{local} := GrpIter.FirstPCBObject;" in body
        assert f"FirstSighting(Seen, Addr)" in body
    assert "TInterfaceList" not in body


def test_tracks_by_end_points_and_the_rest_by_rotate_around(lib):
    body = _code(lib["Lib_RotateFootprint"])
    assert "Trk.x1 := RotCoordX(X1, Y1, OX, OY, Quarter);" in body
    for local in ("Pad", "Arc", "Fill", "Rgn", "Via", "Txt", "Body"):
        assert f"{local}.RotateAroundXY(OX, OY, Angle);" in body


def test_pads_are_read_back(lib):
    body = _code(lib["Lib_RotateFootprint"])
    turn = body.index("Pad.RotateAroundXY(OX, OY, Angle);")
    check = body.index("If (Abs(Pad.x - WantX) > 1) Or (Abs(Pad.y - WantY) > 1)")
    assert turn < check
    assert "Not SameAngle(PadRotAfter, PadRotBefore + Angle)" in body


def test_the_model_is_never_written(lib):
    """GetState cannot hand values back to DelphiScript, so a SetState
    built on them would reset a STEP model's X/Y rotation and Z offset."""
    body = _code(lib["Lib_RotateFootprint"])
    assert "SetState(" not in body and "GetState(" not in body
    assert "Body.Model :=" not in body


def test_bad_angles_are_refused_before_altium(monkeypatch):
    from eda_agent.tools import library as mod
    calls = []

    class _Bridge:
        async def send_command_async(self, command, params=None, **kw):
            calls.append((command, dict(params or {})))
            if command == "application.save_document":
                return {"saved": True}
            return {"mismatches": 0}

    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def reg(fn):
                tools[fn.__name__] = fn
                return fn
            return reg

        def __getattr__(self, n):
            return lambda *a, **k: (lambda fn: fn)

    monkeypatch.setattr(mod, "get_bridge", lambda: _Bridge())
    mod.register_library_tools(_Mcp())
    rot = tools["lib_rotate_footprint"]
    assert "angle" in asyncio.run(rot("L.PcbLib", "FP", 45))["error"]
    assert calls == []

    out = asyncio.run(rot("L.PcbLib", "FP", -90))
    names = [c for c, _ in calls]
    # Opened as a document first: a library loaded off screen cannot be saved.
    assert names[0] == "application.set_active_document"
    assert ("library.rotate_footprint", {
        "library_path": "L.PcbLib", "footprint": "FP", "angle": "-90",
        "dry_run": "false", "show": "false"}) in calls
    assert names[-1] == "application.save_document", "saved when nothing mismatched"
    assert out["saved"] is True

    calls.clear()
    asyncio.run(rot("L.PcbLib", "FP", 90, dry_run=True))
    assert [c for c, _ in calls] == ["library.rotate_footprint"], "a dry run opens nothing"

    calls.clear()

    class _Bad(_Bridge):
        async def send_command_async(self, command, params=None, **kw):
            calls.append((command, dict(params or {})))
            return {"mismatches": 2}

    monkeypatch.setattr(mod, "get_bridge", lambda: _Bad())
    out = asyncio.run(rot("L.PcbLib", "FP", 90))
    assert out["saved"] is False, "a mismatch is never saved"
    assert "application.save_document" not in [c for c, _ in calls]
