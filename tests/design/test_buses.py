# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for bus detection + the canvas Bus/BusEntry data model."""

from __future__ import annotations

import pytest

from eda_agent.design.buses import BusGroup, detect_buses
from eda_agent.design.canvas import BusEntry, BusSegment, SchematicCanvas
from eda_agent.design.plan import DesignPlan

_LIB = "/fake/lib.SchLib"


def _make_plan(parts, nets) -> DesignPlan:
    return DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A3"}],
        "parts": parts,
        "nets": nets,
    })


def _ic_board(width: int, ctrl: int = 0):
    """U1 (MCU) <-> U2 (MEM) with ``width`` data nets + ``ctrl`` extra signals
    from U1 to a small connector J2, plus power/ground."""
    parts = [
        {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
         "status": "existing", "sheet": "main"}
        for r, lr in [("U1", "MCU"), ("U2", "MEM"), ("J2", "HDR")]
    ]
    nets = [
        {"name": f"D{i}", "pins": [
            {"refdes": "U1", "pin": str(i + 1)},
            {"refdes": "U2", "pin": str(i + 1)}]}
        for i in range(width)
    ]
    nets += [
        {"name": f"S{i}", "pins": [
            {"refdes": "U1", "pin": f"s{i}"},
            {"refdes": "J2", "pin": str(i + 1)}]}
        for i in range(ctrl)
    ]
    nets += [
        {"name": "VCC", "is_power": True, "pins": [
            {"refdes": "U1", "pin": "V"}, {"refdes": "U2", "pin": "V"}]},
        {"name": "GND", "is_ground": True, "pins": [
            {"refdes": "U1", "pin": "G"}, {"refdes": "U2", "pin": "G"},
            {"refdes": "J2", "pin": "g"}]},
    ]
    return _make_plan(parts, nets)


def test_detect_wide_data_bus():
    buses = detect_buses(_ic_board(8))
    assert len(buses) == 1
    b = buses[0]
    assert b.width == 8
    assert b.nets == tuple(f"D{i}" for i in range(8))
    assert b.endpoints == frozenset({"U1", "U2"})


def test_narrow_group_is_not_a_bus():
    # 3 shared nets < default min_width (4): more readable as wires/labels.
    assert detect_buses(_ic_board(3)) == []
    # ...but exactly 4 is a bus.
    assert len(detect_buses(_ic_board(4))) == 1


def test_min_width_is_tunable():
    assert detect_buses(_ic_board(6), min_width=8) == []
    assert len(detect_buses(_ic_board(8), min_width=8)) == 1


def test_power_and_ground_never_form_a_bus():
    # 8 caps all on (VCC, GND) -- a power/ground pair, not a signal bus.
    parts = [{"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
              "status": "existing", "sheet": "main"}]
    parts += [{"refdes": f"C{i}", "lib_ref": "CAP", "lib_path": _LIB,
               "status": "existing", "sheet": "main"} for i in range(8)]
    nets = [
        {"name": "VCC", "is_power": True, "pins":
         [{"refdes": "U1", "pin": "1"}]
         + [{"refdes": f"C{i}", "pin": "1"} for i in range(8)]},
        {"name": "GND", "is_ground": True, "pins":
         [{"refdes": "U1", "pin": "2"}]
         + [{"refdes": f"C{i}", "pin": "2"} for i in range(8)]},
    ]
    assert detect_buses(_make_plan(parts, nets)) == []


def test_two_pin_part_cannot_anchor_a_bus():
    """A part can only be a bus endpoint if it touches >= 4 nets; a 2-pin
    resistor physically cannot, so a fan of nets through passives is not a
    bus."""
    # 4 nets each connecting U1 and a DISTINCT 2-pin resistor -> each net's
    # part set is unique ({U1, Rk}), so no group reaches min_width.
    parts = [{"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
              "status": "existing", "sheet": "main"}]
    parts += [{"refdes": f"R{i}", "lib_ref": "RES", "lib_path": _LIB,
               "status": "existing", "sheet": "main"} for i in range(4)]
    nets = [{"name": f"N{i}", "pins": [
        {"refdes": "U1", "pin": str(i + 1)},
        {"refdes": f"R{i}", "pin": "1"}]} for i in range(4)]
    assert detect_buses(_make_plan(parts, nets)) == []


