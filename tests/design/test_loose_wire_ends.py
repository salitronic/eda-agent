# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wire that ends in empty space is cut back, and nothing connected is.

Every pin is stubbed before its net is routed, and a route that joins at
the pin itself, or branches off part way along the stub, leaves the rest
of the stub pointing at nothing. MEASURED before the fix, counting wire
ends that touch nothing: blinker555 drew 4 on its base layout, and buck
and mcu 3 each on their selected layouts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agent.design import pipeline
from eda_agent.design.benchmark import SyntheticSymbolExtractor
from eda_agent.design.canvas import (
    Junction,
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin

PLANS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"

# One pin at the symbol origin, pointing right, 100 mils long, so an
# instance at (x, y) has its wire end at (x + 100, y).
_ONE_PIN = SymbolModel(
    lib_path="test.SchLib",
    lib_ref="ONE_PIN",
    pins=(SymbolPin(designator="1", name="1", x=0, y=0, orientation=0,
                    length=100, electrical_type="passive"),),
    body_bbox=SymbolBBox(x_min=-200, y_min=-100, x_max=0, y_max=100),
)


def _canvas(*parts: tuple[str, int, int]) -> SchematicCanvas:
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    for refdes, x, y in parts:
        canvas.add_instance(SymbolInstance(
            refdes=refdes, symbol=_ONE_PIN, x=x, y=y, rotation=0))
    return canvas


def _end(canvas: SchematicCanvas, refdes: str) -> tuple[int, int]:
    ep = canvas.pin_world(refdes, "1")
    return ep.x, ep.y


def test_a_stub_that_leads_nowhere_is_removed():
    canvas = _canvas(("R1", 0, 0))
    ex, ey = _end(canvas, "R1")
    canvas.add_wires([WireSegment(ex, ey, ex + 300, ey, net="N")])

    assert pipeline._trim_dangling_wires(canvas, "main") == 1
    assert canvas.wires == []


def test_the_tail_past_a_branch_is_cut_back_and_its_dot_goes():
    """R1's stub runs 600 mils; R2's route joins it 200 mils along.

    The shape the BOOT stub had on the TPS54331 demo buck: the route to the
    bootstrap cap branched off the stub, and the rest of the stub carried on
    past the branch. The dot at the branch becomes a corner once the tail is
    gone, so it must go too.
    """
    canvas = _canvas(("R1", 0, 0), ("R2", 200, -400))
    ax, ay = _end(canvas, "R1")
    bx, by = _end(canvas, "R2")
    canvas.add_wires([
        WireSegment(ax, ay, ax + 600, ay, net="N"),
        WireSegment(bx, by, bx, ay, net="N"),
    ])
    canvas.add_junctions([Junction(bx, ay)])

    assert pipeline._trim_dangling_wires(canvas, "main") == 1
    assert WireSegment(ax, ay, bx, ay, net="N") in canvas.wires
    assert WireSegment(bx, by, bx, ay, net="N") in canvas.wires
    assert canvas.junctions == []


@pytest.mark.parametrize("anchor", ["label", "port"])
def test_a_stub_that_carries_a_label_or_port_is_kept(anchor):
    canvas = _canvas(("R1", 0, 0))
    ex, ey = _end(canvas, "R1")
    stub = WireSegment(ex, ey, ex + 300, ey, net="N")
    canvas.add_wires([stub])
    if anchor == "label":
        canvas.add_labels([NetLabel(text="N", x=ex + 300, y=ey, orientation=0)])
    else:
        canvas.add_power_ports([
            PowerPort(text="N", x=ex + 300, y=ey, style="bar")])

    assert pipeline._trim_dangling_wires(canvas, "main") == 0
    assert canvas.wires == [stub]


def test_a_run_of_unused_segments_goes_whole():
    canvas = _canvas(("R1", 0, 0))
    ex, ey = _end(canvas, "R1")
    canvas.add_wires([
        WireSegment(ex, ey, ex + 300, ey, net="N"),
        WireSegment(ex + 300, ey, ex + 300, ey + 300, net="N"),
    ])

    assert pipeline._trim_dangling_wires(canvas, "main") == 2
    assert canvas.wires == []


def test_a_stub_crossed_at_a_dot_stops_at_the_dot():
    """A junction dot is a connection even where no wire ends.

    MEASURED on the buck benchmark: J1.1's stub was crossed by a VIN wire at
    a dot, with neither wire ending there, and the stub's far end touched
    nothing. Counting wire ends alone removed the whole stub and cut J1.1 off
    its rail. The stub must be cut back to the dot, and the dot must stay.
    """
    canvas = _canvas(("R1", 0, 0), ("R2", 200, -400))
    ax, ay = _end(canvas, "R1")
    bx, by = _end(canvas, "R2")
    stub = WireSegment(ax, ay, ax + 300, ay, net="N")
    cross = WireSegment(bx, by, bx, ay + 400, net="N")
    canvas.add_wires([stub, cross])
    canvas.add_power_ports([PowerPort(text="N", x=bx, y=ay + 400, style="bar")])
    canvas.add_junctions([Junction(bx, ay)])

    assert pipeline._trim_dangling_wires(canvas, "main") == 1
    assert canvas.wires == [WireSegment(ax, ay, bx, ay, net="N"), cross]
    assert canvas.junctions == [Junction(bx, ay)]


def test_connected_wire_and_a_real_junction_are_left_alone():
    """A loose stub elsewhere forces the dot recount; the true T must survive it."""
    canvas = _canvas(
        ("R1", 0, 0), ("R2", 800, 0), ("R3", 300, -400), ("R4", 0, -1000))
    a, b = _end(canvas, "R1"), _end(canvas, "R2")
    c, d = _end(canvas, "R3"), _end(canvas, "R4")
    through = WireSegment(a[0], a[1], b[0], b[1], net="N")
    branch = WireSegment(c[0], c[1], c[0], a[1], net="N")
    canvas.add_wires([
        through, branch, WireSegment(d[0], d[1], d[0] + 300, d[1], net="M")])
    canvas.add_junctions([Junction(c[0], a[1])])

    assert pipeline._trim_dangling_wires(canvas, "main") == 1
    assert canvas.wires == [through, branch]
    assert canvas.junctions == [Junction(c[0], a[1])]


def _loose_ends(canvas: SchematicCanvas) -> list:
    """Wire ends that touch no pin, label, port, bus entry or other wire.

    Written apart from the pass under test, so it cannot share a mistake
    with it.
    """
    found = []
    for sheet in {s.name for s in canvas.sheets}:
        wires = canvas.wires_on(sheet)
        touch = {(e.x, e.y) for i in canvas.instances_on(sheet)
                 for e in i.all_pin_endpoints()}
        touch |= {(lab.x, lab.y) for lab in canvas.labels_on(sheet)}
        touch |= {(p.x, p.y) for p in canvas.power_ports_on(sheet)}
        for be in canvas.bus_entries_on(sheet):
            touch |= {(be.x1, be.y1), (be.x2, be.y2)}
        for n, w in enumerate(wires):
            for px, py in ((w.x1, w.y1), (w.x2, w.y2)):
                if (px, py) in touch:
                    continue
                if any(
                    (o.x1 == o.x2 == px
                     and min(o.y1, o.y2) <= py <= max(o.y1, o.y2))
                    or (o.y1 == o.y2 == py
                        and min(o.x1, o.x2) <= px <= max(o.x1, o.x2))
                    for m, o in enumerate(wires) if m != n
                ):
                    continue
                found.append((sheet, w.net, (px, py)))
    return found


def _plans() -> list[DesignPlan]:
    return [
        DesignPlan.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(PLANS_DIR.glob("*.json"))
    ]


def test_no_benchmark_plan_draws_a_wire_to_nowhere():
    plans = _plans()
    assert len(plans) >= 3, f"benchmark plans not found under {PLANS_DIR}"
    for plan in plans:
        result = pipeline.build_best_canvas_from_plan(
            plan, SyntheticSymbolExtractor(plan))
        assert result.canvas.wires, "the layout drew no wire at all"
        loose = _loose_ends(result.canvas)
        assert loose == [], (
            f"{len(loose)} wire end(s) touching nothing: {loose[:4]}")


def test_the_benchmark_plans_leave_unused_stubs_when_nothing_cuts_them(
        monkeypatch):
    """The guard above is only worth something while the hazard is real.

    With the pass disabled the same plans must still produce loose ends. If a
    later change stops the stub pass creating them, this fails and says the
    guard is testing nothing.
    """
    monkeypatch.setattr(
        pipeline, "_trim_dangling_wires", lambda canvas, sheet_name: 0)
    loose = 0
    for plan in _plans():
        result = pipeline.build_best_canvas_from_plan(
            plan, SyntheticSymbolExtractor(plan))
        loose += len(_loose_ends(result.canvas))
    assert loose > 0


def test_trimming_does_not_change_which_layout_wins(monkeypatch):
    """Wire is cut on the winner only, never inside the candidate builds.

    MEASURED with the full sweep: trimming inside every candidate changed
    the scores the selection compares, and changed the winner on the buck
    benchmark and on the 555 test board. Both came out worse: buck's score
    went from 710 to 747, and the 555 went from 1 wire crossing to 6.
    """
    def placements():
        return [
            sorted((i.refdes, i.x, i.y, i.rotation)
                   for i in pipeline.build_best_canvas_from_plan(
                       plan, SyntheticSymbolExtractor(plan)).canvas.instances)
            for plan in _plans()
        ]

    trimmed = placements()
    monkeypatch.setattr(
        pipeline, "_trim_dangling_wires", lambda canvas, sheet_name: 0)
    assert trimmed == placements()


@pytest.mark.parametrize(
    "builder", ["build_canvas_from_plan", "build_best_canvas_from_plan"])
def test_trimming_never_changes_what_is_connected(builder, monkeypatch):
    """Cutting wire must not change the netlist the drawing carries.

    Each layout is built with the pass disabled and then trimmed, so both
    netlists come from one placement and any difference is the pass's doing.
    Both are read back by the geometric net solver, which is written apart
    from the pipeline. This is the check that caught a stub crossed at a
    junction dot being removed on the buck benchmark.
    """
    import copy

    from eda_agent.fileio.netlist_solver import solve_nets

    real = pipeline._trim_dangling_wires
    monkeypatch.setattr(
        pipeline, "_trim_dangling_wires", lambda canvas, sheet_name: 0)

    def groups(plan, canvas):
        pins = []
        for net in plan.nets:
            for ref in net.pins:
                ep = canvas.pin_world(ref.refdes, str(ref.pin))
                if ep is not None:
                    pins.append({"component": ref.refdes, "pin": str(ref.pin),
                                 "x": ep.x, "y": ep.y})
        solved = solve_nets(
            pins,
            [{"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2}
             for w in canvas.wires],
            [{"x": p.x, "y": p.y, "name": p.text} for p in canvas.power_ports],
            [{"x": j.x, "y": j.y} for j in canvas.junctions],
            [{"x": lb.x, "y": lb.y, "name": lb.text} for lb in canvas.labels])
        by_net: dict[str, set] = {}
        for pin_id, net in solved["pin_nets"].items():
            by_net.setdefault(net, set()).add(pin_id)
        return {frozenset(v) for v in by_net.values()}

    cut = 0
    for plan in _plans():
        untrimmed = getattr(pipeline, builder)(
            plan, SyntheticSymbolExtractor(plan)).canvas
        trimmed = copy.deepcopy(untrimmed)
        cut += sum(real(trimmed, s.name) for s in trimmed.sheets)
        assert groups(plan, trimmed) == groups(plan, untrimmed), (
            f"{builder}: trimming changed the netlist a benchmark plan draws")
    assert cut > 0, "no wire was cut, so this compared nothing"
