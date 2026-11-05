# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Differential pairs: coupled at their gap, turning together, skew evened.

Judged on the copper the router lays (the output board), by the exact
DRC and by measuring the two tracks against each other.
"""

from __future__ import annotations

import math

from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import DiffPair, Rule
from eda_agent.layout.route import Router
from eda_agent.layout.route.diffpair import find_pairs, pair_rule
from eda_agent.layout.route.exact import sharp_bends, turn
from eda_agent.layout.route.stages import finish_report
from eda_agent.layout.rules import RuleSet
from tests.layout.test_route import _board, _pad


def _long(comp, name, x, y, net, w, h):
    p = _pad(comp, name, x, y, net)
    p.copper[0].w, p.copper[0].h = w, h
    return p


def _pair_board(swap=False):
    """A connector's two pins at 0.5 mm, the receiver's up and to the right:
    the pair has to turn twice on its way."""
    b = _board(w=800, h=500)
    p_end, n_end = (399.7, 380.0) if not swap else (380.0, 399.7)
    b.pads = [_long("J1", "1", 100, 120, "USB_DP", 11.8, 40), _long("J1", "2", 119.7, 120, "USB_DM", 11.8, 40),
              _long("U1", "1", 600, p_end, "USB_DP", 40, 11.8), _long("U1", "2", 600, n_end, "USB_DM", 40, 11.8)]
    return b


def _polyline(tracks, start):
    """The tracks of one net, chained from the point ``start``."""
    left = list(tracks)
    pts = [start]
    while left:
        for k, t in enumerate(left):
            a, b = (t.x1, t.y1), (t.x2, t.y2)
            if math.dist(a, pts[-1]) < 1e-6:
                pts.append(b)
            elif math.dist(b, pts[-1]) < 1e-6:
                pts.append(a)
            else:
                continue
            del left[k]
            break
        else:
            break
    return pts


def _dist_to(pts, x, y):
    best = math.inf
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def _route(b, **kw):
    r = Router(b, **kw)
    r.run()
    out = r.apply()
    return r, out, run_drc(out), finish_report(r, out)


def test_pairs_are_read_from_the_boards_definitions_else_from_names():
    b = _board()
    b.pads = [_pad("U1", str(i), 50 + 30 * i, 50, n) for i, n in enumerate(
        ["USB_DP", "USB_DM", "LVDS_P", "LVDS_N", "CLK+", "CLK-", "VCC", "TX_P"])]
    got = {(p, n) for _, p, n, src in find_pairs(b)}
    assert got == {("USB_DP", "USB_DM"), ("LVDS_P", "LVDS_N"), ("CLK+", "CLK-")}
    b.diff_pairs = [DiffPair("D", "LVDS_P", "LVDS_N")]
    assert [(p, n, src) for _, p, n, src in find_pairs(b)] == [("LVDS_P", "LVDS_N", "defined")]


def test_width_and_gap_come_from_the_boards_pair_rule():
    b = _board()
    b.diff_pairs = [DiffPair("USB", "USB_DP", "USB_DM")]
    b.rules.insert(0, Rule("DiffPair", "9", "InDifferentialPair('USB')", "All", 1, True, {},
                           "Differential Pairs Uncoupled Length using the Gap Constraints "
                           "(Min=0.1mm) (Max=0.25mm) (Prefered=0.2mm)  and Width Constraints "
                           "(Min=0.1mm) (Max=0.2mm) (Prefered=0.15mm) (InDifferentialPair('USB'))"))
    got = pair_rule(RuleSet.from_board(b), "USB", "USB_DP")
    assert abs(got["gap"] - 0.2 / 0.0254) < 1e-6 and abs(got["width"] - 0.15 / 0.0254) < 1e-6


def test_a_pair_runs_coupled_at_its_gap_and_turns_together_at_45_degrees():
    b = _pair_board()
    r, out, drc, rep = _route(b)
    assert drc.completion == 1.0 and not drc.violations
    entry = rep["pairs"][0]
    assert entry["routed"] and entry["layer_changes"] == 0
    w, gap = entry["width"], entry["gap"]
    p = _polyline([t for t in out.tracks if t.net == "USB_DP"], (100, 120))
    n = _polyline([t for t in out.tracks if t.net == "USB_DM"], (119.7, 120))
    assert math.dist(p[-1], (600, 399.7)) < 1e-6 and math.dist(n[-1], (600, 380.0)) < 1e-6
    # Every bend of either track is 45 degrees or less: two per corner.
    assert sharp_bends([t for t in out.tracks if t.net in ("USB_DP", "USB_DM")]) == []
    assert all(turn(a, c, d) <= 45.5 for a, c, d in zip(p, p[1:], p[2:]))
    assert sum(1 for a, c, d in zip(n, n[1:], n[2:]) if turn(a, c, d) > 1.0) >= 4
    # Along the run between the two fan-ins, every point of the positive
    # track is one pitch (width plus gap) from the negative one, through
    # both turns, except where a bump moves it further away.
    pitch = w + gap
    coupled = 0
    total = 0
    for (ax, ay), (bx, by) in zip(p[3:-3], p[4:-3]):
        for k in range(10):
            x, y = ax + (bx - ax) * k / 10, ay + (by - ay) * k / 10
            d = _dist_to(n, x, y)
            assert d >= pitch - 0.05, (x, y, d)
            total += 1
            coupled += abs(d - pitch) < 0.05
    assert coupled >= 0.8 * total


def test_the_shorter_track_takes_bumps_until_the_skew_is_within_budget():
    b = _pair_board()
    r, out, drc, rep = _route(b)
    assert not drc.violations
    entry = rep["pairs"][0]
    length = {net: sum(math.hypot(t.x2 - t.x1, t.y2 - t.y1) for t in out.tracks if t.net == net)
              for net in ("USB_DP", "USB_DM")}
    assert entry["bumps"] >= 1, "two turns the same way leave the inner track short"
    assert abs(length["USB_DP"] - length["USB_DM"]) <= r.options.pair_skew + 1e-6
    assert abs(entry["skew"] - abs(length["USB_DP"] - length["USB_DM"])) < 1e-3
    assert abs(entry["length_p"] - length["USB_DP"]) < 1e-2
    # The bumps bulge away from the partner: the tracks never come closer
    # than their pitch anywhere.
    p = _polyline([t for t in out.tracks if t.net == "USB_DP"], (100, 120))
    n = _polyline([t for t in out.tracks if t.net == "USB_DM"], (119.7, 120))
    pitch = entry["width"] + entry["gap"]
    for x, y in n[3:-3]:
        assert _dist_to(p, x, y) >= pitch - 0.05


def test_a_leg_with_more_pads_is_coupled_end_to_end_and_its_other_pads_join_it():
    # An ESD part on the pair, off to one side: the pair runs coupled from
    # the connector to the receiver, and the ESD pins join their own track.
    b = _pair_board()
    b.pads += [_pad("D1", "1", 330, 160, "USB_DP", size=12.0), _pad("D1", "2", 349.7, 160, "USB_DM", size=12.0)]
    r, out, drc, rep = _route(b)
    assert drc.completion == 1.0 and not drc.violations
    entry = rep["pairs"][0]
    assert entry["routed"] and entry["branches"] == 2
    # The coupled run is between the connector and the receiver, the two
    # couples furthest apart.
    p = _polyline([t for t in out.tracks if t.net == "USB_DP"], (100, 120))
    assert math.dist(p[-1], (600, 399.7)) < 1e-6
    assert abs(entry["length_p"] - sum(math.dist(a, c) for a, c in zip(p, p[1:]))) < 1e-2


def test_a_pair_whose_ends_disagree_on_polarity_is_left_to_the_router():
    # P on the left at one end and on the right at the other: on one layer
    # the coupled pair would have to cross itself.
    b = _pair_board(swap=True)
    r, out, drc, rep = _route(b)
    entry = rep["pairs"][0]
    assert not entry["routed"] and entry["reason"] == "polarity swap needed"
    assert drc.completion == 1.0 and not drc.violations


def test_a_usb_c_pair_with_two_pads_a_line_is_routed_coupled():
    # A USB-C receptacle carries each data line twice, interleaved for
    # either plug orientation: B7 (D-), A6 (D+), A7 (D-), B6 (D+) at 0.5 mm.
    # A demo board's pair fell back to two loose tracks: both ends were
    # picked on the connector ("two lanes come too close").
    b = _pair_board()
    b.pads = [_long("J1", "B7", 80.3, 120, "USB_DM", 11.8, 43),
              _long("J1", "A6", 100, 120, "USB_DP", 11.8, 43),
              _long("J1", "A7", 119.7, 120, "USB_DM", 11.8, 43),
              _long("J1", "B6", 139.4, 120, "USB_DP", 11.8, 43),
              _long("U1", "1", 600, 399.7, "USB_DP", 40, 11.8),
              _long("U1", "2", 600, 380.0, "USB_DM", 40, 11.8),
              _pad("D1", "1", 330, 160, "USB_DP", size=12.0),
              _pad("D1", "2", 349.7, 160, "USB_DM", size=12.0)]
    r, out, drc, rep = _route(b)
    entry = rep["pairs"][0]
    assert entry["routed"], entry
    assert drc.completion == 1.0 and not drc.violations


def test_a_pair_is_followed_through_an_esd_part():
    """The round LED board's USBLC6 joined USB_DP/USB_DM (pins 1, 3) to the
    connector's NetJ1_A6/NetJ1_A7 (pins 6, 4): only the named half was
    routed as a pair, the connector half as two loose tracks."""
    b = _board()
    esd = {"1": "USB_DP", "2": "GND", "3": "USB_DM", "4": "NetJ1_A7",
           "5": "VBUS", "6": "NetJ1_A6"}
    mcu = {str(i): f"N{i}" for i in range(1, 21)}
    mcu.update({"17": "USB_DM", "18": "USB_DP"})
    b.pads = ([_pad("U1", k, 50 + 10 * int(k), 50, n) for k, n in esd.items()]
              + [_pad("U3", k, 50 + 10 * int(k), 150, n) for k, n in mcu.items()]
              + [_pad("J1", "A6", 300, 50, "NetJ1_A6"), _pad("J1", "A7", 310, 50, "NetJ1_A7")])
    got = {(p, n, src) for _, p, n, src in find_pairs(b)}
    assert ("NetJ1_A6", "NetJ1_A7", "through U1") in got
    # The MCU mirrors its USB pins onto N3/N4: not a pass-through part.
    assert not any("U3" in src for _, _, src in got)
    b.diff_pairs = [DiffPair("USB", "USB_DP", "USB_DM")]
    got = [(p, n, src) for _, p, n, src in find_pairs(b)]
    assert got == [("USB_DP", "USB_DM", "defined"), ("NetJ1_A6", "NetJ1_A7", "through U1")]


def test_the_plane_beside_a_pair_is_named():
    """07b's bottom-layer pair ran over the +5 V plane while the summary
    said ground; the report never said which plane it had."""
    from eda_agent.layout.model import Layer
    from eda_agent.layout.route.diffpair import reference_plane
    b = _board()
    b.layers = [Layer("TopLayer", "signal", 0), Layer("IP1", "plane", 1, "GND"),
                Layer("IP2", "plane", 2, "+5V"), Layer("BottomLayer", "signal", 3)]
    assert reference_plane(b, "BottomLayer") == [
        {"layer": "IP2", "net": "+5V", "layers_away": 1}]
    assert reference_plane(b, "TopLayer") == [
        {"layer": "IP1", "net": "GND", "layers_away": 1}]