def test_extra_signals_to_other_part_dont_merge_into_the_bus():
    # 8 data nets {U1,U2} form one bus; 2 control nets {U1,J2} are a separate,
    # sub-width group and are not part of it.
    buses = detect_buses(_ic_board(8, ctrl=2))
    assert len(buses) == 1
    assert buses[0].endpoints == frozenset({"U1", "U2"})
    assert all(n.startswith("D") for n in buses[0].nets)


def test_detect_buses_is_deterministic():
    a = detect_buses(_ic_board(8))
    b = detect_buses(_ic_board(8))
    assert [g.nets for g in a] == [g.nets for g in b]


# --------------------------- canvas data model ----------------------------

def test_canvas_bus_model_add_and_query():
    cv = SchematicCanvas()
    cv.add_buses([BusSegment(0, 0, 1000, 0, sheet="main"),
                  BusSegment(0, 0, 0, 800, sheet="other")])
    cv.add_bus_entries([BusEntry(1000, 0, 1050, 50, sheet="main", net="D0")])
    assert len(cv.buses_on("main")) == 1
    assert len(cv.buses_on("other")) == 1
    assert len(cv.bus_entries_on("main")) == 1
    assert cv.bus_entries_on("main")[0].net == "D0"
    assert cv.buses[0].length() == 1000


def test_canvas_to_dict_includes_buses():
    cv = SchematicCanvas()
    cv.add_buses([BusSegment(0, 0, 100, 0)])
    cv.add_bus_entries([BusEntry(100, 0, 150, 50, net="D3")])
    d = cv.to_dict()
    assert d["buses"] == [{"x1": 0, "y1": 0, "x2": 100, "y2": 0,
                           "sheet": "main"}]
    assert d["bus_entries"][0]["net"] == "D3"


# ----------------------------- bus geometry -------------------------------

def _ic_instance(n=4, side_orient=0, ix=1000, iy=1000):
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox
    from eda_agent.design.canvas import SymbolInstance
    pins = tuple(
        SymbolPin(designator=str(i + 1), name=f"D{i}", x=200, y=300 - i * 100,
                  orientation=side_orient, length=100, electrical_type="io")
        for i in range(n))
    sym = SymbolModel(lib_path=_LIB, lib_ref="MCU", pins=pins,
                      body_bbox=SymbolBBox(x_min=-200, y_min=-350,
                                           x_max=200, y_max=350))
    return SymbolInstance(refdes="U1", symbol=sym, x=ix, y=iy, rotation=0)


def _bus_plan_for_geom(n=4):
    parts = [{"refdes": "U1", "lib_ref": "MCU", "lib_path": _LIB,
              "status": "existing", "sheet": "main"},
             {"refdes": "U2", "lib_ref": "MEM", "lib_path": _LIB,
              "status": "existing", "sheet": "main"}]
    nets = [{"name": f"D{i}", "pins": [
        {"refdes": "U1", "pin": str(i + 1)},
        {"refdes": "U2", "pin": str(i + 1)}]} for i in range(n)]
    return _make_plan(parts, nets)


def test_build_bus_geometry_right_side():
    from eda_agent.design.buses import build_bus_geometry
    plan = _bus_plan_for_geom(4)
    bus = detect_buses(plan)[0]
    inst = _ic_instance(4, side_orient=0)
    geo = build_bus_geometry(bus, inst, plan, "main")
    assert geo is not None
    assert len(geo.stubs) == 4 and len(geo.entries) == 4
    assert len(geo.labels) == 4 and len(geo.segments) == 1
    # every entry is a true 45-degree segment
    for e in geo.entries:
        assert abs(e.x2 - e.x1) == abs(e.y2 - e.y1) != 0
    # the bus line is a single straight (here vertical) segment
    seg = geo.segments[0]
    assert seg.x1 == seg.x2        # vertical bus for a right-facing pin column
    # entries land ON the bus line's x
    for e in geo.entries:
        assert e.x2 == seg.x1
    # labels carry the member-net identities (connectivity)
    assert {l.text for l in geo.labels} == {f"D{i}" for i in range(4)}


def test_build_bus_geometry_needs_two_pins():
    from eda_agent.design.buses import build_bus_geometry
    plan = _bus_plan_for_geom(4)
    bus = detect_buses(plan)[0]
    # an IC that has none of the bus's pins -> no geometry
    inst = _ic_instance(4, side_orient=0)
    object.__setattr__(inst, "refdes", "U9")  # not on any bus net
    assert build_bus_geometry(bus, inst, plan, "main") is None


