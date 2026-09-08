# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Live sheet positions come back in the frame placement_hints expects.

The conversion is the whole point of this code path. Altium reports a
component's ``Location`` as the symbol ORIGIN, which is what the emitter
wrote; ``placement_hints`` are read as ``PlacedPart`` BODY CENTRES.
Passing one straight through as the other shifts every part by its own
centre offset, silently, and by a different amount per symbol, so a
"keep everything where it is" re-run would move the whole sheet.
"""
from __future__ import annotations

import json

from eda_agent.design.canvas import SymbolInstance
from eda_agent.design.orchestrator import hints_from_sheet
from eda_agent.design.pipeline import _center_offset
from eda_agent.design.symbols import SymbolCache
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


def _symbol(lib_ref: str, lib_path: str = "LIB.SchLib") -> SymbolModel:
    # Pins deliberately off-centre so the body centre is NOT the origin;
    # a symmetric symbol would hide exactly the bug this guards.
    return SymbolModel(
        lib_path=lib_path,
        lib_ref=lib_ref,
        pins=[
            SymbolPin(designator="1", name="A", x=0, y=400,
                      orientation=1, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="K", x=0, y=-100,
                      orientation=3, length=100, electrical_type="passive"),
        ],
        # Body sits ABOVE the origin, so the centre is not the origin.
        body_bbox=SymbolBBox(x_min=-100, y_min=0, x_max=100, y_max=400),
    )


class _Bridge:
    def __init__(self, rows):
        self._rows = rows

    def send_command(self, command, params=None, **kw):
        assert command == "generic.query_objects"
        return {"objects": self._rows}


def _lib(tmp_path) -> str:
    """A real file: SymbolCache.put stats lib_path and skips if it is gone."""
    path = tmp_path / "LIB.SchLib"
    path.write_text("", encoding="utf-8")
    return str(path)


def _write_snapshot(tmp_path, refdes_to_ref):
    project = tmp_path / "board.PrjPcb"
    project.write_text("", encoding="utf-8")
    snap = {
        "plan": {},
        "canvas": {"instances": [
            {"refdes": r, "lib_path": _lib(tmp_path), "lib_ref": ref,
             "x": 0, "y": 0, "rotation": 0}
            for r, ref in refdes_to_ref.items()
        ]},
    }
    project.with_suffix(".canvas.json").write_text(
        json.dumps(snap), encoding="utf-8")
    return project


def _cache(tmp_path, models):
    cache = SymbolCache(tmp_path / "symcache")
    for m in models:
        cache.put(m)
    return tmp_path / "symcache"


def test_positions_convert_from_origin_to_body_centre(tmp_path):
    model = _symbol("DIODE", _lib(tmp_path))
    project = _write_snapshot(tmp_path, {"D1": "DIODE"})
    cache_dir = _cache(tmp_path, [model])
    bridge = _Bridge([
        {"Designator.Text": "D1", "Location.X": 5000, "Location.Y": 3000,
         "Orientation": 0},
    ])

    out = hints_from_sheet(str(project), bridge=bridge, cache_dir=cache_dir)
    assert out["ok"] is True
    hint = out["hints"]["D1"]

    off_x, off_y = _center_offset(model, 0, False)
    assert (off_x, off_y) != (0, 0), (
        "fixture is wrong: a symbol whose centre IS its origin cannot "
        "detect the frame bug this test exists for")
    assert hint == {"x": 5000 + off_x, "y": 3000 + off_y, "rotation": 0}


def test_the_hint_round_trips_back_to_the_reported_location(tmp_path):
    """Feeding the hint through the emitter's own conversion returns it.

    This is the property that matters: hint the sheet at its current
    positions, re-run, and nothing moves.
    """
    model = _symbol("DIODE", _lib(tmp_path))
    project = _write_snapshot(tmp_path, {"D1": "DIODE"})
    cache_dir = _cache(tmp_path, [model])
    for rotation in (0, 90, 180, 270):
        bridge = _Bridge([
            {"Designator.Text": "D1", "Location.X": 5000, "Location.Y": 3000,
             "Orientation": rotation // 90},
        ])
        out = hints_from_sheet(str(project), bridge=bridge,
                               cache_dir=cache_dir)
        hint = out["hints"]["D1"]
        # The pipeline turns a placement centre back into an instance
        # origin exactly this way.
        off_x, off_y = _center_offset(model, hint["rotation"], False)
        assert (hint["x"] - off_x, hint["y"] - off_y) == (5000, 3000), (
            f"round trip lost the position at rotation {rotation}")


def test_a_part_missing_from_the_sheet_is_reported_not_guessed(tmp_path):
    project = _write_snapshot(tmp_path, {"D1": "DIODE", "R9": "DIODE"})
    cache_dir = _cache(tmp_path, [_symbol("DIODE", _lib(tmp_path))])
    bridge = _Bridge([
        {"Designator.Text": "D1", "Location.X": 100, "Location.Y": 200,
         "Orientation": 0},
    ])
    out = hints_from_sheet(str(project), bridge=bridge, cache_dir=cache_dir)
    assert "D1" in out["hints"]
    assert out["unmatched"] == ["R9"]
    assert "R9" not in out["hints"], (
        "an unreadable part must be left for the placer, never hinted at a "
        "guessed position")
    assert any("unhinted" in n for n in out["notes"])


def test_no_snapshot_says_what_to_run(tmp_path):
    project = tmp_path / "nothing.PrjPcb"
    project.write_text("", encoding="utf-8")
    out = hints_from_sheet(str(project), bridge=_Bridge([]))
    assert out["ok"] is False
    assert any("design_execute_plan" in n for n in out["notes"])
