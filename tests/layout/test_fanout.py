# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""BGA fanout: finding the lattice, and giving the inner balls vias."""

from __future__ import annotations

from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Region, Rule
from eda_agent.layout.route import Router
from eda_agent.layout.route.fanout import find_bgas
from eda_agent.layout.route.stages import StageOptions


def _rules(gap=4.0, width=4.0):
    return [Rule("Clearance", "0", "All", "All", 1, True, {"gap": gap},
                 f"Clearance Constraint (Gap={gap}mil) (All),(All)"),
            Rule("Width", "1", "All", "All", 1, True, {},
                 f"Width Constraint (Min={width}mil) (Max={width}mil) (Preferred={width}mil) (All)"),
            Rule("Vias", "2", "All", "All", 1, True, {},
                 "Routing Via (MinHoleWidth=6mil) (MaxHoleWidth=10mil) (PreferredHoleWidth=8mil) "
                 "(MinWidth=12mil) (MaxWidth=18mil) (PreferedWidth=16mil) (All)")]


def _bga(pitch: float, ball: float, n: int = 6, x0: float = 200.0, y0: float = 200.0,
         nets=None) -> list[Pad]:
    pads = []
    for i in range(n):
        for j in range(n):
            net = nets(i, j) if nets else f"N{i}_{j}"
            pads.append(Pad("U1", f"{chr(65 + j)}{i + 1}", x0 + pitch * i, y0 + pitch * j,
                            net=net, copper=[PadCopper("TopLayer", "round", ball, ball)]))
    return pads


def _board(pads, layers=("TopLayer", "MidLayer1", "MidLayer2", "BottomLayer")):
    return LayoutBoard(name="f", outline=[(0, 0), (700, 0), (700, 700), (0, 700)],
                       layers=[Layer(n, "signal", i) for i, n in enumerate(layers)],
                       pads=pads, rules=_rules())


def test_a_lattice_of_round_pads_is_found_with_its_pitch_and_rings():
    bgas = find_bgas(_board(_bga(19.685, 12.0)))
    assert len(bgas) == 1
    g = bgas[0]
    assert abs(g.pitch - 19.685) < 1e-6 and g.ball == 12.0
    assert sorted(set(g.ring.values())) == [0, 1, 2]


