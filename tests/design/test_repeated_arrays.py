# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A repeated subcircuit is laid out as one evenly spaced line.

MEASURED over 707 corpus sheets: 81 (11%) repeat a subcircuit three or more
times, and of the 85 arrays on those sheets, 37 are a single column and 23 a
single row, so 71% are one line; the pitch between instances is a median 500
mils. Found by rendering a macropad sheet where the human drew nine
switch-diode pairs with 18 wires and 8700 mils and the engine scattered the
same 21 parts with 86 wires and 52000 mils.
"""
from __future__ import annotations

from eda_agent.design.arrays import (
    ARRAY_MIN_INSTANCES,
    find_repeated_groups,
    resnap_repeated_arrays,
)
from eda_agent.design.layout import PlacedPart
from eda_agent.design.plan import DesignPlan

_LIB = "/x.SchLib"


def _plan(parts, nets) -> DesignPlan:
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": r[0], "lib_path": _LIB}
                  for r in parts],
        "nets": nets,
    })


def _at(refdes, x, y):
    return PlacedPart(refdes=refdes, sheet="main", x_mils=x, y_mils=y,
                      rotation=0)


def _diode_array(n=4):
    """n copies of (D, R) in series, each pair on its own private node."""
    parts, nets = [], []
    for i in range(1, n + 1):
        parts += [f"D{i}", f"R{i}"]
        nets.append({"name": f"MID{i}",
                     "pins": [{"refdes": f"D{i}", "pin": "2"},
                              {"refdes": f"R{i}", "pin": "1"}]})
    nets.append({"name": "GND", "is_ground": True,
                 "pins": [{"refdes": f"R{i}", "pin": "2"}
                          for i in range(1, n + 1)]})
    return _plan(parts, nets)


def _half(*refdes):
    return {r: (60, 60) for r in refdes}


def test_the_repeated_pairs_are_found_structurally():
    plan = _diode_array(4)
    arrays = find_repeated_groups(plan)
    assert len(arrays) == 1
    assert len(arrays[0]) == 4
    assert all(sorted(inst) == sorted([f"D{i}", f"R{i}"])
               for i, inst in enumerate(sorted(arrays[0]), start=1))


def test_two_copies_are_not_an_array():
    """Two of anything is a coincidence; the corpus counted three or more."""
    assert find_repeated_groups(_diode_array(2)) == []
    assert len(find_repeated_groups(_diode_array(ARRAY_MIN_INSTANCES))) == 1


def test_a_rail_does_not_merge_every_instance_into_one_group():
    """GND touches all of them; if it joined them there is one group, not n."""
    arrays = find_repeated_groups(_diode_array(4))
    assert all(len(inst) == 2 for inst in arrays[0])


def test_scattered_instances_come_back_evenly_spaced_on_one_axis():
    plan = _diode_array(4)
    scattered = [
        _at("D1", 1000, 5000), _at("R1", 1000, 4700),
        _at("D2", 2600, 5300), _at("R2", 2600, 4400),
        _at("D3", 4100, 4800), _at("R3", 4100, 4500),
        _at("D4", 5700, 5100), _at("R4", 5700, 4900),
    ]
    out = {p.refdes: p for p in resnap_repeated_arrays(
        plan, scattered, body_half=_half(*[p.refdes for p in scattered]))}
    cx = sorted({out[f"D{i}"].x_mils for i in range(1, 5)})
    gaps = [b - a for a, b in zip(cx, cx[1:])]
    assert len(cx) == 4, "the four instances should sit at four x positions"
    assert len(set(gaps)) == 1, f"pitch must be even, got {gaps}"
    assert len({out[f"D{i}"].y_mils for i in range(1, 5)}) == 1, (
        "instances on one line share the other axis")


def test_every_instance_is_drawn_on_the_same_internal_shape():
    """The ninth switch-diode pair must look like the first."""
    plan = _diode_array(4)
    scattered = [
        _at("D1", 1000, 5000), _at("R1", 1000, 4700),
        _at("D2", 2600, 5300), _at("R2", 2600, 4400),     # stretched
        _at("D3", 4100, 4800), _at("R3", 4100, 4500),
        _at("D4", 5700, 5100), _at("R4", 5700, 4900),     # squashed
    ]
    out = {p.refdes: p for p in resnap_repeated_arrays(
        plan, scattered, body_half=_half(*[p.refdes for p in scattered]))}
    offsets = {(out[f"D{i}"].x_mils - out[f"R{i}"].x_mils,
                out[f"D{i}"].y_mils - out[f"R{i}"].y_mils)
               for i in range(1, 5)}
    assert len(offsets) == 1, f"instances differ internally: {offsets}"


def test_the_line_follows_the_axis_the_instances_were_already_spread_on():
    """Regularise the placer's arrangement; do not rotate it."""
    plan = _diode_array(3)
    vertical = [
        _at("D1", 3000, 1000), _at("R1", 3000, 800),
        _at("D2", 3100, 3000), _at("R2", 3100, 2800),
        _at("D3", 2900, 5000), _at("R3", 2900, 4800),
    ]
    out = {p.refdes: p for p in resnap_repeated_arrays(
        plan, vertical, body_half=_half(*[p.refdes for p in vertical]))}
    ys = sorted(out[f"D{i}"].y_mils for i in range(1, 4))
    assert len(set(ys)) == 3, "the column must stay a column"
    assert len({out[f"D{i}"].x_mils for i in range(1, 4)}) == 1


