# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The planned stages as a whole: their switches, the access lanes kept
for fine-pitch pins, the report of what stays unreached, and the rule
that no bend the router lays turns by more than 45 degrees."""

from __future__ import annotations

import pytest

from eda_agent.layout import geom
from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Pad, PadCopper, Region, Track
from eda_agent.layout.route import Router
from eda_agent.layout.route.exact import sharp_bends
from eda_agent.layout.route.job import route_job
from eda_agent.layout.route.stages import StageOptions, finish_report
from tests.layout.test_diffpair import _pair_board
from tests.layout.test_fanout_dogbone import _plane_board, _qfn
from tests.layout.test_lanes import _bus_board
from tests.layout.test_route import _board, _pad, _random_board


def _route(b, **kw):
    r = Router(b, **kw)
    r.run()
    out = r.apply()
    return r, out, run_drc(out), finish_report(r, out)


def test_stage_switches():
    assert StageOptions.coerce(None) == StageOptions()
    off = StageOptions.coerce(False)
    assert not off.any() and not off.class_order and off.pad_vias == "any"
    assert off.legacy() and not off.bends and not off.fallback and not off.straight
    assert not StageOptions.coerce(None).legacy() and StageOptions.coerce(None).straight
    assert not StageOptions.coerce({"straight": False}).legacy(), "the rest still runs"
    corners_only = StageOptions.coerce({"diff_pairs": False, "plane_fanout": False,
                                        "pin_lanes": False, "bus_lanes": False,
                                        "class_order": False, "pad_vias": "any"})
    assert corners_only.bends and not corners_only.legacy(), "the corners are still cut"
    assert StageOptions.coerce({"bus_lanes": False}).bus_lanes is False
    with pytest.raises(ValueError):
        StageOptions.coerce({"bus_lane": False})


def test_what_b3_measured_better_is_on_and_the_rest_waits_behind_a_switch():
    on = StageOptions()
    assert on.global_stall and on.quick_release
    assert not (on.repair_clean or on.repair_negotiate or on.stall_stop or on.exact_pad_via)
    off = StageOptions.coerce(False)
    assert not (off.global_stall or off.repair_clean or off.repair_negotiate
                or off.quick_release or off.exact_pad_via)
    for name in ("global_stall", "repair_clean", "repair_negotiate", "quick_release",
                 "exact_pad_via"):
        assert not StageOptions(**{**vars(off), name: True}).legacy(), name


def _fine_row(b, y=200.0, nets=None):
    """Eight pins at 0.5 mm along the top of a part, facing up."""
    nets = nets or [f"P{k}" for k in range(8)]
    pads = []
    for k, net in enumerate(nets):
        p = Pad("U1", str(k + 1), 200 + 19.685 * k, y, net=net,
                copper=[PadCopper("TopLayer", "rect", 9.8, 40.0)])
        pads.append(p)
    # The part's far side, so its pins face up and out.
    pads += [Pad("U1", str(20 + k), 200 + 19.685 * k, y - 120, net="",
                 copper=[PadCopper("TopLayer", "rect", 9.8, 40.0)]) for k in range(8)]
    b.pads += pads
    return pads


def test_a_fine_pitch_pins_access_lane_stays_free_of_other_nets():
    # Net X runs left to right just past the pins' ends, where its
    # straight line would cross in front of pin 4. Pin 4's net leaves it
    # and goes down round the part; the strip straight out of the pin is
    # its own, and X keeps out of it.
    b = _board(w=700, h=400)
    pins = _fine_row(b, nets=["", "", "", "P4", "", "", "", ""])
    b.pads += [_pad("R1", "1", 650, 30, "P4"),
               _pad("J1", "1", 100, 240, "X"), _pad("J2", "1", 500, 240, "X")]
    r, out, drc, rep = _route(b)
    assert drc.completion == 1.0 and not drc.violations
    assert rep["pin_lanes"] == 1
    pin = pins[3]
    depth = r.options.lane_depth
    lane = geom.capsule(pin.x, pin.y + 20.0, pin.x, pin.y + 20.0 + depth, 6.0)
    for t in out.tracks:
        if t.net == "X":
            assert geom.clearance(lane, t.shape()) >= r.c - 1e-3, (t.x1, t.y1, t.x2, t.y2)


def test_an_unreachable_pad_is_reported_with_its_place_and_what_blocks_it():
    b = _board(layers=("TopLayer",))
    ring = [(380, 120), (620, 120), (620, 280), (380, 280)]
    hole = [(420, 160), (580, 160), (580, 240), (420, 240)]
    b.outline = [(0, 0), (700, 0), (700, 400), (0, 400)]
    # A's pads R1 and R3 join; R2 sits inside a ring of B copper.
    b.pads = [_pad("R1", "1", 100, 200, "A"), _pad("R3", "1", 100, 320, "A"),
              _pad("R2", "1", 500, 200, "A"), _pad("J1", "1", 500, 60, "B")]
    b.regions = [Region("TopLayer", ring, [hole], "B", "copper")]
    r, out, drc, rep = _route(b)
    assert drc.unrouted == {"A": 1}
    assert rep["unreached"] == [{"net": "A", "pad": "R2.1", "x": 500, "y": 200,
                                 "layers": ["TopLayer"], "blocked_by": ["B"]}]


def _staircase_board():
    """Long pads at a fine pitch, left along their axis and across: the
    router's stubs out of them turn by 90 degrees unless the corner is cut."""
    w = 21.6535
    b = _board(w=800, h=500, gap=7.874, width=w)
    b.layers = [b.layers[0]]

    def long_pad(name, y, net):
        return Pad("U1", name, 300, y, net=net, copper=[PadCopper("TopLayer", "round", 68.8976, w)])

    b.pads = [long_pad("1", 202.5, "A"), long_pad("2", 233.9961, "B"),
              long_pad("3", 171.0039, "C"), _pad("R1", "1", 600, 352.5, "A", size=30.0),
              _pad("R2", "1", 600, 120, "C", size=30.0), _pad("R3", "1", 120, 352.5, "B", size=30.0)]
    return b


