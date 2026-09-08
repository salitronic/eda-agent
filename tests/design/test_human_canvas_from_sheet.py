# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""human_canvas_from_sheet on a real sheet text: connected copper only."""
from __future__ import annotations

from eda_agent.design.human_benchmark import human_canvas_from_sheet, plan_from_sheet
from eda_agent.design.kicad_sheet_reader import read_sheet, symbols_from_sheet
from eda_agent.design.plan import DesignPlan

# Two resistors on one net, wired pin to pin, plus a stray wire on the SAME
# net far away from either part: the stub of a part the plan never saw.
SHEET = """(kicad_sch (version 20231120) (generator "eeschema")
  (paper "A4")
  (lib_symbols
    (symbol "Lib:R" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 0 0 0))
      (property "Value" "R" (at 0 0 0))
      (symbol "R_0_1"
        (rectangle (start -1.016 -2.54) (end 1.016 2.54)))
      (symbol "R_1_1"
        (pin passive line (at 0 5.08 270) (length 2.54)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -5.08 90) (length 2.54)
          (name "~" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27)))))))
  )
  (symbol (lib_id "Lib:R") (at 25.4 25.4 0) (unit 1)
    (property "Reference" "R1" (at 0 0 0)) (property "Value" "1k" (at 0 0 0)))
  (symbol (lib_id "Lib:R") (at 50.8 25.4 0) (unit 1)
    (property "Reference" "R2" (at 0 0 0)) (property "Value" "1k" (at 0 0 0)))
  (wire (pts (xy 25.4 20.32) (xy 50.8 20.32)))
  (wire (pts (xy 25.4 30.48) (xy 50.8 30.48)))
  (wire (pts (xy 150.0 150.0) (xy 160.0 150.0)))
  (label "SIG" (at 38.1 20.32 0))
  (label "SIG" (at 155.0 150.0 0))
)
"""


def _build():
    sheet = read_sheet(SHEET, "fixture.kicad_sch")
    plan = DesignPlan.model_validate(plan_from_sheet(sheet))
    cv, note = human_canvas_from_sheet(sheet, plan, symbols_from_sheet(SHEET))
    assert cv is not None, note
    return cv, plan


def test_placed_parts_and_their_wiring_survive():
    cv, plan = _build()
    assert {i.refdes for i in cv.instances} == {"R1", "R2"}
    assert len(cv.wires) >= 1, "the pin-to-pin wires must be kept"
    pins = {(ep.x, ep.y) for i in cv.instances for ep in i.all_pin_endpoints()}
    ends = {(w.x1, w.y1) for w in cv.wires} | {(w.x2, w.y2) for w in cv.wires}
    assert pins & ends, "kept wires must touch a placed pin"


def test_a_wire_and_label_that_touch_no_placed_part_are_dropped():
    cv, _ = _build()
    far = [w for w in cv.wires if w.x1 >= 100000 or w.x2 >= 100000]
    assert far == [], "the stray wire is on the plan's net but on no plan part"
    assert not [l for l in cv.labels if l.x >= 100000], (
        "a label on dropped copper is dropped with it")


def test_the_y_axis_is_flipped_into_the_canvas_frame():
    cv, _ = _build()
    r1 = cv.instance_by_refdes("R1")
    assert r1.y < 0, "KiCad Y grows down; the canvas is Y-up"