# --------------------------- apply_bus_drawing ----------------------------

def _two_ic_canvas(width=8):
    """A canvas with U1 (right pins) and U2 (left pins) and per-pin labels for
    the bus nets, simulating the wiring's label-per-pin output."""
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox
    from eda_agent.design.canvas import SchematicCanvas, SymbolInstance, NetLabel

    def ic(refdes, orient, ix):
        pins = tuple(SymbolPin(designator=str(i + 1), name=f"D{i}", x=200,
                               y=400 - i * 100, orientation=orient, length=100,
                               electrical_type="io") for i in range(width))
        if orient == 2:  # left side: mirror x
            pins = tuple(SymbolPin(designator=p.designator, name=p.name,
                                   x=-200, y=p.y, orientation=2, length=100,
                                   electrical_type="io") for p in pins)
        sym = SymbolModel(lib_path=_LIB, lib_ref=refdes, pins=pins,
                          body_bbox=SymbolBBox(x_min=-200, y_min=-450,
                                               x_max=200, y_max=450))
        return SymbolInstance(refdes=refdes, symbol=sym, x=ix, y=1500,
                              rotation=0)

    cv = SchematicCanvas()
    u1 = ic("U1", 0, 1000)
    u2 = ic("U2", 2, 3000)
    cv.add_instance(u1)
    cv.add_instance(u2)
    plan = _bus_plan_for_geom(width)
    # label-per-pin: a NetLabel at each bus pin on each IC
    for inst in (u1, u2):
        for i in range(width):
            ep = inst.pin_world(str(i + 1))
            cv.add_labels([NetLabel(text=f"D{i}", x=ep.x, y=ep.y,
                                    orientation=0)])
    return cv, plan


def test_apply_bus_drawing_replaces_labels_with_bus():
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _two_ic_canvas(8)
    assert len(cv.labels) == 16          # 8 per IC, label-per-pin
    drawn = apply_bus_drawing(cv, plan)
    assert sorted(drawn) == [f"D{i}" for i in range(8)]
    # a bus line + entries now exist (one stub per IC = 16 entries / 16 stubs)
    assert len(cv.buses) == 2            # one bus line per endpoint IC
    assert len(cv.bus_entries) == 16
    # labels: one per pin (connectivity) plus a bus name on each stub.
    texts = {l.text for l in cv.labels}
    assert {f"D{i}" for i in range(8)} <= texts
    assert "D[0..7]" in texts
    assert len(cv.labels) == 18          # 16 per-pin + 2 bus names (one/stub)


def test_apply_bus_drawing_noop_without_bus():
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _two_ic_canvas(3)         # 3 < min_width -> not a bus
    before = (len(cv.labels), len(cv.buses))
    assert apply_bus_drawing(cv, plan) == []
    assert (len(cv.labels), len(cv.buses)) == before


def test_apply_bus_drawing_crossing_gate_does_not_regress():
    from eda_agent.design.buses import apply_bus_drawing
    from eda_agent.design.quality import score_canvas
    cv, plan = _two_ic_canvas(8)
    before = score_canvas(cv, plan).wire_crossings
    apply_bus_drawing(cv, plan, gate_crossings=True)
    assert score_canvas(cv, plan).wire_crossings <= before


# --------------------------- emit + SVG -----------------------------------

class _RecBridge:
    def __init__(self):
        self.calls = []

    def send_command(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"ok": True}


def test_emit_buses_and_entries_call_bridge():
    from eda_agent.design.emitter import (
        EmitResult, _emit_buses, _emit_bus_entries)
    fb = _RecBridge()
    res = EmitResult()
    _emit_buses([BusSegment(0, 0, 0, 800, sheet="main")], fb, res, "main")
    _emit_bus_entries([BusEntry(0, 0, 50, 50, sheet="main", net="D0")],
                      fb, res, "main")
    assert res.buses_emitted == 1 and res.bus_entries_emitted == 1
    cmds = [c for c, _ in fb.calls]
    assert "generic.place_bus" in cmds
    assert "generic.place_bus_entry" in cmds
    bus_params = next(p for c, p in fb.calls if c == "generic.place_bus")
    assert bus_params == {"x1": "0", "y1": "0", "x2": "0", "y2": "800"}