def _tqfp_board():
    """TQFP pads as wide as the track on a 0.5 mm pitch: a route leaves one
    along its axis past its end and then across, a right angle unless the
    corner is cut."""
    b = _board(w=800, h=800, gap=7.874, width=11.811)
    b.layers = [b.layers[0]]
    b.pads = [Pad("U4", str(n + 1), 500, 402.0 + (n - 2) * 19.685, net="A" if n == 2 else f"N{n}",
                  rotation=270.0, copper=[PadCopper("TopLayer", "round", 11.811, 70.866)])
              for n in range(5)]
    b.pads.append(_pad("R1", "1", 200, 552.0, "A", size=30.0))
    return b


@pytest.mark.parametrize("build", [_tqfp_board, _staircase_board, _pair_board, _bus_board]
                         + [lambda s=s: _random_board(s) for s in range(4)])
def test_no_bend_the_router_lays_turns_by_more_than_45_degrees(build):
    b = build()
    r, out, drc, rep = _route(b)
    assert not drc.violations
    assert sharp_bends(out.tracks, out.pads, out.vias) == []
    assert rep["sharp_bends"] == 0


def test_an_end_no_stub_can_reach_is_left_open_not_run_through_the_clearance(monkeypatch):
    # A route leaving a TQFP pad as wide as its track starts on a cell in
    # the pad that is short of room (the track there comes inside the next
    # pin's clearance); the end is trimmed back to a cell with room and a
    # stub from the pad's centre laid to it. Where no stub clears, the end
    # is left unjoined, an open the report names, not kept on the short
    # cell. The router before the stages kept it.
    import numpy as np

    b = _tqfp_board()
    r = Router(b)
    job = next(j for j in r.jobs if j.name == "A")
    t = next(t for t in job.terminals if b.pads[t.pad].comp == "U4")
    spec = r.grid.spec
    ci, cj = spec.cell(b.pads[t.pad].x, b.pads[t.pad].y)
    path = [(0, cj, ci - k) for k in range(14)]
    term_of = {tuple(c): t for c in t.cells.tolist()}
    term_of[path[0]] = t
    row = r.rr.clearance_row(job.id)

    def room(c):
        return float(r._slack(job.id, c[0], (np.array([c[1]]), np.array([c[2]])), row)[0])

    assert room(path[0]) < job.width / 2, "the start cell is short of room"
    monkeypatch.setattr(Router, "_stub", lambda self, *a, **k: None)
    out, ends = r._trim_ends(job, path, term_of, spec, r.grid.layers)
    assert ends[0][1] is None, "no stub: the end joins nothing"
    assert out[0] != path[0] and all(room(c) < job.width / 2 for c in path[:path.index(out[0])]
                                     if c in term_of), "the short cells in the pad are gone"
    r.options = StageOptions.coerce(False)
    out, ends = r._trim_ends(job, path, term_of, spec, r.grid.layers)
    assert out[0] == path[0] and ends[0][1] is t


