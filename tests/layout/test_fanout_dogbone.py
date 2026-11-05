# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Plane fan-out: a dog-bone for every plane pad, and no via in a pad.

A passive's pad never takes a via, from the fan-out stage or from the
router; an IC's exposed pad takes an array of them, and every via in a
pad is listed for the fab notes.
"""

from __future__ import annotations

from eda_agent.layout import geom
from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Region
from eda_agent.layout.route import Router
from eda_agent.layout.route.stages import finish_report
from tests.layout.test_route import _board, _pad

LAYERS = ("TopLayer", "MidLayer1", "MidLayer2", "BottomLayer")


def _plane_board(w=600, h=400):
    """Four layers, a GND pour covering the first inner layer: a plane."""
    b = _board(w=w, h=h, layers=LAYERS)
    b.regions.append(Region("MidLayer1", [(-5, -5), (w + 5, -5), (w + 5, h + 5), (-5, h + 5)],
                            [], "GND", "pour_boundary"))
    return b


def _route(b, **kw):
    # Plane fanout is off by default (it cost completion over the benchmark
    # boards); these tests are about what it does when it is on.
    kw.setdefault("stages", {"plane_fanout": True})
    r = Router(b, **kw)
    r.run()
    out = r.apply()
    return r, out, run_drc(out), finish_report(r, out)


def _vias_in(out, pad):
    """The vias whose copper overlaps the pad's."""
    s = pad.shape_on(pad.copper[0].layer)
    return [v for v in out.vias if geom.clearance(geom.circle(v.x, v.y, v.diameter), s) < 0]


def test_a_passives_plane_pad_gets_a_dog_bone_and_never_a_via_in_its_pad():
    b = _plane_board()
    # Pads a little longer across the part than along it, as a chip part's
    # are: out of the part is along the part, not along the pad's long
    # side, though a stub that way is a mil or two longer.
    b.pads = [_pad("C1", "1", 200, 200, "VCC", size=30.0), _pad("C1", "2", 250, 200, "GND", size=30.0),
              _pad("U1", "1", 500, 300, "VCC")]
    for p in b.pads[:2]:
        p.copper[0].w, p.copper[0].h = 30.0, 34.0
    r, out, drc, rep = _route(b)
    assert drc.completion == 1.0 and not drc.violations
    gnd = b.pads[1]
    assert not _vias_in(out, gnd) and not _vias_in(out, b.pads[0]), "no via in a passive's pad"
    vias = [v for v in out.vias if v.net == "GND"]
    assert len(vias) == 1
    v = vias[0]
    # Out of the part, away from its other pad, by a web past the pad.
    assert v.x > gnd.x + 15.0 and abs(v.y - gnd.y) < 1e-6
    web = geom.clearance(geom.circle(v.x, v.y, v.diameter), gnd.shape_on("TopLayer"))
    assert web >= r.c - 1e-6
    stub = [t for t in out.tracks if t.net == "GND"]
    assert len(stub) == 1 and (stub[0].x1, stub[0].y1) == (gnd.x, gnd.y)
    assert rep["fanout"].get("dogbones") == 1 and rep["via_in_pad"] == []


def test_the_router_never_puts_a_via_in_a_passives_pad():
    # The pad is walled in on its own layer by a keep-out ring: the only way
    # to its plane is a via, and the only place for one is in the pad. The
    # stage cannot lay a dog-bone, and the router must not take the pad:
    # better unreached (and reported) than a via that wicks the joint.
    b = _plane_board()
    b.pads = [_pad("C1", "1", 200, 200, "VCC", size=30.0), _pad("C1", "2", 260, 200, "GND", size=30.0),
              _pad("U1", "1", 500, 300, "VCC"), _pad("U1", "2", 500, 100, "GND")]
    outer = [(225, 165), (295, 165), (295, 235), (225, 235)]
    hole = [(238, 178), (282, 178), (282, 222), (238, 222)]
    b.regions.append(Region("TopLayer", outer, [hole], kind="keepout", keepout=True))
    r, out, drc, rep = _route(b)
    assert not drc.violations
    assert not _vias_in(out, b.pads[1]), "a via went into the passive's pad"
    assert any(u["pad"] == "C1.2" for u in rep["unreached"])
    # The rule is the router's, not only the stage's: with the stages off
    # and via-in-pad left to the router, the cells in the pad stay barred.
    r2, out2, _, _ = _route(b, stages={"plane_fanout": False})
    assert not _vias_in(out2, b.pads[1])


