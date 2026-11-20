# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Plane nets that must really reach their plane, repairs that keep a
clean route or negotiate among the nets in the way, an attempt given up
for the next without its clean-up, the room for a via in a ball judged
exactly, and the search graph left without the arcs no search can
take."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Pad, PadCopper, Region
from eda_agent.layout.route import Router
from tests.layout.test_route import _board, _pad


def _four_layer(pours):
    b = _board(layers=("TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"))
    for layer, net, (x0, y0, x1, y1) in pours:
        b.regions.append(Region(layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [],
                                net, "pour_boundary"))
    return b


def _th(comp, x, y, net):
    return Pad(comp, "1", x, y, net=net, hole=20.0,
               copper=[PadCopper("TopLayer", "round", 40, 40),
                       PadCopper("BottomLayer", "round", 40, 40)])


def test_a_plane_is_its_outline_less_its_holes_even_where_a_hole_reaches_past_it():
    # GND pours most of MidLayer1; a +V pour drawn across its top edge is a
    # hole in it. Filled even-odd, the part of the hole past GND's edge
    # came back as GND plane, and plane vias stood in no copper.
    b = _four_layer([("MidLayer1", "GND", (0, 0, 600, 360)),
                     ("MidLayer1", "+V", (400, 300, 600, 400))])
    b.pads = [_pad("C1", "1", 100, 100, "GND"), _pad("C2", "1", 200, 100, "GND"),
              _pad("C3", "1", 450, 320, "+V"), _pad("C4", "1", 550, 330, "+V")]
    r = Router(b)
    gnd = r._plane_masks()["GND"]
    spec = r.grid.spec

    def at(x, y):
        i, j = spec.cell(x, y)
        return bool(gnd[j, i])

    assert at(100, 100)
    assert not at(500, 330), "inside the hole"
    assert not at(500, 385), "past GND's outline, in the hole's overhang"


def test_a_through_hole_pad_the_plane_does_not_reach_is_routed_to_it():
    # The GND pour covers 85% of the board; one through-hole GND pad sits
    # beyond it. Taken as joined, like every through-hole pad in a plane,
    # it was left apart.
    b = _four_layer([("MidLayer1", "GND", (0, 0, 600, 340))])
    b.pads = [_pad("C1", "1", 100, 100, "GND"), _pad("C2", "1", 500, 100, "GND"),
              _th("J1", 300, 375, "GND"),
              _pad("R1", "1", 100, 250, "A"), _pad("R2", "1", 500, 250, "A")]
    r = Router(b)
    job = next(j for j in r.jobs if j.name == "GND")
    assert job.plane_mask is not None
    assert 2 in {t.pad for t in job.terminals}
    r.run()
    drc = run_drc(r.apply())
    assert drc.completion == 1.0 and not drc.violations


def test_a_plane_route_kept_from_another_attempt_must_still_end_in_the_plane():
    # With two GND planes, a pad beyond the first one's edge took a via into
    # the second. Once the second was given back, the route was kept as it
    # was and the pad counted joined, its via in no GND copper.
    pours = [("MidLayer1", "GND", (0, 0, 600, 340)), ("MidLayer2", "GND", (-10, -10, 610, 410))]
    b = _four_layer(pours)
    b.pads = [_pad("C1", "1", 100, 100, "GND"), _pad("C2", "1", 500, 100, "GND"),
              _pad("C3", "1", 300, 380, "GND"),
              _pad("R1", "1", 100, 250, "A"), _pad("R2", "1", 500, 250, "A")]
    first = Router(b)
    assert {p.layer for p in first.pour_planes} == {"MidLayer1", "MidLayer2"}
    first.report = first.run()
    second = Router(b, released=frozenset({"MidLayer2"}))
    todo = second.warm_from(first)
    assert "GND" in {j.name for j in todo}
    second.run(warm=first)
    drc = run_drc(second.apply())
    assert drc.completion == 1.0 and not drc.violations


def _two_nets():
    b = _board()
    b.pads = [_pad("R1", "1", 100, 100, "A"), _pad("R2", "1", 500, 100, "A"),
              _pad("R3", "1", 100, 300, "B"), _pad("R4", "1", 500, 300, "B")]
    return b


@pytest.mark.parametrize("keep", [True, False])
def test_a_repair_that_finds_a_clean_route_keeps_it(keep):
    r = Router(_two_nets())
    r.run()
    r.options = replace(r.options, repair_clean=keep)
    a = next(j for j in r.jobs if j.name == "A")
    # As the negotiation can leave a net: failed, its room since freed.
    r._unstamp(a)
    a.paths, a.via_at, a.failed = [], {}, 1
    r._repair(1)
    if keep:
        assert a.failed == 0 and a.paths and not r._conflicts(a)
        drc = run_drc(r.apply())
        assert drc.completion == 1.0 and not drc.violations
    else:
        assert a.failed == 1 and not a.paths


