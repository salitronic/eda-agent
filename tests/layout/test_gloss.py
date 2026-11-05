# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The gloss pass on boards small enough to check by hand: a run is laid
again shorter and cleaner, or left alone when that would cost a join, a
clearance or a pad entry."""
from __future__ import annotations

import math

from eda_agent.layout import geom
from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule, Track
from eda_agent.layout.route.gloss import gloss

GAP = 6.0


def _board() -> LayoutBoard:
    rule = Rule(name="Clearance", kind="0", scope1="All", scope2="All", priority=1,
                values={"gap": GAP},
                descriptor=f"Clearance Constraint (Gap={GAP}mil) (All),(All)")
    return LayoutBoard(name="t", layers=[Layer("TopLayer", "signal", 0),
                                         Layer("BottomLayer", "signal", 1)],
                       rules=[rule])


def _pad(comp, x, y, net, w=20.0, h=20.0) -> Pad:
    return Pad(comp=comp, name="1", x=x, y=y, net=net,
               copper=[PadCopper("TopLayer", "rect", w, h)])


def _path(b, net, pts, width=10.0):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        b.tracks.append(Track("TopLayer", x1, y1, x2, y2, width, net))


def _tracks(b, net):
    return [t for t in b.tracks if t.net == net]


def _length(b, net):
    return sum(math.hypot(t.x2 - t.x1, t.y2 - t.y1) for t in _tracks(b, net))


def _max_turn(b, net):
    """The largest turn between two tracks of the net meeting end to end."""
    ts = _tracks(b, net)
    worst = 0.0
    for i, a in enumerate(ts):
        for c in ts[i + 1:]:
            for p, q in (((a.x1, a.y1), (a.x2, a.y2)), ((a.x2, a.y2), (a.x1, a.y1))):
                for r, s in (((c.x1, c.y1), (c.x2, c.y2)), ((c.x2, c.y2), (c.x1, c.y1))):
                    if math.hypot(q[0] - r[0], q[1] - r[1]) < 0.01:
                        u = (q[0] - p[0], q[1] - p[1])
                        v = (s[0] - r[0], s[1] - r[1])
                        cos = (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))
                        worst = max(worst, math.degrees(math.acos(max(-1, min(1, cos)))))
    return worst


def _octilinear(t):
    ang = math.degrees(math.atan2(t.y2 - t.y1, t.x2 - t.x1)) % 45.0
    return min(ang, 45.0 - ang) < 0.05


def test_a_square_detour_becomes_a_45_run_entering_both_pads_along_an_axis():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 300, 200, "N")]
    _path(b, "N", [(0, 0), (0, 200), (300, 200)])
    r = gloss(b)
    assert r.relaid == 1
    assert abs(_length(b, "N") - (100 + 200 * math.sqrt(2))) < 0.01
    assert all(_octilinear(t) for t in _tracks(b, "N"))
    assert _max_turn(b, "N") <= 45.5
    first = [t for t in _tracks(b, "N") if (t.x1, t.y1) == (0, 0) or (t.x2, t.y2) == (0, 0)]
    last = [t for t in _tracks(b, "N") if (t.x1, t.y1) == (300, 200) or (t.x2, t.y2) == (300, 200)]
    assert len(first) == 1 and first[0].y1 == first[0].y2       # leaves U1 along its axis
    assert len(last) == 1 and last[0].y1 == last[0].y2          # enters U2 along its axis
    assert not run_drc(b).violations and not run_drc(b).unrouted


def test_a_shortcut_through_another_nets_copper_is_not_taken():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 300, 200, "N"),
               _pad("U3", 150, 100, "M", 30, 30)]
    _path(b, "N", [(0, 0), (0, 200), (300, 200)])
    before = [(t.x1, t.y1, t.x2, t.y2) for t in _tracks(b, "N")]
    r = gloss(b)
    assert r.relaid == 0 and r.kept == {"clearance": 1} and r.blocked_by == {"pad of M": 1}
    assert [(t.x1, t.y1, t.x2, t.y2) for t in _tracks(b, "N")] == before
    m = b.pads[2].shape_on("TopLayer")
    assert all(geom.clearance(t.shape(), m) >= GAP for t in _tracks(b, "N"))


def test_a_track_teed_onto_the_run_keeps_its_join():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 300, 200, "N"), _pad("U3", 150, 300, "N")]
    _path(b, "N", [(0, 0), (0, 200), (300, 200)])
    _path(b, "N", [(150, 300), (150, 200)])
    gloss(b)
    tee = geom.circle(150, 200, 0.0)
    assert any(geom.clearance(tee, t.shape()) <= 1e-3 for t in _tracks(b, "N")
               if not (t.x1 == t.x2 == 150))
    assert not run_drc(b).unrouted


def test_a_clean_run_is_left_alone():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 400, 0, "N")]
    _path(b, "N", [(0, 0), (400, 0)])
    r = gloss(b)
    assert r.relaid == 0 and len(_tracks(b, "N")) == 1
    assert set(r.kept) <= {"already_shortest"}


def test_a_hairpin_past_a_pad_is_cut_without_turning_back():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 400, 0, "N")]
    # Overshoots U2 and comes back: a 180 degree fold at (500, 0).
    _path(b, "N", [(0, 0), (500, 0), (500, 40), (400, 40), (400, 0)])
    r = gloss(b)
    assert r.relaid == 1
    assert abs(_length(b, "N") - 400) < 0.01 and _max_turn(b, "N") <= 90.5


def test_a_bend_another_net_needs_is_kept_and_the_board_stays_clean():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 400, 0, "N")]
    b.tracks.append(Track("TopLayer", 200, -100, 200, 100, 10.0, "M"))
    # N has to cross M on the bottom: here it is on top and goes round.
    _path(b, "N", [(0, 0), (0, 150), (400, 150), (400, 0)])
    gloss(b)
    assert not run_drc(b).violations
    assert all(geom.clearance(t.shape(), b.tracks[0].shape()) >= GAP
               for t in _tracks(b, "N"))


def test_a_run_from_a_junction_never_folds_onto_another_track_there():
    b = _board()
    b.pads += [_pad("U2", 300, 200, "N"), _pad("U3", 100, 100, "N"), _pad("U4", -200, 0, "N")]
    _path(b, "N", [(0, 0), (0, 200), (300, 200)])
    _path(b, "N", [(0, 0), (100, 100)])        # leaves the junction at 45 degrees
    _path(b, "N", [(0, 0), (-200, 0)])
    gloss(b)
    at_j = [t for t in _tracks(b, "N") if (0, 0) in ((t.x1, t.y1), (t.x2, t.y2))]
    dirs = [((t.x2, t.y2) if (t.x1, t.y1) == (0, 0) else (t.x1, t.y1)) for t in at_j]
    unit = [(x / math.hypot(x, y), y / math.hypot(x, y)) for x, y in dirs]
    sharpest = min(math.degrees(math.acos(max(-1, min(1, u[0] * v[0] + u[1] * v[1]))))
                   for i, u in enumerate(unit) for v in unit[i + 1:])
    assert sharpest >= 44.5
    assert not run_drc(b).unrouted


def test_a_run_turns_only_once_it_is_clear_of_the_pad():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 300, 290, "N")]
    # The shortest 45 run would leave U1 straight for 5 mil and turn inside it.
    _path(b, "N", [(0, 0), (0, 290), (300, 290)])
    gloss(b)
    u1 = b.pads[0].shape_on("TopLayer")
    for t in _tracks(b, "N"):
        if not (t.x1 == t.x2 or t.y1 == t.y2):
            assert geom.clearance(t.shape(), u1) > 0


def test_a_run_never_crosses_a_pad_of_its_net_on_the_way():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 0, 400, "N"), _pad("U3", 0, 200, "N")]
    # U1 to U2 goes round U3, which has its own track: the straight path
    # is shorter and clear of every other net, but runs over U3.
    _path(b, "N", [(0, 0), (0, 20), (60, 80), (60, 320), (0, 380), (0, 400)])
    _path(b, "N", [(0, 200), (-150, 200)])
    gloss(b)
    u3 = b.pads[2].shape_on("TopLayer")
    for t in _tracks(b, "N"):
        if not (t.y1 == t.y2 == 200):
            assert geom.clearance(t.shape(), u3) > 0


def test_a_run_passing_over_a_pad_is_left_as_laid():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 300, 300, "N"), _pad("U3", 0, 150, "N")]
    _path(b, "N", [(0, 0), (0, 300), (300, 300)])        # runs straight over U3
    before = [(t.x1, t.y1, t.x2, t.y2) for t in _tracks(b, "N")]
    r = gloss(b)
    assert r.relaid == 0 and [(t.x1, t.y1, t.x2, t.y2) for t in _tracks(b, "N")] == before


def test_only_the_tracks_named_are_laid_again():
    b = _board()
    b.pads += [_pad("U1", 0, 0, "N"), _pad("U2", 300, 200, "N"),
               _pad("U3", 0, 1000, "M"), _pad("U4", 300, 1200, "M")]
    _path(b, "M", [(0, 1000), (0, 1200), (300, 1200)])      # the board's own
    own = [(t.x1, t.y1, t.x2, t.y2) for t in _tracks(b, "M")]
    first = len(b.tracks)
    _path(b, "N", [(0, 0), (0, 200), (300, 200)])           # a router's
    r = gloss(b, tracks=b.tracks[first:])
    assert r.relaid == 1 and r.nets == ["N"]
    assert [(t.x1, t.y1, t.x2, t.y2) for t in _tracks(b, "M")] == own
