# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""With axis_entry a route reaches a surface pad straight out of its
centre along one of its axes, turns only once clear of its copper, never
crosses it, and leaves an IC's elongated pin through its short ends."""
from __future__ import annotations

from eda_agent.layout import geom
from eda_agent.layout.drc import run_drc
from eda_agent.layout.model import Layer, LayoutBoard, Pad, PadCopper, Rule
from eda_agent.layout.pad_entry import pad_entry_audit
from eda_agent.layout.route import Router
from eda_agent.layout.route.stages import StageOptions

WIDTH = 6.0


def _board() -> LayoutBoard:
    rules = [
        Rule("Clearance", "0", "All", "All", 1, True, {"gap": 6.0},
             "Clearance Constraint (Gap=6mil) (All),(All)"),
        Rule("Width", "1", "All", "All", 1, True, {},
             f"Width Constraint (Min={WIDTH}mil) (Max={WIDTH}mil) (Preferred={WIDTH}mil) (All)"),
        Rule("RoutingVias", "2", "All", "All", 1, True, {},
             "Routing Via (MinHoleWidth=10mil) (MaxHoleWidth=10mil) (PreferredHoleWidth=10mil) "
             "(MinWidth=20mil) (MaxWidth=20mil) (PreferedWidth=20mil) (All)"),
    ]
    b = LayoutBoard(name="t", outline=[(0, 0), (700, 0), (700, 500), (0, 500)],
                    layers=[Layer("TopLayer", "signal", 0), Layer("BottomLayer", "signal", 1)],
                    rules=rules)
    # An IC's row of tall pins, and a part beside it: the shortest way from
    # pin 2 leaves it through its long side, between the pins.
    for k, x in enumerate((200, 250, 300, 350), 1):
        b.pads.append(Pad("U1", str(k), x, 250, net="A" if k == 2 else f"N{k}",
                          copper=[PadCopper("TopLayer", "rect", 14.0, 60.0)]))
    b.pads.append(Pad("R1", "1", 520, 250, net="A",
                      copper=[PadCopper("TopLayer", "rect", 24.0, 24.0)]))
    b.pads.append(Pad("R1", "2", 580, 250, net="N9",
                      copper=[PadCopper("TopLayer", "rect", 24.0, 24.0)]))
    return b


def _routed(axis: bool) -> LayoutBoard:
    r = Router(_board(), stages=StageOptions(axis_entry=axis))
    r.run()
    return r.apply()


def _entries(out: LayoutBoard, pad: Pad):
    """(inside end, outside end) of each track of the pad's net that
    starts in its copper and leaves it."""
    s = pad.shape_on("TopLayer")
    inside = lambda x, y: geom.clearance(geom.circle(x, y, 0.0), s) <= 1e-3
    for t in out.tracks:
        if t.net != pad.net:
            continue
        a, b = (t.x1, t.y1), (t.x2, t.y2)
        if inside(*a) != inside(*b):
            yield (a, b) if inside(*a) else (b, a)


def test_an_ic_pin_is_left_through_its_short_end_and_the_turn_clears_it():
    out = _routed(True)
    assert run_drc(out).completion == 1.0 and not run_drc(out).violations
    pin = next(p for p in out.pads if p.comp == "U1" and p.name == "2")
    entries = list(_entries(out, pin))
    assert entries
    for (ix, iy), (ox, oy) in entries:
        assert abs(ix - pin.x) < 1e-6 and abs(iy - pin.y) < 1e-6     # from the centre
        assert abs(ox - ix) < 1e-6                                   # along the long axis
        turn = geom.circle(ox, oy, WIDTH)
        assert geom.clearance(turn, pin.shape_on("TopLayer")) >= 0   # turns clear of it


def test_no_pad_is_entered_slantwise_or_crossed():
    out = _routed(True)
    kinds = {f["kind"] for f in pad_entry_audit(out)["findings"]}
    assert not kinds & {"off_axis", "crosses_pad"}


def test_without_it_the_router_keeps_its_old_entries():
    on, off = _routed(True), _routed(False)
    assert run_drc(off).completion == 1.0
    assert [(t.x1, t.y1, t.x2, t.y2) for t in on.tracks] != \
        [(t.x1, t.y1, t.x2, t.y2) for t in off.tracks]


def _scattered(seed: int) -> LayoutBoard:
    import random
    rng = random.Random(seed)
    b = _board()
    b.pads = []
    for k in range(14):
        w, h = rng.choice([(12, 30), (30, 12), (20, 20), (15, 50), (40, 16)])
        p = Pad(f"P{k}", "1", rng.uniform(60, 640), rng.uniform(60, 440), net=f"N{k % 5}",
                copper=[PadCopper("TopLayer", "rect", w, h)])
        if all(geom.clearance(p.shape_on("TopLayer"), q.shape_on("TopLayer")) > 25
               for q in b.pads):
            b.pads.append(p)
    return b


def _touching_tracks_are_stubs(out: LayoutBoard) -> list:
    """Every track of a pad's net touching its copper starts at its centre
    and runs along one of its axes; what breaks that, per pad."""
    bad = []
    for pad in out.pads:
        s = pad.shape_on("TopLayer")
        for t in out.tracks:
            if t.net != pad.net or geom.clearance(t.shape(), s) > -1e-3:
                continue
            ends = ((t.x1, t.y1), (t.x2, t.y2))
            at_centre = any(abs(x - pad.x) < 1e-6 and abs(y - pad.y) < 1e-6 for x, y in ends)
            along = abs(t.x1 - t.x2) < 1e-6 or abs(t.y1 - t.y2) < 1e-6
            if not (at_centre and along):
                bad.append((pad.comp, [tuple(round(v, 1) for v in e) for e in ends]))
    return bad


def test_on_scattered_boards_only_axis_stubs_touch_a_pad():
    for seed in range(8):
        r = Router(_scattered(seed), stages=StageOptions(axis_entry=True))
        r.run()
        out = r.apply()
        assert not run_drc(out).violations, seed
        assert not _touching_tracks_are_stubs(out), (seed, _touching_tracks_are_stubs(out)[:3])


def _router() -> Router:
    return Router(_board(), stages=StageOptions(axis_entry=True))


def test_a_straightened_piece_may_not_cross_its_own_pad():
    r = _router()
    job = next(j for j in r.jobs if j.name == "A")
    pin = next(p for p in r.board.pads if p.comp == "U1" and p.name == "2")
    li = r.grid.layer_index["TopLayer"]
    across = [(pin.x - 40, pin.y + 10), (pin.x + 40, pin.y - 10)]
    beside = [(pin.x - 40, pin.y + 60), (pin.x + 40, pin.y + 60)]
    assert r._over_own_pads(job, li, across)
    assert not r._over_own_pads(job, li, beside)


def test_the_stubs_come_back_from_straightening_as_laid():
    r = _router()
    job = next(j for j in r.jobs if j.name == "A")
    li = r.grid.layer_index["TopLayer"]
    pin = next(p for p in r.board.pads if p.comp == "U1" and p.name == "2")
    pre = [(pin.x, pin.y), (pin.x, pin.y + 40)]
    post = [(pin.x + 200, pin.y + 40), (pin.x + 200, pin.y)]
    spec = r.grid.spec
    ci, cj = spec.cell(pin.x + 5, pin.y + 40)
    cells = [(cj, ci + k) for k in range(0, 36)]
    pts = r._straighten(job, li, cells, {}, pre, post, 0)
    assert pts[:2] == pre and pts[-2:] == post