def _qfn(b, cx=300.0, cy=200.0, nets=None, ep="GND"):
    """Sixteen pins at 0.5 mm, four a side, and a 100 mil exposed pad."""
    nets = nets or ["S1", "GND", "S2", "S3", "S4", "S5", "GND", "S6",
                    "S7", "S8", "S9", "S10", "S11", "GND", "S12", "VCC"]
    k = 0
    for side in range(4):
        for j in range(4):
            t = (j - 1.5) * 19.685
            x, y, w, h = [(cx + t, cy - 80, 9.8, 30), (cx + 80, cy + t, 30, 9.8),
                          (cx - t, cy + 80, 9.8, 30), (cx - 80, cy - t, 30, 9.8)][side]
            p = _pad("U1", str(k + 1), x, y, nets[k])
            p.copper[0].w, p.copper[0].h = w, h
            b.pads.append(p)
            k += 1
    ep_pad = _pad("U1", "17", cx, cy, ep, size=100.0)
    b.pads.append(ep_pad)
    return ep_pad


def test_an_exposed_pad_gets_a_via_array_listed_for_the_fab_notes():
    b = _plane_board(700, 500)
    ep = _qfn(b)
    for i in range(1, 13):
        b.pads.append(_pad(f"R{i}", "1", 40 + 50 * i, 470, f"S{i}"))
    b.pads.append(_pad("R20", "1", 650, 60, "VCC"))
    # Vias kept out of IC pins too, so every via in a pad is the array's.
    r, out, drc, rep = _route(b, stages={"plane_fanout": True, "pad_vias": "bga"})
    assert not drc.violations
    inside = _vias_in(out, ep)
    assert len(inside) >= 4, "an array, not a single via"
    for v in inside:
        assert geom.clearance(geom.circle(v.x, v.y, v.diameter), ep.shape_on("TopLayer")) < 0
    listed = {(round(v["x"], 3), round(v["y"], 3)) for v in rep["via_in_pad"]
              if v["kind"] == "exposed pad" and v["pad"] == "U1.17"}
    assert listed == {(round(v.x, 3), round(v.y, 3)) for v in inside}
    assert all("fill" in v["note"] for v in rep["via_in_pad"])
    # The QFN's GND pins beside the pad are joined to it by a stub inward.
    pins = [p for p in b.pads if p.comp == "U1" and p.net == "GND" and p is not ep]
    for p in pins:
        stubs = [t for t in out.tracks if t.net == "GND" and (t.x1, t.y1) == (p.x, p.y)]
        assert stubs, p.name
        t = stubs[0]
        assert geom.point_in_poly(t.x2, t.y2, ep.shape_on("TopLayer")), "the stub ends on the pad"
    assert rep["fanout"].get("exposed_pad_stubs") == len(pins)
    assert {v["kind"] for v in rep["via_in_pad"]} == {"exposed pad"}


def test_a_pad_already_joined_to_its_via_gets_no_second_dog_bone():
    """The final 07 take: every LED supply pad already had its via and
    stub, laid by the agent, and the plane fan-out added 303 more vias."""
    from eda_agent.layout.model import Track, Via
    b = _plane_board()
    b.pads = [_pad("C1", "1", 200, 200, "VCC", size=30.0), _pad("C1", "2", 250, 200, "GND", size=30.0),
              _pad("U1", "1", 500, 300, "VCC")]
    b.tracks.append(Track("TopLayer", 250, 200, 290, 200, 10.0, "GND"))
    b.vias.append(Via(290, 200, 24.0, 12.0, "TopLayer", "BottomLayer", "GND"))
    r, out, drc, rep = _route(b)
    assert len([v for v in out.vias if v.net == "GND"]) == 1, "a second via for a joined pad"
    assert rep["fanout"].get("already_joined") == 1
    assert not rep["fanout"].get("dogbones")