@pytest.mark.parametrize("quick", [True, False])
def test_an_attempt_that_stalls_with_a_plane_to_give_back_skips_its_clean_up(quick, monkeypatch):
    # Stalling and repair belong to the negotiated routing.
    r = Router(_two_nets(), stages={"quick_release": quick, "negotiate": True})
    calls = []

    def stalled(self, todo, rep, t0, stall_abort=False, settle=True):
        for job in todo:
            self.route_net(job, 0.5)
        rep.stalled = True
        calls.append(("settle", settle))

    monkeypatch.setattr(Router, "_negotiate", stalled)
    monkeypatch.setattr(Router, "_repair", lambda self, n: calls.append(("repair", n)))
    rep = r.run(stall_abort=True)
    assert rep.unsettled is quick
    if quick:
        assert calls == [("settle", False)]
    else:
        assert calls[0] == ("settle", True) and ("repair", r.repair_rounds) in calls


def test_an_unsettled_attempt_is_never_the_one_kept(monkeypatch):
    from eda_agent.layout.route import router as rmod

    pours = [("MidLayer1", "GND", (-10, -10, 610, 410)), ("MidLayer2", "GND", (-10, -10, 610, 410))]
    b = _four_layer(pours)
    b.pads = [_pad("C1", "1", 100, 100, "GND"), _pad("C2", "1", 500, 100, "GND"),
              _pad("R1", "1", 100, 250, "A"), _pad("R2", "1", 500, 250, "A")]
    real = Router.run
    seen = []

    def run(self, stall_abort=False, warm=None):
        rep = real(self, stall_abort, warm)
        if stall_abort:
            # Say the first attempt stalled and was left as it stood.
            rep.unsettled, rep.failed = True, 0
        seen.append(rep)
        return rep

    monkeypatch.setattr(Router, "run", run)
    best = rmod.route_adaptive(b, stages={"quick_release": True})
    # Counted as no failures, the first attempt ended the search and was
    # kept with its conflicts; unsettled, it is no score and the next runs.
    assert len(seen) == 2 and seen[0].unsettled
    assert best.report is seen[1] and best.released


# ---------------------------------------------------------------------------
# The search graph
# ---------------------------------------------------------------------------

def _all_arcs(win, cell_cost, via_cost, move_factor, pitch, potential):
    """The graph with every arc, nine per layer cell and one per layer per
    via node, unusable ones at BLOCKED: what build_graph made before it
    left those out."""
    from scipy.sparse import csr_matrix

    from eda_agent.layout.route.search import BLOCKED, MOVES

    L, M, h, w = win.L, win.M, win.h, win.w
    cy, cx = win.cy, win.cx
    cc = np.minimum(cell_cost, BLOCKED)
    vc = np.minimum(via_cost, BLOCKED)
    idx = np.empty((L, M, 9), dtype=np.int32)
    wt = np.empty((L, M, 9))
    selfid = np.arange(M)
    base = (np.arange(L) * M)[:, None]
    for k, (dx, dy, length) in enumerate(MOVES):
        ny, nx = cy + dy, cx + dx
        ok = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
        r = np.full(M, -1)
        r[ok] = win.rank[ny[ok], nx[ok]]
        ok &= r >= 0
        nb = np.where(ok, r, selfid)
        idx[:, :, k] = base + nb[None, :]
        wk = cc[:, nb] * ((length * pitch) * move_factor[:, k][:, None])
        wk += (potential[nb] - potential)[None, :]
        np.maximum(wk, 0.0, out=wk)
        wt[:, :, k] = np.where(ok[None, :], wk, BLOCKED)
    blocked = cc >= BLOCKED
    idx[:, :, 8] = win.n_layer + selfid[None, :]
    wt[:, :, 8] = vc[None, :] / 2.0 + blocked * BLOCKED
    data = np.concatenate([wt.reshape(-1), (vc[:, None] / 2.0 + blocked.T * BLOCKED).reshape(-1)])
    ind = np.concatenate([idx.reshape(-1), (base + selfid[None, :]).T.reshape(-1)])
    nL = L * M
    ptr = np.concatenate([np.arange(0, nL * 9, 9), nL * 9 + np.arange(0, M * L + 1, L)])
    return csr_matrix((data, ind, ptr), shape=(win.size, win.size))


