# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Parts nearly sharing an axis with a wired neighbour are nudged onto it.

MEASURED over the 39 sheets of the stratified sample: 77.8% of parts on a
human sheet share an exact x or y with another part. The engine reaches
65.5% after the priors, the overlap shove knocks it to 54.5% because it
judges each part alone, and the shape-specific resnaps recover only to
61.5%. This pass takes it to 65.7% (mean 58.2 to 67.3).

Every refusal below is one this pass learned by breaking something.
"""
from __future__ import annotations

from eda_agent.design.layout import PlacedPart
from eda_agent.design.plan import DesignPlan
from eda_agent.design.priors import align_to_neighbour_axes

_LIB = "/x.SchLib"


def _plan(parts, nets) -> DesignPlan:
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": r[0], "lib_path": _LIB}
                  for r in parts],
        "nets": nets,
    })


def _at(refdes, x, y, rot=0):
    return PlacedPart(refdes=refdes, sheet="main", x_mils=x, y_mils=y,
                      rotation=rot)


def _tiny_half(*refdes):
    return {r: (50, 50) for r in refdes}


def test_a_part_just_off_its_neighbours_axis_is_snapped_on():
    plan = _plan(["R1", "R2"], [
        {"name": "SIG", "pins": [{"refdes": "R1", "pin": "1"},
                                 {"refdes": "R2", "pin": "2"}]}])
    out = align_to_neighbour_axes(
        plan, [_at("R1", 1000, 2000), _at("R2", 3000, 2200)],
        body_half=_tiny_half("R1", "R2"))
    r1 = next(p for p in out if p.refdes == "R1")
    r2 = next(p for p in out if p.refdes == "R2")
    # WHICH of the two moves is not the property: parts are visited in
    # refdes order and either one landing on the other's row is the same
    # alignment. Asserting that R2 moved would pin an implementation detail
    # and fail the day the visit order changes for a good reason.
    assert r1.y_mils == r2.y_mils, "the pair should end up sharing a row"
    assert {r1.y_mils} <= {2000, 2200}, "the row must be one they already had"
    assert (r1.x_mils, r2.x_mils) == (1000, 3000), "only the near axis moves"


def test_a_part_far_off_the_axis_is_left_alone():
    """max_shift is what keeps this a nudge rather than a placement."""
    plan = _plan(["R1", "R2"], [
        {"name": "SIG", "pins": [{"refdes": "R1", "pin": "1"},
                                 {"refdes": "R2", "pin": "2"}]}])
    before = [_at("R1", 1000, 2000), _at("R2", 3000, 4000)]
    out = align_to_neighbour_axes(plan, before, body_half=_tiny_half("R1", "R2"),
                                  max_shift=300)
    assert [(p.x_mils, p.y_mils) for p in out] == \
        [(p.x_mils, p.y_mils) for p in before]


def test_parts_that_share_no_net_are_not_aligned():
    """Lining up with an unrelated part across the sheet is a coincidence."""
    plan = _plan(["R1", "R2"], [
        {"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                               {"refdes": "R1", "pin": "2"}]},
        {"name": "B", "pins": [{"refdes": "R2", "pin": "1"},
                               {"refdes": "R2", "pin": "2"}]}])
    before = [_at("R1", 1000, 2000), _at("R2", 3000, 2200)]
    out = align_to_neighbour_axes(plan, before, body_half=_tiny_half("R1", "R2"))
    assert [(p.x_mils, p.y_mils) for p in out] == \
        [(p.x_mils, p.y_mils) for p in before]


def test_a_rail_is_not_a_neighbourhood():
    """A rail touches most of the sheet; aligning to it means nothing."""
    plan = _plan(["R1", "R2"], [
        {"name": "GND", "is_ground": True,
         "pins": [{"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]}])
    before = [_at("R1", 1000, 2000), _at("R2", 3000, 2200)]
    out = align_to_neighbour_axes(plan, before, body_half=_tiny_half("R1", "R2"))
    assert [(p.x_mils, p.y_mils) for p in out] == \
        [(p.x_mils, p.y_mils) for p in before]


def test_an_anchor_is_never_moved():
    """apply_placement_priors leaves anchors alone for the same reason: parts
    cluster around them, so nudging one shifts every relationship at once.
    Measured on the 555 blinker, moving U1 by 200 mils changed the score
    enough that a different candidate won and the timing network came out on
    the wrong side of the IC."""
    plan = _plan(["U1", "R1"], [
        {"name": "A", "pins": [{"refdes": "U1", "pin": "1"},
                               {"refdes": "R1", "pin": "1"}]},
        {"name": "B", "pins": [{"refdes": "U1", "pin": "2"},
                               {"refdes": "R1", "pin": "2"}]},
        {"name": "C", "pins": [{"refdes": "U1", "pin": "3"},
                               {"refdes": "U1", "pin": "4"}]}])
    out = align_to_neighbour_axes(
        plan, [_at("U1", 1000, 2000), _at("R1", 3000, 2200)],
        body_half=_tiny_half("U1", "R1"))
    u1 = next(p for p in out if p.refdes == "U1")
    assert (u1.x_mils, u1.y_mils) == (1000, 2000)


def test_a_move_that_would_overlap_is_refused():
    """The overlap is exactly what the shove exists to prevent.

    R1 is visited first and is the part that would move: its only mate is
    R2, 100 mils up, so its target is (1000, 2100). R3 already sits there
    and is on no shared net, so it is not a mate and will not move out of
    the way. An earlier version of this test put the obstacle in R2's target
    instead, and R2 never moved anyway (R1 had already lined the pair up),
    so it passed with the overlap check deleted.
    """
    plan = _plan(["R1", "R2", "R3"], [
        {"name": "SIG", "pins": [{"refdes": "R1", "pin": "1"},
                                 {"refdes": "R2", "pin": "2"}]},
        {"name": "T", "pins": [{"refdes": "R3", "pin": "1"},
                               {"refdes": "R3", "pin": "2"}]}])
    before = [_at("R1", 1000, 2000), _at("R2", 3000, 2100),
              _at("R3", 1000, 2100)]                 # sits in R1's target
    out = align_to_neighbour_axes(plan, before,
                                  body_half=_tiny_half("R1", "R2", "R3"))
    r1 = next(p for p in out if p.refdes == "R1")
    r3 = next(p for p in out if p.refdes == "R3")
    assert (r1.x_mils, r1.y_mils) == (1000, 2000), (
        "R1's target is occupied by R3 and the move must be refused")
    assert (r3.x_mils, r3.y_mils) == (1000, 2100), "R3 must not move"


def test_a_crystal_load_cap_is_left_to_its_own_resnap():
    """resnap_crystal_clusters places these deliberately and tightly; the
    aligner pulled one from 400 to 700 mils off its crystal to line it up."""
    plan = _plan(["U1", "Y1", "C1", "C2"], [
        {"name": "XA", "pins": [{"refdes": "Y1", "pin": "1"},
                                {"refdes": "U1", "pin": "1"},
                                {"refdes": "C1", "pin": "1"}]},
        {"name": "XB", "pins": [{"refdes": "Y1", "pin": "2"},
                                {"refdes": "U1", "pin": "2"},
                                {"refdes": "C2", "pin": "1"}]},
        {"name": "GND", "is_ground": True,
         "pins": [{"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                  {"refdes": "U1", "pin": "3"}, {"refdes": "U1", "pin": "4"}]}])
    before = [_at("U1", 1000, 5000), _at("Y1", 3000, 2000),
              _at("C1", 2600, 2100), _at("C2", 3400, 2100)]
    out = align_to_neighbour_axes(plan, before,
                                  body_half=_tiny_half("U1", "Y1", "C1", "C2"))
    assert [(p.refdes, p.x_mils, p.y_mils) for p in out] == \
        [(p.refdes, p.x_mils, p.y_mils) for p in before]


def test_the_result_does_not_depend_on_dict_order():
    plan = _plan(["R1", "R2", "R3"], [
        {"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                               {"refdes": "R2", "pin": "2"}]},
        {"name": "B", "pins": [{"refdes": "R2", "pin": "1"},
                               {"refdes": "R3", "pin": "2"}]}])
    base = [_at("R1", 1000, 2000), _at("R2", 3000, 2200), _at("R3", 5000, 2100)]
    half = _tiny_half("R1", "R2", "R3")
    first = align_to_neighbour_axes(plan, base, body_half=half)
    second = align_to_neighbour_axes(plan, list(reversed(base)), body_half=half)
    assert sorted((p.refdes, p.x_mils, p.y_mils) for p in first) == \
        sorted((p.refdes, p.x_mils, p.y_mils) for p in second)