def test_emit_buses_records_failure_note_and_stops():
    from eda_agent.design.emitter import EmitResult, _emit_buses

    class _FailBridge:
        def send_command(self, command, params=None, timeout=None):
            raise RuntimeError("boom")

    res = EmitResult()
    _emit_buses([BusSegment(0, 0, 0, 100), BusSegment(0, 0, 0, 200)],
                _FailBridge(), res, "main")
    assert res.buses_emitted == 0
    assert len(res.notes) == 1          # stops after the first failure


def test_render_svg_includes_bus_and_entry():
    from eda_agent.design.render_svg import render_canvas_svg
    from eda_agent.design.canvas import Sheet as CSheet
    cv = SchematicCanvas()
    cv.add_sheet(CSheet(name="main", size="A4"))
    cv.add_buses([BusSegment(1000, 1000, 1000, 2000, sheet="main")])
    cv.add_bus_entries([BusEntry(1000, 1500, 1050, 1550, sheet="main",
                                 net="D0")])
    svg = render_canvas_svg(cv)
    assert 'stroke-width="4"' in svg          # the thick bus line
    assert "0033aa" in svg.lower()            # bus stroke colour
    assert "<title>D0</title>" in svg         # entry carries its net


def test_apply_bus_drawing_reverts_when_bus_line_crosses_a_wire():
    """A bus LINE crossing an unrelated wire is invisible to the wire-only
    crossing count, so the hardened gate counts bus-segment crossings and
    falls back to per-pin labels rather than draw a bus through a wire."""
    from eda_agent.design.buses import apply_bus_drawing
    from eda_agent.design.canvas import WireSegment
    cv, plan = _two_ic_canvas(8)
    # a horizontal wire sweeping across the gap at mid-height -- the vertical
    # bus stub(s) would cut straight through it.
    cv.add_wires([WireSegment(1500, 1500, 2500, 1500, sheet="main",
                              net="OTHER")])
    drawn = apply_bus_drawing(cv, plan)
    assert drawn == []                       # reverted
    assert len(cv.buses) == 0                # no bus drawn
    assert any(w.net == "OTHER" for w in cv.wires)   # everything restored
    # with the gate OFF, the bus IS drawn (proves the wire is what's blocking it)
    cv2, plan2 = _two_ic_canvas(8)
    cv2.add_wires([WireSegment(1500, 1500, 2500, 1500, sheet="main",
                               net="OTHER")])
    assert apply_bus_drawing(cv2, plan2, gate_crossings=False) != []


# ----------------------------- bus name label -----------------------------

def test_bus_name_bracket_notation():
    from eda_agent.design.buses import bus_name
    assert bus_name([f"D{i}" for i in range(8)]) == "D[0..7]"
    assert bus_name(["ADDR0", "ADDR1", "ADDR2"]) == "ADDR[0..2]"
    # non-contiguous -> None (D[0..3] must not imply the missing D2)
    assert bus_name(["D0", "D1", "D3"]) is None
    # mixed prefixes -> None
    assert bus_name(["D0", "A1"]) is None
    # no numeric suffix -> None
    assert bus_name(["SDA", "SCL"]) is None


def test_build_bus_geometry_sets_bus_label():
    from eda_agent.design.buses import build_bus_geometry
    plan = _bus_plan_for_geom(4)
    bus = detect_buses(plan)[0]
    geo = build_bus_geometry(bus, _ic_instance(4, side_orient=0), plan, "main")
    assert geo.bus_label is not None
    assert geo.bus_label.text == "D[0..3]"
    # the per-pin labels are unchanged (4, separate from the bus name)
    assert len(geo.labels) == 4


def test_apply_bus_drawing_adds_bus_name_label():
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _two_ic_canvas(8)
    apply_bus_drawing(cv, plan)
    texts = {l.text for l in cv.labels}
    assert "D[0..7]" in texts                 # named bus
    assert {f"D{i}" for i in range(8)} <= texts   # plus per-signal labels


# ------------------ a member the comb cannot draw everywhere ---------------