def test_with_the_stages_off_the_router_lays_its_corners_as_it_always_did():
    # stages=False is the router as it was before the stages, right-angle
    # corners and all: a server restart must not change what it lays.
    r, out, drc, rep = _route(_tqfp_board(), stages=False)
    assert drc.completion == 1.0 and not drc.violations
    assert sharp_bends(out.tracks, out.pads, out.vias), "the old router's right angle"


def test_a_plane_board_with_an_exposed_pad_has_no_sharp_bend_either():
    b = _plane_board(700, 500)
    _qfn(b)
    for i in range(1, 13):
        b.pads.append(_pad(f"R{i}", "1", 40 + 50 * i, 470, f"S{i}"))
    b.pads.append(_pad("R20", "1", 650, 60, "VCC"))
    r, out, drc, rep = _route(b)
    assert not drc.violations and drc.completion == 1.0
    assert sharp_bends(out.tracks, out.pads, out.vias) == []


def test_the_bend_check_sees_a_right_angle_and_not_a_turn_under_a_pad():
    t = [Track("TopLayer", 0, 0, 100, 0, 6, "A"), Track("TopLayer", 100, 0, 100, 100, 6, "A")]
    assert [(n, x, y, d) for n, _, x, y, d in sharp_bends(t)] == [("A", 100, 0, 90.0)]
    t45 = [Track("TopLayer", 0, 0, 100, 0, 6, "A"), Track("TopLayer", 100, 0, 200, 100, 6, "A")]
    assert sharp_bends(t45) == []
    pad = _pad("U1", "1", 100, 0, "A", size=20.0)
    assert sharp_bends(t, [pad]) == []
    # A third end at the point makes it a junction, not a bend.
    t3 = t + [Track("TopLayer", 100, 0, 200, 0, 6, "A")]
    assert sharp_bends(t3) == []


def _blocked_corridor_board():
    """A GND pad whose only dog-bone lands in the one corridor a signal must
    take on every layer, while a via island further along the pad's own
    strip of top copper would do for GND."""
    b = _plane_board(500, 400)

    def ring(x0, y0, x1, y1):
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    whole = ring(-5, -5, 505, 405)
    b.regions.append(Region("TopLayer", whole, [ring(345, -5, 377, 182), ring(100, 182, 377, 218),
                                                ring(345, 218, 377, 405), ring(315, 245, 345, 345)],
                            kind="keepout", keepout=True))
    for layer in ("MidLayer2", "BottomLayer"):
        b.regions.append(Region(layer, whole, [ring(345, -5, 377, 405), ring(110, 180, 150, 220)],
                                kind="keepout", keepout=True))
    b.pads = [_pad("C1", "1", 330, 260, "VCC"), _pad("C1", "2", 330, 200, "GND"),
              _pad("R1", "1", 330, 325, "VCC"), _pad("R2", "1", 361, 30, "S"),
              _pad("R3", "1", 361, 370, "S")]
    return b


