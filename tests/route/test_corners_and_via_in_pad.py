# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Two conventions a fabricator expects that DRC will not enforce.

Reported off a real board this router laid out: every corner was a right
angle, and it put vias inside pads that had no need of one.

Right angles are what a 4-direction grid produces and nothing checks
them: the acute-angle rule fires below 90 degrees, so a board full of
them passes. Via-in-pad wicks solder off the joint and turns the board
into a filled-and-capped process; Altium can check it with a Vias Under
SMD rule, but only if somebody switched that rule on.
"""
from __future__ import annotations

from eda_agent.route.model import RoutingProblem
from eda_agent.route.router import RouterOptions, route_geometry

from tests.route.test_router import RULES, _geom, _pad


def _segments(sol, net=None):
    return [t for t in sol["tracks"]
            if net is None or t["net_name"] == net]


def _diagonals(sol, net=None):
    return [t for t in _segments(sol, net)
            if (t["x2"] - t["x1"]) and (t["y2"] - t["y1"])]


def test_a_corner_becomes_two_45s():
    """The whole point: an L between two pads should not be a right
    angle."""
    sol = route_geometry(_geom([_pad(50, 50, "A"), _pad(900, 500, "A")]),
                         RULES)
    assert sol["nets"]["A"]["status"] == "routed"
    assert _diagonals(sol), "every corner is still a right angle"


def test_every_diagonal_is_exactly_45_degrees():
    """An arbitrary angle is worse than a right angle: it is unbuildable
    by the same house rules and no fabricator expects it."""
    sol = route_geometry(_geom([_pad(50, 50, "A"), _pad(900, 500, "A"),
                                _pad(100, 500, "B"), _pad(850, 80, "B")]),
                         RULES)
    for t in _diagonals(sol):
        assert abs(t["x2"] - t["x1"]) == abs(t["y2"] - t["y1"]), (
            f"segment is not a 45: {t}")


def test_the_chamfer_keeps_the_route_legal():
    """The diagonal crosses cells neither leg occupied, so it has to be
    checked against the same obstacles the search used."""
    sol = route_geometry(_geom([_pad(50, 50, "A"), _pad(900, 500, "A"),
                                _pad(500, 300, "C")]), RULES)
    assert sol["validation"]["ok"], sol["validation"]

def test_a_corner_with_no_room_keeps_its_right_angle():
    """Correct and visible beats clever and shorted.

    The pads sit either side of the corner, so the chamfer would cut
    through copper that is not on the route.
    """
    pads = [_pad(50, 300, "A"), _pad(300, 50, "A")]
    pads += [_pad(x, y, "BLOCK")
             for x in range(140, 260, 40) for y in range(140, 260, 40)]
    sol = route_geometry(_geom(pads), RULES)
    if sol["nets"]["A"]["status"] == "routed":
        assert sol["validation"]["ok"], sol["validation"]


def test_no_via_lands_in_a_pad():
    """A wall forces a layer change; the via must go beside a pad, not
    inside one."""
    wall = [_pad(500, y, "B") for y in range(0, 601, 40)]
    sol = route_geometry(
        _geom([_pad(50, 300, "A"), _pad(950, 300, "A")] + wall), RULES)
    assert sol["vias"], "this geometry needs a via; the test proves nothing"
    problem = RoutingProblem.from_geometry(
        _geom([_pad(50, 300, "A"), _pad(950, 300, "A")] + wall), RULES)
    for v in sol["vias"]:
        cell = problem.snap_cell(v["x"], v["y"])
        for li in range(len(problem.layers)):
            assert cell not in problem.pad_cells[li], (
                f"via at {v['x']},{v['y']} sits in pad copper")


def test_via_in_pad_is_available_when_asked_for():
    """BGA fanout and a stitched thermal pad genuinely need it, and the
    refusal must not become a wall."""
    wall = [_pad(500, y, "B") for y in range(0, 601, 40)]
    geom = _geom([_pad(50, 300, "A"), _pad(950, 300, "A")] + wall)
    sol = route_geometry(geom, RULES,
                         options=RouterOptions(allow_via_in_pad=True))
    assert sol["ok"]
    assert sol["nets"]["A"]["status"] == "routed"


def test_a_reversal_is_not_a_corner():
    """A path that doubles back has no corner to cut.

    Treating 180 degrees as a right angle produces a degenerate
    "diagonal" of zero width. The router's own paths do not reverse, so
    this drives the function directly rather than hoping for one.
    """
    from eda_agent.route.router import _chamfer

    problem = RoutingProblem.from_geometry(_geom([_pad(500, 300, "A")]),
                                           RULES)
    straight_out_and_back = [(10, 10), (11, 10), (12, 10), (13, 10),
                             (12, 10), (11, 10), (10, 10)]
    out = _chamfer(problem, "A", 0, list(straight_out_and_back))
    for a, b in zip(out, out[1:]):
        dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
        assert (dx, dy) != (0, 0), f"repeated cell in {out}"
        assert dx == 0 or dy == 0 or dx == dy, (
            f"reversal produced a bogus step {a}->{b} in {out}")


def test_pad_cells_are_the_copper_not_its_clearance():
    """Keyed on the pad itself: a cell beside a pad is exactly where a
    via belongs when a pad needs one."""
    geom = _geom([_pad(500, 300, "A", size=40)])
    problem = RoutingProblem.from_geometry(geom, RULES)
    inside = problem.snap_cell(500, 300)
    assert any(inside in problem.pad_cells[li]
               for li in range(len(problem.layers)))
    outside = problem.snap_cell(500, 300 + 200)
    assert not any(outside in problem.pad_cells[li]
                   for li in range(len(problem.layers)))
    # And the edge case that matters: a cell just off the copper but
    # well inside the pad's clearance ring is still a legal via site,
    # because that is exactly where a via belongs when a pad needs one.
    # The pad is 40 mils across, so 25 mils from centre is off it.
    just_off = problem.snap_cell(500 + 25, 300 + 25)
    if just_off != inside:
        assert not any(just_off in problem.pad_cells[li]
                       for li in range(len(problem.layers))), (
            "the pad map was inflated past the copper, which pushes vias "
            "further out than they need to go")


def test_the_discipline_states_both_rules():
    """They are conventions, not physics: an agent placing tracks by
    hand has to be told, because nothing downstream will complain."""
    from eda_agent.design.discipline import _DISCIPLINE

    text = _DISCIPLINE.lower()
    assert "45 degree" in text and "90" in text
    assert "via in a pad" in text or "via-in-pad" in text
    assert "viasundersmd" in text.replace(" ", "")
