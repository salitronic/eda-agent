# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A shunt 2-pin part must stand upright whatever its symbol library.

``_apply_rotations`` writes rotation 270 to mean "stand this part up", on
the assumption that the symbol is natively pins-left-right. MEASURED across
101 distinct 2-pin symbols on public boards: 53 are natively pins-up-down.
For those, 270 lays the part on its side, and in the world frame 98% of
human decoupling caps were upright against 0% of the engine's.

These tests measure the WORLD orientation of the placed pins, never the
rotation value, because the rotation value is what looked correct the whole
time the drawing was wrong.
"""
from __future__ import annotations

from typing import Optional

from eda_agent.design.layout import PlacedPart, correct_two_pin_rotation
from eda_agent.design.pipeline import build_canvas_from_plan
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolExtractor,
    SymbolModel,
    SymbolPin,
)

_LIB = "/fake/lib.SchLib"


def _cap(lib_ref: str, *, native_vertical: bool) -> SymbolModel:
    if native_vertical:
        pins = (SymbolPin(designator="1", name="1", x=0, y=100, orientation=1,
                          length=100, electrical_type="passive"),
                SymbolPin(designator="2", name="2", x=0, y=-100, orientation=3,
                          length=100, electrical_type="passive"))
    else:
        pins = (SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                          length=100, electrical_type="passive"),
                SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                          length=100, electrical_type="passive"))
    return SymbolModel(lib_path=_LIB, lib_ref=lib_ref, pins=pins,
                       body_bbox=SymbolBBox(x_min=-50, y_min=-50,
                                            x_max=50, y_max=50))


def _ic() -> SymbolModel:
    pins = tuple(
        SymbolPin(designator=str(i + 1), name=f"P{i + 1}", x=300,
                  y=200 - i * 100, orientation=0, length=100,
                  electrical_type="passive")
        for i in range(4))
    return SymbolModel(lib_path=_LIB, lib_ref="IC", pins=pins,
                       body_bbox=SymbolBBox(x_min=-300, y_min=-300,
                                            x_max=300, y_max=300))


class _Extractor(SymbolExtractor):
    def __init__(self, symbols):
        self._symbols = symbols

    def extract_one(self, lib_path, lib_ref) -> Optional[SymbolModel]:
        return self._symbols.get((lib_path, lib_ref))

    def extract_many(self, refs):
        return {k: self._symbols[k] for k in refs if k in self._symbols}


def _plan() -> DesignPlan:
    """C1 is a decoupling cap (VCC to GND); R1 is a series signal part."""
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"}]},
            {"name": "SIG_A", "pins": [{"refdes": "U1", "pin": "3"},
                                       {"refdes": "R1", "pin": "1"}]},
            {"name": "SIG_B", "pins": [{"refdes": "U1", "pin": "4"},
                                       {"refdes": "R1", "pin": "2"}]},
        ],
    })


def _world_vertical(model: SymbolModel, rotation: int) -> bool:
    from eda_agent.design.canvas import SymbolInstance

    inst = SymbolInstance(refdes="X", symbol=model, x=0, y=0,
                          rotation=rotation)
    a, b = list(inst.all_pin_endpoints())
    return abs(a.y - b.y) > abs(a.x - b.x)


def _placed(rotation_c1: int, rotation_r1: int = 0) -> list[PlacedPart]:
    return [
        PlacedPart(refdes="U1", sheet="main", x_mils=2000, y_mils=2000, rotation=0),
        PlacedPart(refdes="C1", sheet="main", x_mils=3000, y_mils=2000,
                   rotation=rotation_c1),
        PlacedPart(refdes="R1", sheet="main", x_mils=3000, y_mils=1000,
                   rotation=rotation_r1),
    ]


def test_a_natively_vertical_decoupling_cap_ends_upright():
    """The case the placer got backwards: 270 on a pins-up-down symbol."""
    cap = _cap("CAP", native_vertical=True)
    out = correct_two_pin_rotation(
        _plan(), _placed(270), {"C1": cap, "R1": _cap("RES", native_vertical=False)})
    c1 = next(p for p in out if p.refdes == "C1")
    assert _world_vertical(cap, c1.rotation), (
        f"rotation {c1.rotation} lays a pins-up-down cap on its side")


def test_a_natively_horizontal_decoupling_cap_ends_upright_too():
    """The case the placer already handled must keep working."""
    cap = _cap("CAP", native_vertical=False)
    out = correct_two_pin_rotation(
        _plan(), _placed(270), {"C1": cap, "R1": _cap("RES", native_vertical=False)})
    c1 = next(p for p in out if p.refdes == "C1")
    assert _world_vertical(cap, c1.rotation)


def test_the_pass_is_idempotent():
    """Running it over its own output must change nothing.

    The first version derived the intent from the CURRENT rotation, so a
    second application flipped every corrected part back. The best-of
    variants rebuild from placements a previous build already corrected, so
    that path is real: measured, decoupling caps went from 0% upright to 11%
    instead of ~98%.
    """
    symbols = {"C1": _cap("CAP", native_vertical=True),
               "R1": _cap("RES", native_vertical=False)}
    once = correct_two_pin_rotation(_plan(), _placed(270), symbols)
    twice = correct_two_pin_rotation(_plan(), once, symbols)
    assert [(p.refdes, p.rotation) for p in twice] == \
        [(p.refdes, p.rotation) for p in once]


def test_a_series_signal_part_is_left_alone():
    """Only the shunt-on-a-rail convention is enforced.

    A series resistor's right orientation depends on where its neighbours
    sit, which the placer decided; this pass has no better information.
    """
    symbols = {"C1": _cap("CAP", native_vertical=True),
               "R1": _cap("RES", native_vertical=True)}
    for rot in (0, 270):
        out = correct_two_pin_rotation(_plan(), _placed(270, rot), symbols)
        r1 = next(p for p in out if p.refdes == "R1")
        assert r1.rotation == rot


def test_the_override_path_is_corrected_as_well():
    """The best-of variants rebuild through layout_overrides.

    That path skips the 2b-2d placement block entirely, and a correction
    placed inside that block reached only the base candidate: measured,
    decoupling caps went from 0% upright to 11% instead of ~98%. The pass
    has to run where every path goes through.
    """
    cap = _cap("CAP", native_vertical=True)
    symbols = {(_LIB, "IC"): _ic(), (_LIB, "CAP"): cap,
               (_LIB, "RES"): _cap("RES", native_vertical=False)}
    plan = _plan()
    overrides = {p.refdes: p for p in _placed(270)}
    result = build_canvas_from_plan(plan, _Extractor(symbols),
                                    layout_overrides=overrides)
    c1 = result.canvas.instance_by_refdes("C1")
    assert c1 is not None
    a, b = list(c1.all_pin_endpoints())
    assert abs(a.y - b.y) > abs(a.x - b.x), (
        f"C1 came out at rotation {c1.rotation}, pins left-right, on the "
        f"override path")


def test_a_two_pin_power_connector_is_not_stood_upright():
    """J1 on VCC/GND passes the shunt test, and is still the sheet's I/O edge.

    ``_apply_rotations`` lays connectors horizontal on purpose; measured on
    the corpus, 0% of human 2-pin connectors stand upright.
    """
    plan = _plan()
    plan = plan.model_copy(deep=True)
    plan.parts.append(type(plan.parts[0])(
        refdes="J1", lib_ref="HDR", lib_path=_LIB, status="existing",
        sheet="main"))
    plan.nets[0].pins.append(type(plan.nets[0].pins[0])(refdes="J1", pin="1"))
    plan.nets[1].pins.append(type(plan.nets[0].pins[0])(refdes="J1", pin="2"))
    hdr = _cap("HDR", native_vertical=True)     # pins up/down natively
    placed = _placed(270) + [PlacedPart(refdes="J1", sheet="main", x_mils=500,
                                        y_mils=2000, rotation=270)]
    out = correct_two_pin_rotation(
        plan, placed, {"C1": _cap("CAP", native_vertical=True),
                       "R1": _cap("RES", native_vertical=False), "J1": hdr})
    j1 = next(p for p in out if p.refdes == "J1")
    assert j1.rotation == 270, "the connector was re-oriented"