def _canvas_with_a_stray_bus_pin(stray_at=("U2",)):
    """Eight-member bus where D7 leaves the named ICs from the TOP.

    That is ordinary hardware: a connector or MCU rarely brings a whole group
    out of one side. build_bus_geometry keeps only the dominant pin column, so
    D7 is missing from the comb at every IC named in ``stray_at``.
    """
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox
    from eda_agent.design.canvas import SchematicCanvas, SymbolInstance, NetLabel

    def ic(refdes, orient, ix, stray):
        pins = []
        for i in range(8):
            if i == 7 and stray:
                pins.append(SymbolPin(
                    designator="8", name="D7", x=0, y=550,
                    orientation=1, length=100, electrical_type="io"))
                continue
            pins.append(SymbolPin(
                designator=str(i + 1), name=f"D{i}",
                x=200 if orient == 0 else -200, y=400 - i * 100,
                orientation=orient, length=100, electrical_type="io"))
        sym = SymbolModel(lib_path=_LIB, lib_ref=refdes, pins=tuple(pins),
                          body_bbox=SymbolBBox(x_min=-200, y_min=-450,
                                               x_max=200, y_max=450))
        return SymbolInstance(refdes=refdes, symbol=sym, x=ix, y=1500,
                              rotation=0)

    cv = SchematicCanvas()
    u1 = ic("U1", 0, 1000, "U1" in stray_at)
    u2 = ic("U2", 2, 3000, "U2" in stray_at)
    cv.add_instance(u1)
    cv.add_instance(u2)
    plan = _bus_plan_for_geom(8)
    for inst in (u1, u2):
        for i in range(8):
            ep = inst.pin_world(str(i + 1))
            cv.add_labels([NetLabel(text=f"D{i}", x=ep.x, y=ep.y,
                                    orientation=0)])
    return cv, plan


def test_a_member_missing_from_every_comb_is_not_erased():
    """The netlist must survive a bus that can only draw part of itself.

    apply_bus_drawing deletes every wire and label belonging to the nets it is
    about to redraw. The redraw keeps one dominant pin column per endpoint, so
    a member whose pin points elsewhere at BOTH ends used to be deleted and
    never drawn back: no wire, no label, no port anywhere, and the pipeline
    shipped the sheet with ok=True while warning that the net was disconnected.

    Found on a public XIAO carrier board, where net TX left both the MCU and
    the header on a different side from the other five members of its group.
    """
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _canvas_with_a_stray_bus_pin(stray_at=("U1", "U2"))
    apply_bus_drawing(cv, plan)

    represented = ({w.net for w in cv.wires} | {l.text for l in cv.labels}
                   | {p.text for p in cv.power_ports})
    orphaned = [n.name for n in plan.nets if n.name not in represented]
    assert orphaned == [], (
        f"nets {orphaned} lost every wire, label and port to the bus drawing")


@pytest.mark.parametrize("stray_at", [("U2",), ("U1", "U2")])
def test_a_stray_member_stays_connected_at_both_of_its_pins(stray_at):
    """Representation is not enough: BOTH pins have to be on the net.

    With the member missing from one comb only, the surviving endpoint keeps a
    label, so the net still counts as represented while its other pin floats.
    That is the case the orphan check above cannot see.
    """
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _canvas_with_a_stray_bus_pin(stray_at=stray_at)
    apply_bus_drawing(cv, plan)

    d7_pins = [(pr.refdes, pr.pin)
               for n in plan.nets if n.name == "D7" for pr in n.pins]
    assert len(d7_pins) == 2
    for refdes, pin in d7_pins:
        ep = cv.instance_by_refdes(refdes).pin_world(pin)
        on_wire = any(
            (w.x1, w.y1) == (ep.x, ep.y) or (w.x2, w.y2) == (ep.x, ep.y)
            for w in cv.wires if w.net == "D7")
        on_label = any(l.text == "D7" and (l.x, l.y) == (ep.x, ep.y)
                       for l in cv.labels)
        assert on_wire or on_label, (
            f"D7 pin {refdes}.{pin} has neither a wire end nor a label on it")


@pytest.mark.parametrize("stray_at", [("U2",), ("U1", "U2")])
def test_the_bus_is_still_drawn_for_the_members_that_fit(stray_at):
    """The fix must not answer "draw no buses".

    Seven of the eight members are on the dominant column at both ends, which
    is still a bus worth drawing.
    """
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _canvas_with_a_stray_bus_pin(stray_at=stray_at)
    drawn = apply_bus_drawing(cv, plan)
    assert len(drawn) >= 2, "the drawable members were dropped along with D7"
    assert cv.buses, "no bus line was drawn at all"
    assert "D7" not in drawn


