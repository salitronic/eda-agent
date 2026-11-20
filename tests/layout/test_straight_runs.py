# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Straight and 45 degree runs in place of the grid's staircases: a run
between two bends becomes at most two pieces, each kept only where it
clears every other net's copper exactly, never cutting past a point where
another route of its net meets it."""

from __future__ import annotations

import math

from eda_agent.layout import geom
from eda_agent.layout.drc import run_drc
from eda_agent.layout.route import Router
from eda_agent.layout.route.exact import sharp_bends
from tests.layout.test_route import _board, _pad, _random_board


def _staircase(r, x, y, n):
    """Cells from the one nearest (x, y): n pairs of one step east and one
    step north-east, the staircase the grid lays along a shallow slope."""
    i, j = r.grid.spec.cell(x, y)
    sy = 1 if r.grid.spec.y(1) > r.grid.spec.y(0) else -1
    cells = [(j, i)]
    for _ in range(n):
        cells.append((cells[-1][0], cells[-1][1] + 1))
        cells.append((cells[-1][0] + sy, cells[-1][1] + 1))
    return cells


def _at(r, c):
    return (float(r.grid.spec.x(c[1])), float(r.grid.spec.y(c[0])))


def _octilinear(p, q) -> bool:
    a = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 45.0
    return min(a, 45.0 - a) < 1e-6


def _one_net_board(extra=()):
    b = _board(w=600, h=400, layers=("TopLayer",))
    b.pads = [_pad("R1", "1", 60, 100, "A", size=10.0), _pad("R2", "1", 560, 300, "A", size=10.0)]
    b.pads += list(extra)
    return b


def test_a_staircase_becomes_one_straight_and_one_45_degree_piece():
    r = Router(_one_net_board())
    job = next(j for j in r.jobs if j.name == "A")
    cells = _staircase(r, 100, 100, 20)
    pts = r._straighten(job, 0, cells)
    assert len(pts) == 3, "two pieces"
    assert pts[0] == _at(r, cells[0]) and pts[-1] == _at(r, cells[-1])
    assert all(_octilinear(p, q) for p, q in zip(pts, pts[1:]))
    # The grid's own runs were forty pieces.
    assert len(cells) == 41


def test_a_piece_that_would_clip_a_neighbour_is_refused():
    # A pad of another net on each of the two ways a single piece could
    # take (straight then 45 degrees, or 45 then straight); the staircase
    # passes between them. No piece may come inside their clearance, so
    # the run keeps more of its own bends there.
    probe = Router(_one_net_board())
    cells = _staircase(probe, 100, 100, 20)
    corner_low = _at(probe, (cells[0][0], cells[0][1] + 20))
    corner_high = _at(probe, (cells[-1][0], cells[0][1] + 20))
    blockers = [_pad("U1", "1", *corner_low, "B", size=10.0),
                _pad("U2", "1", *corner_high, "B", size=10.0)]
    r = Router(_one_net_board(blockers))
    job = next(j for j in r.jobs if j.name == "A")
    assert r.grid.spec.cell(100, 100) == probe.grid.spec.cell(100, 100)
    pts = r._straighten(job, 0, cells)
    assert len(pts) > 3, "the two-piece run would cross a pad of B"
    assert pts[0] == _at(r, cells[0]) and pts[-1] == _at(r, cells[-1])
    for p, q in zip(pts, pts[1:]):
        track = geom.capsule(p[0], p[1], q[0], q[1], job.width)
        for pad in blockers:
            assert geom.clearance(track, pad.shape_on("TopLayer")) >= r.c - 1e-6, (p, q)
    assert len(pts) < len(cells), "still fewer pieces than cells"


def _on(pts, p) -> bool:
    return any(geom.clearance(geom.circle(*p, 0.0), geom.capsule(*a, *b, 0.0)) <= 1e-6
               for a, b in zip(pts, pts[1:]))


def test_a_run_is_laid_through_the_cell_where_another_route_meets_it():
    r = Router(_one_net_board())
    job = next(j for j in r.jobs if j.name == "A")
    cells = _staircase(r, 100, 100, 20)
    plain = r._straighten(job, 0, cells)
    meet = cells[17]
    assert not _on(plain, _at(r, meet)), "the two pieces pass the cell by"
    pts = r._straighten(job, 0, cells, {meet: {1}}, number=0)
    assert _on(pts, _at(r, meet)), "the route ending there would be left short"
    # The run's own route ending at its ends is no other route meeting it.
    assert r._straighten(job, 0, cells, {cells[0]: {0}, cells[-1]: {0}}, number=0) == plain


def test_the_routed_board_lays_a_shallow_connection_in_two_pieces():
    # Without turn costs the search's path is a staircase and straightening
    # lays it in two pieces; with them the search itself bends once.
    counts = {}
    for straight, turns in ((False, False), (True, False), (True, True)):
        b = _board(w=600, h=400, layers=("TopLayer",))
        b.pads = [_pad("R1", "1", 100, 100, "A"), _pad("R2", "1", 500, 250, "A")]
        r = Router(b, stages={"straight": straight, "turn_cost": turns})
        r.run()
        out = r.apply()
        drc = run_drc(out)
        assert drc.completion == 1.0 and not drc.violations
        ts = [t for t in out.tracks if t.net == "A"]
        counts[straight, turns] = len(ts)
        if straight:
            assert all(_octilinear((t.x1, t.y1), (t.x2, t.y2)) for t in ts)
            assert sharp_bends(out.tracks, out.pads, out.vias) == []
    assert counts[True, False] == 2 and counts[False, False] > 6
    assert counts[True, True] == 2


def test_a_line_holds_only_when_every_piece_the_first_pass_did_not_lay_clears():
    blocker = _pad("U1", "1", 300, 100, "B", size=10.0)
    r = Router(_one_net_board([blocker]))
    job = next(j for j in r.jobs if j.name == "A")
    first = [(100.0, 160.0), (500.0, 160.0)]
    assert r._line_holds(job, 0, first, first)
    assert r._line_holds(job, 0, first[::-1], first), "the same piece either way round"
    # 2 from the blocker's edge to the track's, where 6 is required.
    assert not r._line_holds(job, 0, [(100.0, 160.0), (300.0, 110.0), (500.0, 160.0)], first)


def _blocked_line_board():
    """A's two pads with a pad of B square on the straight line between
    them: the router goes round it."""
    b = _board(w=600, h=400, layers=("TopLayer",))
    b.pads = [_pad("R1", "1", 100, 100, "A"), _pad("R2", "1", 500, 250, "A"),
              _pad("U1", "1", 300, 175, "B", size=30.0)]
    return b


def test_a_line_whose_new_pieces_fail_keeps_the_first_pass_copper(monkeypatch):
    # Every line laid again is made to run straight from end to end,
    # through B's pad: the second pass must give it back exactly as the
    # first pass laid it.
    def tracks(out):
        return sorted((t.net, t.layer, round(t.x1, 6), round(t.y1, 6), round(t.x2, 6),
                       round(t.y2, 6)) for t in out.tracks)

    r = Router(_blocked_line_board(), stages={"straight": False})
    r.run()
    plain = tracks(r.apply())
    assert len(plain) > 2

    def straight_through(self, job, li, cells, meets=None, pre=(), post=(), number=0):
        spec = self.grid.spec
        start = pre[0] if pre else (float(spec.x(cells[0][1])), float(spec.y(cells[0][0])))
        end = post[-1] if post else (float(spec.x(cells[-1][1])), float(spec.y(cells[-1][0])))
        return [tuple(start), tuple(end)]

    monkeypatch.setattr(Router, "_straighten", straight_through)
    r = Router(_blocked_line_board())
    r.run()
    out = r.apply()
    drc = run_drc(out)
    assert drc.completion == 1.0 and not drc.violations
    assert tracks(out) == plain


def test_straight_runs_add_no_violation_and_lose_no_connection_on_random_boards():
    for seed in range(6):
        made = {}
        for straight in (False, True):
            r = Router(_random_board(seed), stages={"straight": straight})
            r.run()
            out = r.apply()
            drc = run_drc(out)
            assert not drc.violations, (seed, straight)
            made[straight] = (drc.completion,
                              sum(1 for t in out.tracks if t.net and not t.comp))
        assert made[True][0] == made[False][0], seed
        assert made[True][1] <= made[False][1], seed