def test_a_plan_that_leaves_a_connection_unmade_gives_way_to_one_that_makes_it():
    from eda_agent.layout.route import route_adaptive

    planned = route_adaptive(_blocked_corridor_board(), stages={"fallback": False, "plane_fanout": True})
    assert run_drc(planned.apply()).unrouted == {"S": 1}, "the dog-bone sits in S's corridor"
    r = route_adaptive(_blocked_corridor_board(), stages={"plane_fanout": True})
    drc = run_drc(r.apply())
    assert drc.completion == 1.0 and not drc.violations
    assert "1 more connection made" in r.stage_report.fallback
    assert finish_report(r, r.apply())["fallback"] == r.stage_report.fallback


@pytest.mark.asyncio
async def test_the_autoroute_tool_hands_its_stage_switches_to_the_job(monkeypatch, tmp_path):
    from eda_agent.design import jobs
    from eda_agent.layout import read_altium
    from eda_agent.tools import route as route_module
    from tests.conftest import install_bridge_fake

    class _NoBridge:
        async def send_command_async(self, command, params=None, timeout=None):
            raise AssertionError(command)

    install_bridge_fake(monkeypatch, tmp_path, _NoBridge())
    monkeypatch.setattr(read_altium, "read_live_board", lambda path: _pair_board())
    sent = {}

    class _Store:
        def submit(self, kind, fn, params):
            sent.update(kind=kind, params=params)
            return "job-x"

    monkeypatch.setattr(jobs, "get_job_store", lambda: _Store())
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    route_module.register_route_tools(_Mcp())
    out = await tools["pcb_autoroute"](expect_file=r"C:\b\b.PcbDoc", bus_lanes=False,
                                       plane_fanout=True)
    assert out["job_id"] == "job-x" and sent["kind"] == "layout_route"
    assert sent["params"]["stages"] == {"diff_pairs": True, "plane_fanout": True,
                                        "pin_lanes": True, "bus_lanes": False}
    assert StageOptions.coerce(sent["params"]["stages"]).diff_pairs
    # The defaults: pairs, pin lanes and buses on, plane fanout off, and a
    # via only in a BGA ball or an exposed pad (the fallback widens that to
    # IC pins when it alone leaves connections unmade).
    await tools["pcb_autoroute"](expect_file=r"C:\b\b.PcbDoc")
    chosen = StageOptions.coerce(sent["params"]["stages"])
    assert sent["params"]["stages"] == {"diff_pairs": True, "plane_fanout": False,
                                        "pin_lanes": True, "bus_lanes": True}
    assert chosen.pad_vias == "bga" and chosen.bends and chosen.class_order
    # All four off is the router as it was before the stages.
    await tools["pcb_autoroute"](expect_file=r"C:\b\b.PcbDoc", diff_pairs=False,
                                 pin_lanes=False, bus_lanes=False)
    plain = StageOptions.coerce(sent["params"]["stages"])
    assert sent["params"]["stages"] is False
    assert plain.legacy()


def test_the_job_reports_the_stages_and_takes_their_switches():
    b = _pair_board()
    res = route_job({"board": b})
    s = res["summary"]
    assert s["completion"] == 1.0 and s["violations"] == 0
    assert res["pairs"] and res["pairs"][0]["routed"]
    assert res["via_in_pad"] == [] and res["unreached"] == [] and res["buses"] == []
    assert s["sharp_bends"] == 0
    off = route_job({"board": _pair_board(), "stages": {"diff_pairs": False}})
    assert off["pairs"] == []
