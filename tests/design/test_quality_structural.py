# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The score can see conventions, not just tidiness.

The six original features are global scalars: crossings, wire length,
aspect ratio, counts. None of them can tell whether a bypass cap sits
beside the IC it bypasses, or whether signal runs left to right. A
Bradley-Terry model fitted to 312 comparisons over those six scored
58.0% on a binary choice, which is what fitting noise looks like.

These features are structural and local. They earn their place in the
deterministic scorer whether or not anything is ever trained on them.
"""
from __future__ import annotations

import pytest

from eda_agent.design.canvas import (
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.quality import score_canvas
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


@pytest.fixture(autouse=True)
def _force_heuristic_scorer(monkeypatch, tmp_path):
    from eda_agent.design import quality

    monkeypatch.setenv("EDA_AGENT_QUALITY_MODEL", str(tmp_path / "none.json"))
    quality.reset_model_cache()
    yield
    quality.reset_model_cache()


def _sym(lib_ref="R", pins=None) -> SymbolModel:
    pins = pins or (
        SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                  length=100, electrical_type="passive"),
        SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                  length=100, electrical_type="passive"),
    )
    return SymbolModel(
        lib_path="/x.SchLib", lib_ref=lib_ref, pins=pins,
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30),
    )


def _canvas(*instances, labels=(), ports=()) -> SchematicCanvas:
    c = SchematicCanvas()
    c.add_sheet(Sheet(name="main"))
    for inst in instances:
        c.add_instance(inst)
    if labels:
        c.add_labels(list(labels))
    if ports:
        c.add_power_ports(list(ports))
    return c


def _plan(parts) -> DesignPlan:
    """A minimal plan carrying roles, which is all the scorer reads."""
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": parts,
        # DesignPlan requires a net with at least two pins. The scorer
        # reads only roles, so this exists to satisfy the schema: it
        # joins pin 1 of every part, or both pins of a lone one.
        "nets": [{"name": "N1", "pins": (
            [{"refdes": p["refdes"], "pin": "1"} for p in parts]
            if len(parts) > 1 else
            [{"refdes": parts[0]["refdes"], "pin": "1"},
             {"refdes": parts[0]["refdes"], "pin": "2"}]
        )}],
    })


# ---------------------------------------------------------------------------
# decap_orphans: the most-cited schematic review comment there is.
# ---------------------------------------------------------------------------

def _ic_and_cap(cap_x: int):
    ic = SymbolInstance(refdes="U1", symbol=_sym("IC"), x=1000, y=1000,
                        rotation=0)
    cap = SymbolInstance(refdes="C1", symbol=_sym("C"), x=cap_x, y=1000,
                         rotation=0)
    plan = _plan([
        {"refdes": "U1", "lib_ref": "IC", "lib_path": "/x.SchLib",
         "role": "ic"},
        {"refdes": "C1", "lib_ref": "C", "lib_path": "/x.SchLib",
         "role": "vcc_decoup"},
    ])
    return _canvas(ic, cap), plan


def test_a_bypass_cap_beside_its_ic_is_not_an_orphan():
    canvas, plan = _ic_and_cap(cap_x=1300)
    assert score_canvas(canvas, plan).decap_orphans == 0


def test_a_bypass_cap_across_the_sheet_is_counted():
    canvas, plan = _ic_and_cap(cap_x=6000)
    assert score_canvas(canvas, plan).decap_orphans == 1


def test_the_decap_measure_is_reported_but_does_not_steer():
    """A BOARD rule that does not hold on a SHEET.

    Measured on 801 capacitors across 57 human-drawn KiCad demo sheets:
    caps with decoupling values sit a median 2550 mils from the nearest
    IC, and every other cap sits at 1768. The bypass caps are FURTHER
    away, so proximity does not distinguish them at all and a 600 mil
    rule flagged 96.9% of professionally placed ones.

    pcb_placement enforces the real version of this, where it belongs.
    """
    from eda_agent.design import quality

    assert quality._W_DECAP_ORPHAN == 0.0
    far, plan = _ic_and_cap(cap_x=6000)
    score = score_canvas(far, plan)
    assert score.decap_orphans == 1, "the distance is still reported"
    assert score.breakdown.get("decap_orphans", 0) == 0, "and costs nothing"
    # Comparing TOTALS would prove nothing here: moving the cap also
    # changes wire length and aspect ratio, so the two layouts differ
    # for reasons that have nothing to do with this term.


def test_a_sheet_with_no_ic_has_no_orphans():
    """Nothing to be near is not a violation."""
    cap = SymbolInstance(refdes="C1", symbol=_sym("C"), x=1000, y=1000,
                         rotation=0)
    plan = _plan([{"refdes": "C1", "lib_ref": "C", "lib_path": "/x.SchLib",
                   "role": "vcc_decoup"}])
    assert score_canvas(_canvas(cap), plan).decap_orphans == 0


def test_no_plan_means_no_role_based_findings():
    """score_canvas takes an optional plan; without it, roles are unknown.

    Guessing a role from nothing would be worse than reporting zero.
    """
    cap = SymbolInstance(refdes="C1", symbol=_sym("C"), x=9000, y=9000,
                         rotation=0)
    assert score_canvas(_canvas(cap)).decap_orphans == 0


# ---------------------------------------------------------------------------
# flow_reversals: signal runs left to right.
# ---------------------------------------------------------------------------

def _driver_and_load(load_x: int):
    out = _sym("DRV", pins=(
        SymbolPin(designator="1", name="OUT", x=100, y=0, orientation=0,
                  length=100, electrical_type="output"),
    ))
    inp = _sym("LOAD", pins=(
        SymbolPin(designator="1", name="IN", x=-100, y=0, orientation=2,
                  length=100, electrical_type="input"),
    ))
    canvas = _canvas(
        SymbolInstance(refdes="U1", symbol=out, x=1000, y=1000, rotation=0),
        SymbolInstance(refdes="U2", symbol=inp, x=load_x, y=1000, rotation=0),
    )
    # Paired by NET, not by proximity: the plan says which input this
    # output drives, so there is nothing to guess.
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "DRV", "lib_path": "/x.SchLib"},
            {"refdes": "U2", "lib_ref": "LOAD", "lib_path": "/x.SchLib"},
        ],
        "nets": [{"name": "SIG", "pins": [
            {"refdes": "U1", "pin": "1"}, {"refdes": "U2", "pin": "1"}]}],
    })
    return canvas, plan


def test_an_output_driving_rightward_is_conventional():
    canvas, plan = _driver_and_load(load_x=2000)
    assert score_canvas(canvas, plan).flow_reversals == 0


def test_an_output_driving_leftward_is_a_reversal():
    canvas, plan = _driver_and_load(load_x=200)
    assert score_canvas(canvas, plan).flow_reversals == 1


def test_without_a_plan_there_is_no_net_to_pair_on():
    """Reporting zero beats pairing by proximity.

    The nearest-input guess fired on 36% of outputs across 68 human
    drawn sheets, which was mostly the pairing being wrong rather than
    the layout.
    """
    canvas, _ = _driver_and_load(load_x=200)
    assert score_canvas(canvas).flow_reversals == 0


def test_untyped_pins_contribute_nothing_rather_than_noise():
    """A library that does not set pin types must not be penalised.

    Contributing a guess here would punish every symbol from a library
    that leaves everything passive, which is most generated ones.
    """
    a = SymbolInstance(refdes="R1", symbol=_sym(), x=2000, y=1000, rotation=0)
    b = SymbolInstance(refdes="R2", symbol=_sym(), x=1000, y=1000, rotation=0)
    plan = _plan([
        {"refdes": "R1", "lib_ref": "R", "lib_path": "/x.SchLib"},
        {"refdes": "R2", "lib_ref": "R", "lib_path": "/x.SchLib"},
    ])
    assert score_canvas(_canvas(a, b), plan).flow_reversals == 0


# ---------------------------------------------------------------------------
# label_column_spread: same-net labels line up.
# ---------------------------------------------------------------------------

def _labelled(second_xy):
    inst = SymbolInstance(refdes="R1", symbol=_sym(), x=1000, y=1000,
                          rotation=0)
    labels = (
        NetLabel(text="SDA", x=1000, y=1000, orientation=0),
        NetLabel(text="SDA", x=second_xy[0], y=second_xy[1], orientation=0),
    )
    return _canvas(inst, labels=labels)


@pytest.mark.parametrize("second,spread", [
    ((1000, 2000), 0),   # same column
    ((2000, 1000), 0),   # same row
    ((2000, 2000), 1),   # neither
])
def test_same_net_labels_misalignment_is_measured(second, spread):
    assert score_canvas(_labelled(second)).label_column_spread == spread


def test_label_spread_is_reported_but_does_not_steer():
    """Falsified against 114 human-drawn KiCad demo sheets.

    Only 43 score zero, the median is 2 and the worst is 155. A net
    legitimately appears in several unrelated places, so weighting this
    would penalise exactly what professionals do. It stays visible and
    costs nothing.
    """
    from eda_agent.design import quality

    assert quality._W_LABEL_SPREAD == 0.0
    score = score_canvas(_labelled((2000, 2000)))
    assert score.label_column_spread == 1, "still reported"
    assert score.breakdown.get("label_spread", 0) == 0, "and costs nothing"


def test_a_single_label_cannot_be_misaligned():
    inst = SymbolInstance(refdes="R1", symbol=_sym(), x=1000, y=1000,
                          rotation=0)
    canvas = _canvas(inst, labels=(NetLabel(text="SDA", x=5, y=7,
                                            orientation=0),))
    assert score_canvas(canvas).label_column_spread == 0


# ---------------------------------------------------------------------------
# rail_direction_flips: computed, reported, deliberately not weighted.
# ---------------------------------------------------------------------------

def test_a_ground_glyph_above_its_own_net_is_a_flip():
    """Matched BY NET NAME, so there is nothing to guess.

    MEASURED on 1685 comparable glyph placements across the human-drawn
    KiCad demo sheets: 7.1% violate this. An earlier 4.1% is withdrawn,
    having been taken while the reader left power nets anonymous, so
    only 13 glyphs in the corpus were comparable at all.

    Canvas Y is UP, so the glyph at the LARGER y is the higher one.
    """
    from eda_agent.design import quality

    # The CONVENTION is validated at 7.1% violation across 1685 human
    # placements. The WEIGHT is not: at 150 it outweighed pin-side
    # placement and pulled a cap to the wrong side of its IC. Reported,
    # not steering, until the two conventions are measured in conflict.
    assert quality._W_RAIL_FLIP == 0.0

    inst = SymbolInstance(refdes="R1", symbol=_sym(), x=1000, y=1000,
                          rotation=0)
    plan = _plan([{"refdes": "R1", "lib_ref": "R", "lib_path": "/x.SchLib"}])
    plan.nets[0].name = "GND"

    above = _canvas(inst, ports=(
        PowerPort(text="GND", x=1000, y=5000, style="gnd_power"),))
    below = _canvas(inst, ports=(
        PowerPort(text="GND", x=1000, y=200, style="gnd_power"),))

    assert score_canvas(above, plan).rail_direction_flips == 1
    assert score_canvas(below, plan).rail_direction_flips == 0
    assert score_canvas(above, plan).breakdown["rail_flips"] == 0, (
        "reported but not weighted; see the constant's note")


def test_a_glyph_whose_net_is_absent_is_not_judged():
    """Nothing to be above or below is not a violation."""
    inst = SymbolInstance(refdes="R1", symbol=_sym(), x=1000, y=1000,
                          rotation=0)
    plan = _plan([{"refdes": "R1", "lib_ref": "R", "lib_path": "/x.SchLib"}])
    canvas = _canvas(inst, ports=(
        PowerPort(text="NOT_A_NET", x=1000, y=9000, style="gnd_power"),))
    assert score_canvas(canvas, plan).rail_direction_flips == 0


def test_without_a_plan_the_rail_check_reports_nothing():
    """The old proximity fallback measured its own guess."""
    inst = SymbolInstance(refdes="R1", symbol=_sym(), x=1000, y=1000,
                          rotation=0)
    canvas = _canvas(inst, ports=(
        PowerPort(text="GND", x=1000, y=9000, style="gnd_power"),))
    assert score_canvas(canvas).rail_direction_flips == 0


def test_a_glyph_under_its_own_pin_is_not_a_flip_wherever_the_rail_runs():
    """The convention is local, and a rail is not.

    A power symbol connects globally by name, so GND is ONE net across
    the whole sheet. Judging a glyph against the median pin of all of
    it asks whether the glyph sits above the average of unrelated
    parts, which the convention never claimed: measured on the corpus,
    the rail-median form called 40.6% of human placements violations
    and this one calls 7.1%.

    Here R1 sits high and R2/R3 sit low. The glyph is tucked correctly
    under R1's own pin, and still sits above the net's median.
    """
    high = SymbolInstance(refdes="R1", symbol=_sym(), x=1000, y=9000,
                          rotation=0)
    low1 = SymbolInstance(refdes="R2", symbol=_sym(), x=2000, y=1000,
                          rotation=0)
    low2 = SymbolInstance(refdes="R3", symbol=_sym(), x=3000, y=1000,
                          rotation=0)
    plan = _plan([
        {"refdes": "R1", "lib_ref": "R", "lib_path": "/x.SchLib"},
        {"refdes": "R2", "lib_ref": "R", "lib_path": "/x.SchLib"},
        {"refdes": "R3", "lib_ref": "R", "lib_path": "/x.SchLib"},
    ])
    plan.nets[0].name = "GND"

    pin_y = high.pin_world("1").y
    canvas = _canvas(high, low1, low2, ports=(
        PowerPort(text="GND", x=1000, y=pin_y - 200, style="gnd_power"),))

    assert score_canvas(canvas, plan).rail_direction_flips == 0, (
        "a ground glyph directly under the pin it returns is correct no "
        "matter where the rest of the rail runs")


# ---------------------------------------------------------------------------
# aspect: measured against the sheet, which is not square.
# ---------------------------------------------------------------------------

def _grid(width: int, height: int):
    """Four parts at the corners of a width x height arrangement."""
    return [
        SymbolInstance(refdes=f"R{i+1}", symbol=_sym(), x=x, y=y, rotation=0)
        for i, (x, y) in enumerate(
            [(0, 0), (width, 0), (0, -height), (width, -height)])
    ]


def test_a_landscape_layout_beats_a_square_one_on_a_landscape_sheet():
    """The sheet is the target, and A4 is 11500 x 7600.

    MEASURED on 104 human-drawn sheets: the median is 1.49 times wider
    than tall and only 8.7% are near square, so a square target charged
    professionals a median 0.34 penalty for filling the paper. A4's own
    drawing area is 1.51, so the target comes from the sheet rather
    than from fitting that corpus.
    """
    landscape = score_canvas(_canvas(*_grid(1500, 1000))).breakdown["aspect"]
    square = score_canvas(_canvas(*_grid(1000, 1000))).breakdown["aspect"]
    assert landscape < square, (
        "a layout matching the sheet's proportions must not score worse "
        "than a square one on a landscape sheet")


def test_both_ways_of_missing_the_sheet_ratio_are_penalised():
    """Twice as wide as the paper is as wrong as half as wide.

    A one-sided measure would let the optimiser run away sideways.
    """
    from eda_agent.design.quality import _aspect_ratio_penalty

    class _Box:
        def __init__(self, w, h):
            self.x_min, self.y_min, self.x_max, self.y_max = 0, 0, w, h

    target = 11500 / 7600
    too_wide = _aspect_ratio_penalty([_Box(3000, 1000)], target)
    too_tall = _aspect_ratio_penalty([_Box(1000, 3000)], target)
    on_sheet = _aspect_ratio_penalty([_Box(1151, 760)], target)
    assert on_sheet < 0.01, f"matching the sheet should be free, got {on_sheet}"
    assert too_wide > 0.2 and too_tall > 0.2, (too_wide, too_tall)


def test_a_flat_body_cannot_overlap_anything():
    """A zero-area body covers nothing, whatever its edges say.

    A net tie, or any symbol whose graphics this reader does not
    recognise, gets a bbox of zero height or width. Comparing edges
    alone, such a box lying across a real one satisfies every test and
    scored 1000 points, the band reserved for illegal geometry. Found
    by the human benchmark, which reported the engine overlapping
    bodies on two sheets; the engine had done nothing wrong.
    """
    from eda_agent.design.quality import _count_body_overlaps

    class _Box:
        def __init__(self, x0, y0, x1, y1):
            self.x_min, self.y_min, self.x_max, self.y_max = x0, y0, x1, y1

    real = _Box(1900, 2600, 4300, 5400)
    flat = _Box(3380, 5300, 3420, 5300)      # zero height, inside `real`
    assert _count_body_overlaps([real, flat]) == 0

    # A real overlap must still be caught.
    assert _count_body_overlaps([real, _Box(4000, 5000, 4400, 5600)]) == 1


def test_a_wire_leaving_a_pin_does_not_cross_that_parts_own_body():
    """Some symbols are drawn AROUND their connection point.

    A test point is a small mark centred on its own pin, so every wire
    leaving one crosses its body by construction. Measured on nRF54L15,
    three of four reported body crossings were test points being
    charged 400 points each for existing, and the fourth was real.
    """
    from eda_agent.design.quality import _count_wires_through_bodies

    class _Box:
        def __init__(self, x0, y0, x1, y1):
            self.x_min, self.y_min, self.x_max, self.y_max = x0, y0, x1, y1

    class _Pin:
        def __init__(self, x, y):
            self.x, self.y, self.pin_id = x, y, "1"

    class _Inst:
        def __init__(self, refdes, pins):
            self.refdes, self._pins = refdes, pins

        def all_pin_endpoints(self):
            return self._pins

    # TP1's only pin sits at the centre of its own 40x40 body.
    tp = _Inst("TP1", [_Pin(9100, 5300)])
    tp_box = _Box(9080, 5280, 9120, 5320)
    wire = (9100, 5300, 9100, 5000)
    assert _count_wires_through_bodies([wire], [tp_box], [tp]) == 0

    # A part the wire does NOT attach to is still counted.
    other = _Inst("U9", [_Pin(1, 1)])
    other_box = _Box(9000, 4900, 9200, 5200)
    assert _count_wires_through_bodies(
        [wire], [tp_box, other_box], [tp, other]) == 1


# ---------------------------------------------------------------------------
# The module's own weight table is a claim about the code.
# ---------------------------------------------------------------------------

_TABLE_TO_CONSTANT = {
    "body_overlaps": "_W_OVERLAP",
    "wires_through_bodies": "_W_THROUGH_BODY",
    "wire_crossings": "_W_CROSSINGS",
    "total_wire_length": "_W_LENGTH",
    "aspect_ratio_penalty": "_W_ASPECT",
    "alignment": "_W_ALIGNMENT",
    "flow_reversals": "_W_FLOW_REVERSAL",
    "port_count": "_W_PORTS",
    "decap_orphans": "_W_DECAP_ORPHAN",
    "rail_flips": "_W_RAIL_FLIP",
    "label_spread": "_W_LABEL_SPREAD",
}


def _documented_weights():
    """Parse the weight table out of the module docstring."""
    from eda_agent.design import quality

    rows = {}
    for line in (quality.__doc__ or "").splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line or "weight" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        try:
            rows[cells[0]] = float(cells[1])
        except ValueError:
            continue
    return rows


def test_the_documented_weights_are_the_real_ones():
    """A table nobody checks drifts, and this one already had.

    Its aspect row described a square target for some time after the
    target became the sheet's own proportions, and it claimed six
    metrics when there were eleven. A reader trusts the summary over
    the constants, so the summary has to be true.
    """
    from eda_agent.design import quality

    documented = _documented_weights()
    assert documented, "the weight table is missing from the module docstring"
    for metric, weight in documented.items():
        constant = _TABLE_TO_CONSTANT.get(metric)
        assert constant is not None, f"{metric} is documented but unmapped"
        actual = getattr(quality, constant)
        assert actual == weight, (
            f"the docstring says {metric} weighs {weight}, "
            f"but {constant} is {actual}")


def test_every_weight_appears_in_the_table():
    """A metric added without a row is one nobody knows is scored."""
    from eda_agent.design import quality

    documented = set(_documented_weights())
    for metric, constant in _TABLE_TO_CONSTANT.items():
        assert hasattr(quality, constant), f"{constant} no longer exists"
        assert metric in documented, (
            f"{metric} ({constant}) is scored but has no row in the "
            f"module's weight table")