# ------------- a member the comb reaches at only some of its pins ----------

def _canvas_where_a_member_reaches_one_ic_twice():
    """D7 lands on U1 twice: pin 8 in the column, pin 9 on the far side.

    Ordinary hardware: a passthrough, a series element, or a buffer with its
    input and output on one net. The comb keyed one pin per net, so pin 9 was
    never drawn while D7's own wiring had already been erased.
    """
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox
    from eda_agent.design.canvas import SchematicCanvas, SymbolInstance, NetLabel

    def ic(refdes, orient, ix, extra_pin):
        pins = [SymbolPin(designator=str(i + 1), name=f"D{i}",
                          x=200 if orient == 0 else -200, y=400 - i * 100,
                          orientation=orient, length=100,
                          electrical_type="io") for i in range(8)]
        if extra_pin:
            pins.append(SymbolPin(designator="9", name="D7B",
                                  x=-200 if orient == 0 else 200, y=0,
                                  orientation=2 if orient == 0 else 0,
                                  length=100, electrical_type="io"))
        sym = SymbolModel(lib_path=_LIB, lib_ref=refdes, pins=tuple(pins),
                          body_bbox=SymbolBBox(x_min=-200, y_min=-450,
                                               x_max=200, y_max=450))
        return SymbolInstance(refdes=refdes, symbol=sym, x=ix, y=1500,
                              rotation=0)

    cv = SchematicCanvas()
    u1 = ic("U1", 0, 1000, True)
    u2 = ic("U2", 2, 3000, False)
    cv.add_instance(u1)
    cv.add_instance(u2)

    parts = [{"refdes": "U1", "lib_ref": "MCU", "lib_path": _LIB,
              "status": "existing", "sheet": "main"},
             {"refdes": "U2", "lib_ref": "MEM", "lib_path": _LIB,
              "status": "existing", "sheet": "main"}]
    nets = []
    for i in range(8):
        pins = [{"refdes": "U1", "pin": str(i + 1)},
                {"refdes": "U2", "pin": str(i + 1)}]
        if i == 7:
            pins.append({"refdes": "U1", "pin": "9"})
        nets.append({"name": f"D{i}", "pins": pins})
    plan = _make_plan(parts, nets)

    for inst in (u1, u2):
        for pin in [str(i + 1) for i in range(8)] + (["9"] if inst is u1
                                                     else []):
            ep = inst.pin_world(pin)
            text = "D7" if pin == "9" else f"D{int(pin) - 1}"
            cv.add_labels([NetLabel(text=text, x=ep.x, y=ep.y,
                                    orientation=0)])
    return cv, plan


def test_a_second_pin_of_a_member_on_the_same_ic_is_not_left_bare():
    """Coverage is per PIN, not per net.

    build_bus_geometry used to key one pin per net per IC, so a member
    reaching that IC twice was combed once. The net still carried labels, so
    every net-level check read clean while the second pin sat on nothing.
    Found on a public HDMI/GPDI board: four differential nets each landing on
    the level shifter twice.
    """
    from eda_agent.design.buses import apply_bus_drawing
    cv, plan = _canvas_where_a_member_reaches_one_ic_twice()
    apply_bus_drawing(cv, plan)

    for pr in [p for n in plan.nets if n.name == "D7" for p in n.pins]:
        ep = cv.instance_by_refdes(pr.refdes).pin_world(pr.pin)
        on_wire = any(
            (w.x1, w.y1) == (ep.x, ep.y) or (w.x2, w.y2) == (ep.x, ep.y)
            for w in cv.wires if w.net == "D7")
        on_label = any(l.text == "D7" and (l.x, l.y) == (ep.x, ep.y)
                       for l in cv.labels)
        assert on_wire or on_label, (
            f"D7 pin {pr.refdes}.{pr.pin} has neither a wire end nor a label")


