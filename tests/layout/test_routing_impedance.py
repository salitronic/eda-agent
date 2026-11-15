# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Impedance in the brief: the width each layer needs on the board's own
stack, refused where the entry misses it, and measured on the routed copper.

The stack is a public example board's as its Layer Stack Manager has it:
four copper layers of 1.4173 mil, FR-4 12.5984 mil at 4.8 between them.
"""

from __future__ import annotations

import asyncio

import pytest

from eda_agent.design import impedance_sizing as zs
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.route import brief as B
from eda_agent.layout.route import impedance as zi
from eda_agent.layout.route import review
from eda_agent.units import OZ_TO_MILS

CU, FR4, ER = 1.4173, 12.5984, 4.8
STACK = [{"kind": "copper", "name": "Top Layer", "thickness": CU, "er": 0.0},
         {"kind": "dielectric", "name": "Dielectric 1", "thickness": FR4, "er": ER},
         {"kind": "copper", "name": "Mid-Layer 1", "thickness": CU, "er": 0.0},
         {"kind": "dielectric", "name": "Dielectric 2", "thickness": FR4, "er": ER},
         {"kind": "copper", "name": "Mid-Layer 2", "thickness": CU, "er": 0.0},
         {"kind": "dielectric", "name": "Dielectric 3", "thickness": FR4, "er": ER},
         {"kind": "copper", "name": "Bottom Layer", "thickness": CU, "er": 0.0}]


def _board(stack=True) -> LayoutBoard:
    rules = [Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
                  "Clearance Constraint (Gap=6mil) (All),(All)"),
             Rule("Width", "1", "All", "All", 1, True, {},
                  "Width Constraint (Min=5mil) (Max=40mil) (Preferred=6mil) (All)")]
    names = ["TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"]
    b = LayoutBoard(name="demo.PcbDoc", outline=[(0, 0), (600, 0), (600, 400), (0, 400)],
                    layers=[Layer(n, "signal", i) for i, n in enumerate(names)], rules=rules)
    smd = [PadCopper("TopLayer", "rect", 30, 30)]
    b.pads = [Pad("J1", "1", 100, 200, net="RF", copper=list(smd)),
              Pad("U1", "1", 500, 200, net="RF", copper=list(smd))]
    if stack:
        b.meta["stack"] = [dict(l) for l in STACK]
    return b


def test_each_copper_layer_takes_its_geometry_from_the_stack():
    geo = zi.geometry(_board())
    assert geo["TopLayer"] == {"geometry": "microstrip", "h": FR4, "er": ER, "oz": CU / OZ_TO_MILS}
    assert geo["MidLayer1"]["geometry"] == "stripline"
    assert geo["MidLayer1"]["h"] == pytest.approx(2 * FR4 + CU)
    assert geo["BottomLayer"]["geometry"] == "microstrip"
    w = zi.width_for(geo["TopLayer"], 50.0)
    assert zs.z0_microstrip(w, FR4, ER, CU / OZ_TO_MILS) == pytest.approx(50.0, abs=0.1)
    assert zi.geometry(_board(stack=False)) == {}


def test_an_entry_is_refused_until_its_width_meets_the_target_on_every_layer_allowed():
    b = _board()
    need = zi.width_for(zi.geometry(b)["TopLayer"], 50.0)
    got = B.check(b, B.Brief.from_list([{"nets": ["RF"], "layers": ["TopLayer"],
                                         "impedance": 50, "source": "test"}]))
    assert f"needs a width of {need:g} on TopLayer" in got["errors"][0]
    got = B.check(b, B.Brief.from_list([{"nets": ["RF"], "layers": ["TopLayer"], "width": 6,
                                         "impedance": 50, "source": "test"}]))
    assert "width 6 gives" in got["errors"][0] and f"needs {need:g}" in got["errors"][0]
    got = B.check(b, B.Brief.from_list([{"nets": ["RF"], "layers": ["TopLayer"], "width": need,
                                         "impedance": 50, "source": "test"}]))
    assert got["ok"]
    # The same width on an inner layer is not 50 ohm: the entry must leave
    # it out, or give a width that suits both.
    got = B.check(b, B.Brief.from_list([{"nets": ["RF"], "layers": ["TopLayer", "MidLayer1"],
                                         "width": need, "impedance": 50, "source": "test"}]))
    assert len(got["errors"]) == 1 and "on MidLayer1" in got["errors"][0]


def test_no_dielectric_and_a_differential_target_without_its_gap_are_refused():
    got = B.check(_board(stack=False), B.Brief.from_list([
        {"nets": ["RF"], "impedance": 50, "width": 10, "source": "test"}]))
    assert "gives no dielectric" in got["errors"][0]
    got = B.check(_board(), B.Brief.from_list([
        {"nets": ["RF"], "diff_impedance": 90, "width": 6, "source": "test"}]))
    assert "needs the pair's gap" in got["errors"][0]


def test_the_review_measures_each_piece_at_its_width_on_its_layer():
    b = _board()
    brief = B.Brief.from_list([{"nets": ["RF"], "impedance": 50, "width": 22,
                                "source": "test"}])

    def t(layer, width, x1=100, x2=300):
        return {"x1": x1, "y1": 200, "x2": x2, "y2": 200, "width": width, "layer": layer,
                "net_name": "RF"}

    need = zi.width_for(zi.geometry(b)["TopLayer"], 50.0)
    assert review.impedance_misses(b, [t("TopLayer", need)], brief) == {}
    miss = review.impedance_misses(b, [t("TopLayer", need), t("MidLayer1", need, 300, 500)],
                                   brief)
    assert miss["RF"]["layer"] == "MidLayer1" and miss["RF"]["length"] == pytest.approx(200)
    found = review.findings(b, b, [t("MidLayer1", need)], [], brief, ["RF"], {}, {}, {}, {})
    f = next(f for f in found if f["check"] == "impedance")
    assert f["id"] == "impedance:RF" and f["limit"] == 50 and f["measured"] < 45
    assert "field solver" in f["text"]


# ---- the stack read ------------------------------------------------------------------

class _Bridge:
    def __init__(self, phys):
        self.phys = phys

    async def send_command_async(self, command, params=None, timeout=None):
        if command == "pcb.get_layer_stackup":
            return {"layers": [{"name": n, "order": i + 1, "dielectric_height_mils": 0,
                                "dielectric_constant": 0, "dielectric_type": "none"}
                               for i, n in enumerate(["Top Layer", "Mid-Layer 1",
                                                      "Mid-Layer 2", "Bottom Layer"])]}
        if command == "pcb.physical_stack":
            return self.phys
        raise AssertionError(command)


def _phys():
    return {"layers": [{"kind": "solder_mask", "thickness_mils": 0.4, "constant": 3.5}]
            + [{"kind": l["kind"], "name": l["name"], "thickness_mils": l["thickness"],
                "constant": l["er"], "material": "FR-4" if l["kind"] == "dielectric" else ""}
               for l in STACK]}


def test_the_layer_stack_read_takes_its_dielectrics_from_the_physical_stack():
    # Live, every dielectric read 0 mil at 0 while the stack manager had
    # FR-4 12.6 mil at 4.8.
    from eda_agent.bridge.stackup import layer_stackup

    got = asyncio.run(layer_stackup(_Bridge(_phys())))
    top = got["layers"][0]
    assert (top["dielectric_height_mils"], top["dielectric_constant"]) == (FR4, ER)
    assert got["layers"][3]["dielectric_height_mils"] == 0, "nothing beneath the bottom"
    assert got["dielectrics_from"] == "pcb_physical_stack"
    # A stack whose copper does not match is left as read.
    got = asyncio.run(layer_stackup(_Bridge({"layers": _phys()["layers"][:4]})))
    assert "dielectrics_from" not in got


def test_every_reader_of_the_stack_goes_through_the_fill():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "eda_agent"
    direct = []
    for f in root.rglob("*.py"):
        if f.name == "stackup.py":
            continue
        for m in re.finditer(r'send_command_async\(\s*"pcb\.get_layer_stackup"',
                             f.read_text(encoding="utf-8")):
            direct.append(f.relative_to(root).as_posix())
    # The outjob reads only the layer ids, which the fill does not touch.
    assert direct == ["tools/project.py"]