def test_the_search_graph_without_unusable_arcs_finds_the_same_paths():
    from scipy.sparse.csgraph import dijkstra

    from eda_agent.layout.route.search import Window, build_graph

    rng = np.random.default_rng(3)
    h, w, L = 30, 40, 2
    mask = rng.random((h, w)) > 0.1                  # a region with holes in it
    win = Window(5, 7, w, h, L, mask)
    cell = rng.uniform(1.0, 4.0, (L, win.M))
    cell[rng.random((L, win.M)) < 0.3] = np.inf      # barred cells
    via = rng.uniform(20.0, 60.0, win.M)
    via[rng.random(win.M) < 0.5] = np.inf
    mf = rng.uniform(1.0, 1.6, (L, 8))
    pot = rng.uniform(0.0, 3.0, win.M)
    lean = build_graph(win, cell, via, mf, 2.5, pot)
    full = _all_arcs(win, cell, via, mf, 2.5, pot)
    assert lean.nnz < full.nnz
    src = np.unique(rng.integers(0, win.n_layer, 6))
    for limit in (40.0, 1e11):
        d1, p1, s1 = dijkstra(full, directed=True, indices=src, return_predecessors=True,
                              min_only=True, limit=limit)
        d2, p2, s2 = dijkstra(lean, directed=True, indices=src, return_predecessors=True,
                              min_only=True, limit=limit)
        assert np.array_equal(d1, d2) and np.array_equal(p1, p2) and np.array_equal(s1, s2)


def _two_gaps():
    """One layer, a wall across it with two gaps a track wide: X near the
    first net's pads and Y further round."""
    b = _board(w=600, h=300, layers=("TopLayer",))

    def keepout(x0, y0, x1, y1):
        b.regions.append(Region("TopLayer", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [], "",
                                "keepout", keepout=True))

    keepout(295, -5, 305, 88)
    keepout(295, 112, 305, 238)
    keepout(295, 262, 305, 305)
    b.pads = [_pad("A1", "1", 230, 100, "A", size=10), _pad("A2", "1", 370, 100, "A", size=10),
              _pad("J1", "1", 100, 100, "J", size=10), _pad("J2", "1", 500, 100, "J", size=10)]
    return b


def test_a_negotiated_repair_trades_room_between_a_failing_net_and_its_blocker():
    r = Router(_two_gaps(), stages={"repair_negotiate": True})
    r.run()
    a, j = (next(x for x in r.jobs if x.name == n) for n in "AJ")
    # J left failed with A in the gap it wanted.
    r._unstamp(j)
    j.paths, j.via_at, j.failed = [], {}, 1
    r._stamp(j)
    assert r._negotiate_locally(j, [a], before=1)
    assert r._walls is None
    assert a.failed == 0 and j.failed == 0
    assert not r._conflicts(a) and not r._conflicts(j)
    drc = run_drc(r.apply())
    assert drc.completion == 1.0 and not drc.violations


def test_while_a_repair_negotiates_other_nets_are_walls_and_its_own_are_not():
    r = Router(_two_gaps())
    r.run()
    a, j = (next(x for x in r.jobs if x.name == n) for n in "AJ")
    win = r._region(None, None)
    cells = [c for p in a.paths for c in p if c[0] >= 0 and c not in a.deep_cells]
    rank = win.rank[[c[1] - win.j0 for c in cells], [c[2] - win.i0 for c in cells]]
    layer = [c[0] for c in cells]
    r._unstamp(j)
    # The cells of A's route that J may enter at a price (not A's pads).
    cell, _ = r._window_costs(j, win, 1.0)
    open_ = np.isfinite(cell[layer, rank])
    assert open_.sum() > 10
    # A outside the group: a wall to J.
    r._walls = (r.occ_t.copy(), r.occ_v.copy())
    cell, _ = r._window_costs(j, win, 1.0)
    assert np.isinf(cell[layer, rank][open_]).all()
    # A in the group: shared at a price.
    r._unstamp(a)
    r._walls = (r.occ_t.copy(), r.occ_v.copy())
    r._stamp(a)
    cell, _ = r._window_costs(j, win, 1.0)
    assert np.isfinite(cell[layer, rank][open_]).all()
    r._walls = None


@pytest.mark.parametrize("exact", [True, False])
def test_the_room_for_a_via_in_a_ball_is_judged_from_the_shapes_round_it(exact):
    # A 0.5 mm grid of 12 mil balls, clearance 5: from a ball's centre the
    # next ball's edge is 19.685 - 6 = 13.685 mil away, room for a via of
    # radius 8.685. Judged from the nearest cell less the distance to it,
    # the room came out short, and a 16 mil via the ball takes was barred.
    pitch = 19.685
    b = _board(w=400, h=400, gap=5.0, via=(16.0, 8.0))
    b.pads = [Pad("U1", f"{i}{j}", 150.3 + i * pitch, 150.7 + j * pitch, net=f"N{i}{j}",
                  copper=[PadCopper("TopLayer", "round", 12, 12)])
              for i in range(4) for j in range(4)]
    b.pads.append(_pad("R1", "1", 300, 300, "N11"))
    r = Router(b, stages={"exact_pad_via": exact})
    job = next(j for j in r.jobs if j.name == "N11")
    ball = next(p for p in b.pads if p.name == "11")
    room = next(v[2] for v in job.pad_via.values() if (v[0], v[1]) == (ball.x, ball.y))
    # Up to the largest via in use: 8 of the 8.685 there is.
    if exact:
        assert room >= 8.0 - 1e-9
    else:
        assert room < 8.0