def test_a_member_touching_a_part_that_is_not_an_endpoint_is_not_erased():
    """The comb only visits the bus's endpoint ICs.

    A member that also reaches a series resistor or a test point has a pin no
    comb will ever draw, so its original wiring has to stay.
    """
    from eda_agent.design.buses import apply_bus_drawing
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox
    from eda_agent.design.canvas import SymbolInstance, NetLabel

    cv, plan = _canvas_with_a_stray_bus_pin(stray_at=())
    r_sym = SymbolModel(
        lib_path=_LIB, lib_ref="R",
        pins=(SymbolPin(designator="1", name="1", x=0, y=100,
                        orientation=1, length=100, electrical_type="passive"),
              SymbolPin(designator="2", name="2", x=0, y=-100,
                        orientation=3, length=100, electrical_type="passive")),
        body_bbox=SymbolBBox(x_min=-50, y_min=-100, x_max=50, y_max=100))
    cv.add_instance(SymbolInstance(refdes="R1", symbol=r_sym, x=2000, y=2600,
                                   rotation=0))
    plan = plan.model_copy(deep=True)
    plan.parts.append(type(plan.parts[0])(
        refdes="R1", lib_ref="R", lib_path=_LIB, status="existing",
        sheet="main"))
    d3 = next(n for n in plan.nets if n.name == "D3")
    d3.pins.append(type(d3.pins[0])(refdes="R1", pin="1"))
    ep = cv.instance_by_refdes("R1").pin_world("1")
    cv.add_labels([NetLabel(text="D3", x=ep.x, y=ep.y, orientation=0)])

    apply_bus_drawing(cv, plan)
    on_label = any(l.text == "D3" and (l.x, l.y) == (ep.x, ep.y)
                   for l in cv.labels)
    on_wire = any((w.x1, w.y1) == (ep.x, ep.y) or (w.x2, w.y2) == (ep.x, ep.y)
                  for w in cv.wires if w.net == "D3")
    assert on_label or on_wire, (
        "D3's pin on R1 lost its label to a comb that never draws R1")


def test_both_pins_of_a_member_on_the_dominant_side_are_combed():
    """Drawing every pin is what KEEPS such a member in the bus.

    The per-pin coverage rule alone would keep the sheet correct by dropping
    the member from the comb and leaving its own wiring in place. That is safe
    but poorer: with both of its pins in the column there is a perfectly good
    comb to draw, and keying one pin per net threw it away. This is the test
    that fails if build_bus_geometry goes back to one pin per net per IC.
    """
    from eda_agent.design.buses import apply_bus_drawing
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox
    from eda_agent.design.canvas import SchematicCanvas, SymbolInstance

    def ic(refdes, orient, ix, extra):
        pins = [SymbolPin(designator=str(i + 1), name=f"D{i}",
                          x=200 if orient == 0 else -200, y=400 - i * 100,
                          orientation=orient, length=100,
                          electrical_type="io") for i in range(8)]
        if extra:
            # same side, same column: a second tap on D7
            pins.append(SymbolPin(designator="9", name="D7B",
                                  x=200 if orient == 0 else -200, y=-500,
                                  orientation=orient, length=100,
                                  electrical_type="io"))
        sym = SymbolModel(lib_path=_LIB, lib_ref=refdes, pins=tuple(pins),
                          body_bbox=SymbolBBox(x_min=-200, y_min=-600,
                                               x_max=200, y_max=450))
        return SymbolInstance(refdes=refdes, symbol=sym, x=ix, y=1500,
                              rotation=0)

    cv = SchematicCanvas()
    cv.add_instance(ic("U1", 0, 1000, True))
    cv.add_instance(ic("U2", 2, 3000, False))
    parts = [{"refdes": "U1", "lib_ref": "MCU", "lib_path": _LIB,
              "status": "existing", "sheet": "main"},
             {"refdes": "U2", "lib_ref": "MEM", "lib_path": _LIB,
              "status": "existing", "sheet": "main"}]
    nets = []
    for i in range(8):
        pins = [{"refdes": "U1", "pin": str(i + 1)},
                {"refdes": "U2", "pin": str(i + 1)}]
        if i == 7:
            pins.append({"refdes": "U1", "pin": "9"})
        nets.append({"name": f"D{i}", "pins": pins})
    plan = _make_plan(parts, nets)

    drawn = apply_bus_drawing(cv, plan)
    assert "D7" in drawn, (
        "D7 has both of its U1 pins in the bus column, so it belongs in the "
        "comb; keying one pin per net drops it")
    u1_d7_entries = [e for e in cv.bus_entries if e.net == "D7"]
    assert len(u1_d7_entries) >= 3, (
        f"D7 has three pins and got {len(u1_d7_entries)} bus entries")