def test_rectangular_pads_in_a_row_are_not_a_bga():
    pads = [Pad("U2", str(k), 100 + 20 * (k % 8), 100 + 20 * (k // 8), net=f"M{k}",
                copper=[PadCopper("TopLayer", "rect", 10, 30)]) for k in range(32)]
    assert find_bgas(_board(pads)) == []


def _with_partners(pads):
    """Give every ball a partner pad round the board's edge, so each
    ball's net has somewhere to go."""
    out = list(pads)
    n = len(pads)
    side = -(-n // 4)
    for k, p in enumerate(pads):
        e, m = divmod(k, side)
        t = 30 + m * (640 / max(side - 1, 1))
        x, y = [(t, 15), (685, t), (t, 685), (15, t)][e]
        out.append(Pad(f"R{k}", "1", x, y, net=p.net,
                       copper=[PadCopper("TopLayer", "rect", 8, 8)]))
    return out


def test_at_half_a_millimetre_every_ball_past_the_outer_ring_gets_a_via():
    balls = _bga(19.685, 12.0)
    r = Router(_board(_with_partners(balls)))
    by_pad = r.fanout.by_pad
    outer = [i for i, p in enumerate(r.board.pads) if p.comp == "U1"
             and min(int(round((p.x - 200) / 19.685)), int(round((p.y - 200) / 19.685)),
                     5 - int(round((p.x - 200) / 19.685)), 5 - int(round((p.y - 200) / 19.685))) == 0]
    inner = [i for i, p in enumerate(r.board.pads) if p.comp == "U1" and i not in outer]
    assert all(i in by_pad for i in inner), "no room between 0.5 mm balls: every inner ball needs a via"
    assert not any(i in by_pad for i in outer), "the outer ring leaves on its own layer"
    # No room for a dog-bone at this pitch: every via is in its ball, at the centre.
    for i in inner:
        v, p = by_pad[i], r.board.pads[i]
        assert (v.x, v.y) == (p.x, p.y)


def test_at_a_millimetre_the_vias_are_dog_bones_pointing_outwards():
    # Three rings leave on top at this pitch; the fourth and fifth need vias.
    balls = _bga(39.37, 18.0, n=10, x0=170.0, y0=170.0)
    r = Router(_board(_with_partners(balls)))
    assert r.fanout.by_pad
    cx = sum(p.x for p in balls) / len(balls)
    cy = sum(p.y for p in balls) / len(balls)
    for i, v in r.fanout.by_pad.items():
        p = r.board.pads[i]
        assert (v.x, v.y) != (p.x, p.y), "a dog-bone, not a via in the ball"
        assert (v.x - p.x) * (p.x - cx) >= 0 and (v.y - p.y) * (p.y - cy) >= 0
    assert len(r.fanout.tracks) == len(r.fanout.by_pad), "each dog-bone has its stub"


def test_a_fanned_out_board_routes_clean_and_the_callers_board_is_untouched():
    # A 4 x 4 part: the middle four get vias and leave on the inner
    # layers, between balls that have none.
    balls = _bga(19.685, 12.0, n=4)
    b = _board(_with_partners(balls))
    before = (len(b.vias), len(b.tracks))
    # Routed with rip-up and repair: one connection at a time leaves N1_3 unmade.
    r = Router(b, stages=StageOptions(negotiate=True))
    assert len(r.fanout.by_pad) == 4
    r.run()
    out = r.apply()
    assert (len(b.vias), len(b.tracks)) == before
    drc = run_drc(out)
    assert not drc.violations
    assert drc.completion == 1.0, drc.unrouted


def test_a_ball_beside_a_fanout_via_takes_only_the_via_that_fits_there():
    # The room at each ball's centre is judged against the fanout's vias:
    # a 16 mil via in a ball 19.685 mil from one stands 3.685 mil from it,
    # where 4 is required.
    r = Router(_board(_with_partners(_bga(19.685, 12.0, n=4))))
    assert r.fanout.by_pad
    checked = 0
    for job in r.jobs:
        for (y, x), (px, py, room) in job.pad_via.items():
            exact = r._room_at(job.id, px, py, max(v.diameter for v in job.vias) / 2)
            assert room <= exact + 1e-9, (job.name, room, exact)
            checked += 1
            fits = [k for k, v in enumerate(job.vias) if room >= v.diameter / 2 - 1e-9]
            if fits:
                assert r._style_at(job, y, x) == fits[0]
    assert checked


def test_a_ball_of_a_plane_net_is_done_once_its_via_is_in_the_plane():
    balls = _bga(19.685, 12.0, nets=lambda i, j: "GND" if (i + j) % 2 else f"N{i}_{j}")
    b = _board(_with_partners(balls))
    b.regions.append(Region("MidLayer1", [(-5, -5), (705, -5), (705, 705), (-5, 705)], [],
                            "GND", "pour_boundary"))
    r = Router(b)
    gnd = [j for j in r.jobs if j.name == "GND"]
    fanned = {i for i in r.fanout.by_pad if r.board.pads[i].net == "GND"}
    assert fanned
    if gnd:
        assert not fanned & {t.pad for t in gnd[0].terminals}


def test_a_path_leaving_a_dog_bone_on_an_inner_layer_draws_no_stub_there():
    # The stub from a ball's centre belongs on the ball's own layer. On an
    # inner layer the path starts at the via, whose copper covers its
    # cell; a stub from the ball's centre there crossed ground the router
    # never checked it on.
    balls = _bga(39.37, 18.0, n=10, x0=170.0, y0=170.0)
    b = _board(_with_partners(balls))
    r = Router(b)
    i, v = next(iter(r.fanout.by_pad.items()))
    pad = r.board.pads[i]
    job = next(j for j in r.jobs if j.name == pad.net)
    ci, cj = r.grid.spec.cell(v.x, v.y)
    inner = r.grid.layer_index["MidLayer1"]
    path = [(inner, cj, ci), (inner, cj, ci + 1), (inner, cj, ci + 2)]
    out = r.board.__class__.from_dict(r.board.to_dict())
    n = len(out.tracks)
    r._emit_path(job, path, out, r.grid.spec, r.grid.layers, set(), out.copper_layers())
    for t in out.tracks[n:]:
        assert t.layer != "MidLayer1" or (t.x1, t.y1) != (pad.x, pad.y), t