def test_the_order_along_the_line_is_the_order_they_were_in():
    """Re-ordering would cross the wires that reach them."""
    plan = _diode_array(3)
    before = [
        _at("D1", 5000, 2000), _at("R1", 5000, 1800),
        _at("D2", 1000, 2000), _at("R2", 1000, 1800),
        _at("D3", 3000, 2000), _at("R3", 3000, 1800),
    ]
    out = {p.refdes: p for p in resnap_repeated_arrays(
        plan, before, body_half=_half(*[p.refdes for p in before]))}
    by_x = sorted(("D1", "D2", "D3"), key=lambda r: out[r].x_mils)
    assert by_x == ["D2", "D3", "D1"], (
        "D2 was leftmost and D1 rightmost; that order must survive")


def test_an_array_that_would_land_on_another_part_is_left_alone():
    """Half a regular array is worse than none."""
    plan = _diode_array(3)
    plan = plan.model_copy(deep=True)
    plan.parts.append(type(plan.parts[0])(
        refdes="U9", lib_ref="U", lib_path=_LIB))
    # ON THE RAIL, not on the array's own net. Putting U9 on MID1 made
    # {D1, R1, U9} a different shape from {D2, R2}, so there were only two
    # D+R instances left, no array at all, and the test passed with the
    # collision check deleted.
    gnd = next(n for n in plan.nets if n.is_ground)
    gnd.pins.append(type(gnd.pins[0])(refdes="U9", pin="1"))
    before = [
        _at("D1", 1000, 3000), _at("R1", 1000, 2800),
        _at("D2", 2000, 3000), _at("R2", 2000, 2800),
        _at("D3", 3000, 3000), _at("R3", 3000, 2800),
        _at("U9", 2000, 3000),                       # sits in the middle slot
    ]
    half = _half("D1", "R1", "D2", "R2", "D3", "R3")
    half["U9"] = (900, 900)                          # a big obstacle
    out = {p.refdes: p for p in resnap_repeated_arrays(plan, before,
                                                       body_half=half)}
    assert [(p.refdes, out[p.refdes].x_mils, out[p.refdes].y_mils)
            for p in before] == [(p.refdes, p.x_mils, p.y_mils)
                                 for p in before]


def test_a_sheet_with_no_repetition_is_untouched():
    plan = _plan(["R1", "C1"], [
        {"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                               {"refdes": "C1", "pin": "1"}]}])
    before = [_at("R1", 1000, 1000), _at("C1", 2000, 1500)]
    out = resnap_repeated_arrays(plan, before, body_half=_half("R1", "C1"))
    assert [(p.x_mils, p.y_mils) for p in out] == \
        [(p.x_mils, p.y_mils) for p in before]


def test_the_result_does_not_depend_on_input_order():
    plan = _diode_array(3)
    base = [
        _at("D1", 1000, 3000), _at("R1", 1000, 2800),
        _at("D2", 2600, 3300), _at("R2", 2600, 2500),
        _at("D3", 4100, 2900), _at("R3", 4100, 2700),
    ]
    half = _half(*[p.refdes for p in base])
    a = resnap_repeated_arrays(plan, base, body_half=half)
    b = resnap_repeated_arrays(plan, list(reversed(base)), body_half=half)
    assert sorted((p.refdes, p.x_mils, p.y_mils) for p in a) == \
        sorted((p.refdes, p.x_mils, p.y_mils) for p in b)
