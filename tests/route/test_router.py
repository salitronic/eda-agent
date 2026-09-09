# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the Manhattan router on synthetic boards (all mils).

Scenarios: two-pad net, crossing nets forcing vias, single-layer
detour, multi-pin star, congestion failure reported honestly,
clearance respected, determinism, and degenerate inputs. Boards are
tiny (~41x25 cells at 25 mil pitch) so the suite stays fast.
"""

from __future__ import annotations

import copy

from eda_agent.route.model import (
    RoutingProblem,
    dist_point_seg,
    dist_seg_rect,
    dist_seg_seg,
)
from eda_agent.route.router import (
    RouterOptions,
    route_geometry,
    route_problem,
    validate_solution,
)

RULES = {
    "clearance_mils": 10,
    "track_width_mils": {"default": 10, "power": 20},
    "via_size_mils": 50,
    "via_drill_mils": 28,
    "layers": ["TopLayer", "BottomLayer"],
}


def _pad(x, y, net="", layer="TopLayer", size=40):
    return {"x": x, "y": y, "x_size": size, "y_size": size,
            "shape": "Rectangular", "layer": layer, "net": net,
            "rotation": 0}


def _geom(pads, bbox=(0, 0, 1000, 600), tracks=None, vias=None):
    return {
        "bbox": {"x1": bbox[0], "y1": bbox[1],
                 "x2": bbox[2], "y2": bbox[3]},
        "pads": pads,
        "tracks": tracks or [],
        "vias": vias or [],
    }


# ---------------------------------------------------------------------------
# Connectivity checker: union-find over emitted copper
# ---------------------------------------------------------------------------


def _connected(solution, pad_points, layers=("TopLayer", "BottomLayer")):
    """True if all ``pad_points`` [(x, y, layer-or-None)] sit on one
    connected copper island of the solution (tracks + vias). Handles
    T-junctions (an endpoint landing mid-segment) and vias joining
    layers."""
    parent: dict = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    tracks = solution["tracks"]
    nodes = set()
    for t in tracks:
        nodes.add((t["layer"], t["x1"], t["y1"]))
        nodes.add((t["layer"], t["x2"], t["y2"]))
    for v in solution["vias"]:
        for lay in layers:
            nodes.add((lay, v["x"], v["y"]))
    for (x, y, lay) in pad_points:
        for l2 in ([lay] if lay else list(layers)):
            nodes.add((l2, x, y))

    for t in tracks:
        union((t["layer"], t["x1"], t["y1"]), (t["layer"], t["x2"], t["y2"]))
    for v in solution["vias"]:
        first = (layers[0], v["x"], v["y"])
        for lay in layers[1:]:
            union(first, (lay, v["x"], v["y"]))
    for (x, y, lay) in pad_points:
        if not lay:
            first = (layers[0], x, y)
            for l2 in layers[1:]:
                union(first, (l2, x, y))
    # Merge any node that lies ON a same-layer segment (T-junction).
    for (lay, x, y) in nodes:
        for t in tracks:
            if t["layer"] != lay:
                continue
            if dist_point_seg(x, y, t["x1"], t["y1"],
                              t["x2"], t["y2"]) <= t["width"] / 2 + 1e-6:
                union((lay, x, y), (t["layer"], t["x1"], t["y1"]))

    reps = set()
    for (x, y, lay) in pad_points:
        l2 = lay if lay else layers[0]
        reps.add(find((l2, x, y)))
    return len(reps) == 1


def _track_keys_ok(t):
    assert set(t) == {"x1", "y1", "x2", "y2", "width", "layer", "net_name"}
    for k in ("x1", "y1", "x2", "y2", "width"):
        assert isinstance(t[k], int)
    assert isinstance(t["layer"], str)
    assert isinstance(t["net_name"], str)


def _via_keys_ok(v):
    assert set(v) == {"x", "y", "net", "size", "hole_size"}
    for k in ("x", "y", "size", "hole_size"):
        assert isinstance(v[k], int)
    assert isinstance(v["net"], str)


# ---------------------------------------------------------------------------
# Basic two-pad net
# ---------------------------------------------------------------------------


class TestTwoPads:
    def test_straight_route(self):
        g = _geom([_pad(100, 300, "SIG"), _pad(900, 300, "SIG")])
        sol = route_geometry(g, RULES)
        assert sol["ok"]
        net = sol["nets"]["SIG"]
        assert net["status"] == "routed"
        assert net["vias"] == []
        assert all(t["layer"] == "TopLayer" for t in net["tracks"])
        assert _connected(sol, [(100, 300, "TopLayer"),
                                (900, 300, "TopLayer")])
        assert sol["validation"]["ok"]
        assert sol["summary"]["completion"] == 1.0
        assert sol["summary"]["routed"] == 1

    def test_output_shapes_match_mcp_tools(self):
        g = _geom([_pad(100, 300, "SIG"), _pad(900, 300, "SIG")])
        sol = route_geometry(g, RULES)
        assert sol["tracks"]
        for t in sol["tracks"]:
            _track_keys_ok(t)
            assert t["width"] == 10
            assert t["net_name"] == "SIG"

    def test_length_reported(self):
        g = _geom([_pad(100, 300, "SIG"), _pad(900, 300, "SIG")])
        sol = route_geometry(g, RULES)
        assert sol["nets"]["SIG"]["length_mils"] >= 800
        assert sol["summary"]["total_length_mils"] >= 800

    def test_off_grid_pads_get_stubs(self):
        # 110 / 890 are not multiples of the 25 mil pitch: the exact pad
        # centers must still appear as track endpoints.
        g = _geom([_pad(110, 300, "SIG"), _pad(890, 300, "SIG")])
        sol = route_geometry(g, RULES)
        ends = {(t["x1"], t["y1"]) for t in sol["tracks"]}
        ends |= {(t["x2"], t["y2"]) for t in sol["tracks"]}
        assert (110, 300) in ends and (890, 300) in ends
        assert _connected(sol, [(110, 300, "TopLayer"),
                                (890, 300, "TopLayer")])
        assert sol["validation"]["ok"]


# ---------------------------------------------------------------------------
# Crossing nets force vias (or a detour) and stay clear of each other
# ---------------------------------------------------------------------------


class TestCrossingNets:
    def _geometry(self):
        return _geom([
            _pad(50, 300, "A"), _pad(950, 300, "A"),
            _pad(500, 50, "B"), _pad(500, 550, "B"),
        ])

    def test_both_routed_with_vias(self):
        sol = route_geometry(self._geometry(), RULES)
        assert sol["ok"]
        assert sol["nets"]["A"]["status"] == "routed"
        assert sol["nets"]["B"]["status"] == "routed"
        # B (shorter) routes first and straight; A must change layers to
        # cross it -- down and back up.
        assert len(sol["vias"]) >= 2
        for v in sol["vias"]:
            _via_keys_ok(v)
            assert v["size"] == 50 and v["hole_size"] == 28
        assert sol["validation"]["ok"]

    def test_a_wall_with_no_way_round_still_uses_vias(self):
        """The via path has to keep working where it is the only path.

        B is a row of pads spanning the full height of the board, so
        there is no planar route at all and A must change layers.
        """
        wall = [_pad(500, y, "B") for y in range(0, 601, 40)]
        sol = route_geometry(
            _geom([_pad(50, 300, "A"), _pad(950, 300, "A")] + wall), RULES)
        assert sol["ok"]
        assert sol["nets"]["A"]["status"] == "routed"
        assert len(sol["vias"]) >= 2
        for v in sol["vias"]:
            _via_keys_ok(v)
            assert v["size"] == 50 and v["hole_size"] == 28
        layers = {t["layer"] for t in sol["tracks"] if t["net_name"] == "A"}
        assert layers == {"TopLayer", "BottomLayer"}
        assert sol["validation"]["ok"]

    def test_short_net_first(self):
        sol = route_geometry(self._geometry(), RULES)
        assert sol["order"] == ["B", "A"]  # HPWL 500 before 900

    def test_no_same_layer_crossing(self):
        sol = route_geometry(self._geometry(), RULES)
        for i, a in enumerate(sol["tracks"]):
            for b in sol["tracks"][i + 1:]:
                if (a["net_name"] != b["net_name"]
                        and a["layer"] == b["layer"]):
                    d = dist_seg_seg(a["x1"], a["y1"], a["x2"], a["y2"],
                                     b["x1"], b["y1"], b["x2"], b["y2"])
                    assert d >= 10 + 10  # half-widths + clearance


# ---------------------------------------------------------------------------
# Single layer: obstacle forces a detour, never a via
# ---------------------------------------------------------------------------


class TestSingleLayerDetour:
    def test_detours_around_obstacle(self):
        rules = dict(RULES, layers=["TopLayer"])
        obstacle = _pad(500, 300, "", size=100)   # unnetted blocker
        g = _geom([_pad(100, 300, "D"), _pad(900, 300, "D"), obstacle])
        sol = route_geometry(g, rules)
        assert sol["nets"]["D"]["status"] == "routed"
        assert sol["vias"] == []
        assert _connected(sol, [(100, 300, "TopLayer"),
                                (900, 300, "TopLayer")],
                          layers=("TopLayer",))
        # Every emitted segment clears the obstacle copper by
        # clearance + half track width.
        for t in sol["tracks"]:
            d = dist_seg_rect(t["x1"], t["y1"], t["x2"], t["y2"],
                              500, 300, 50, 50)
            assert d >= 10 + 5 - 1e-6
        assert sol["validation"]["ok"]

    def test_multilayer_pads_route_on_free_layer(self):
        # A full-height top-layer wall between two through-hole pads:
        # the route should drop to the bottom layer with zero vias
        # (through-hole pads are reachable on every layer).
        wall = {"x1": 500, "y1": 0, "x2": 500, "y2": 600,
                "width": 20, "layer": "TopLayer", "net": ""}
        g = _geom([_pad(100, 300, "T", layer="MultiLayer"),
                   _pad(900, 300, "T", layer="MultiLayer")],
                  tracks=[wall])
        sol = route_geometry(g, RULES)
        assert sol["nets"]["T"]["status"] == "routed"
        assert sol["vias"] == []
        assert all(t["layer"] == "BottomLayer" for t in sol["tracks"])
        assert sol["validation"]["ok"]


# ---------------------------------------------------------------------------
# Multi-pin star (steiner-lite tree)
# ---------------------------------------------------------------------------


class TestStar:
    def test_four_component_star(self):
        centers = [(500, 300), (100, 300), (900, 300), (500, 100),
                   (500, 500)]
        g = _geom([_pad(x, y, "N") for (x, y) in centers])
        sol = route_geometry(g, RULES)
        assert sol["nets"]["N"]["status"] == "routed"
        assert _connected(sol, [(x, y, "TopLayer") for (x, y) in centers])
        assert sol["validation"]["ok"]

    def test_tree_shorter_than_pairwise_chain(self):
        # The tree taps the trunk instead of re-running pin-to-pin: total
        # copper stays well under the sum of all center-to-center runs.
        centers = [(500, 300), (100, 300), (900, 300), (500, 100)]
        g = _geom([_pad(x, y, "N") for (x, y) in centers])
        sol = route_geometry(g, RULES)
        assert sol["summary"]["total_length_mils"] <= 400 + 400 + 200 + 100


# ---------------------------------------------------------------------------
# Congestion: failure is honest
# ---------------------------------------------------------------------------


class TestCongestion:
    def _walled_geometry(self):
        wall = [_pad(500, y, "", size=40) for y in range(0, 601, 50)]
        pads = [_pad(300, 300, "X"), _pad(700, 300, "X"),
                _pad(100, 100, "OK"), _pad(100, 500, "OK")]
        return _geom(pads + wall)

    def test_failed_net_reported(self):
        rules = dict(RULES, layers=["TopLayer"])  # no escape via bottom
        sol = route_geometry(self._walled_geometry(), rules)
        assert sol["ok"]  # tool succeeded; the NET failed
        assert sol["nets"]["X"]["status"] == "failed"
        assert "X" in sol["nets"]["X"]["reason"] or sol["nets"]["X"]["reason"]
        assert sol["nets"]["OK"]["status"] == "routed"
        assert sol["summary"]["routed"] == 1
        assert sol["summary"]["failed"] == 1
        assert sol["summary"]["completion"] == 0.5

    def test_failed_net_leaves_no_copper(self):
        rules = dict(RULES, layers=["TopLayer"])
        sol = route_geometry(self._walled_geometry(), rules)
        assert sol["nets"]["X"]["tracks"] == []
        assert all(t["net_name"] != "X" for t in sol["tracks"])

    def test_second_layer_rescues_the_net(self):
        sol = route_geometry(self._walled_geometry(), RULES)  # 2 layers
        assert sol["nets"]["X"]["status"] == "routed"
        assert len(sol["nets"]["X"]["vias"]) >= 2
        assert sol["summary"]["completion"] == 1.0
        assert sol["validation"]["ok"]


# ---------------------------------------------------------------------------
# Clearance between routed nets
# ---------------------------------------------------------------------------


class TestClearance:
    def test_parallel_nets_validate(self):
        g = _geom([
            _pad(100, 200, "P1"), _pad(900, 200, "P1"),
            _pad(100, 300, "P2"), _pad(900, 300, "P2"),
        ])
        sol = route_geometry(g, RULES)
        assert sol["nets"]["P1"]["status"] == "routed"
        assert sol["nets"]["P2"]["status"] == "routed"
        assert sol["validation"]["ok"]
        for i, a in enumerate(sol["tracks"]):
            for b in sol["tracks"][i + 1:]:
                if (a["net_name"] != b["net_name"]
                        and a["layer"] == b["layer"]):
                    d = dist_seg_seg(a["x1"], a["y1"], a["x2"], a["y2"],
                                     b["x1"], b["y1"], b["x2"], b["y2"])
                    assert d >= a["width"] / 2 + b["width"] / 2 + 10 - 1e-6

    def test_validate_solution_catches_injected_short(self):
        g = _geom([_pad(100, 300, "SIG"), _pad(900, 300, "SIG")])
        prob = RoutingProblem.from_geometry(g, RULES)
        sol = route_problem(prob)
        assert sol["validation"]["ok"]
        # Inject a foreign track crossing the routed one.
        bad = dict(sol)
        bad["tracks"] = sol["tracks"] + [{
            "x1": 500, "y1": 100, "x2": 500, "y2": 500,
            "width": 10, "layer": "TopLayer", "net_name": "EVIL",
        }]
        check = validate_solution(prob, bad)
        assert not check["ok"]
        assert any(v["kind"] == "track_track" for v in check["violations"])

    def test_validate_solution_catches_pad_encroachment(self):
        g = _geom([_pad(100, 300, "SIG"), _pad(900, 300, "SIG"),
                   _pad(500, 100, "OTHER")])
        prob = RoutingProblem.from_geometry(g, RULES)
        sol = route_problem(prob)
        bad = dict(sol)
        bad["tracks"] = sol["tracks"] + [{
            "x1": 400, "y1": 100, "x2": 600, "y2": 100,
            "width": 10, "layer": "TopLayer", "net_name": "SIG",
        }]  # runs straight through OTHER's pad
        check = validate_solution(prob, bad)
        assert not check["ok"]
        assert any(v["kind"] == "track_rect" for v in check["violations"])


# ---------------------------------------------------------------------------
# Net ordering / classes
# ---------------------------------------------------------------------------


class TestNetOrder:
    def test_power_routes_first_and_wider(self):
        g = _geom([
            _pad(100, 100, "VCC"), _pad(900, 500, "VCC"),  # long
            _pad(400, 300, "SIG"), _pad(600, 300, "SIG"),  # short
        ])
        sol = route_geometry(g, RULES, net_classes={"VCC": "power"})
        assert sol["order"][0] == "VCC"  # priority beats length
        assert sol["nets"]["VCC"]["width"] == 20
        assert all(t["width"] == 20 for t in sol["nets"]["VCC"]["tracks"])
        assert sol["nets"]["SIG"]["width"] == 10
        assert sol["validation"]["ok"]

    def test_same_class_orders_short_to_long_then_name(self):
        g = _geom([
            _pad(100, 100, "LONG"), _pad(900, 500, "LONG"),
            _pad(400, 300, "ZB"), _pad(600, 300, "ZB"),
            _pad(400, 400, "AB"), _pad(600, 400, "AB"),
        ])
        sol = route_geometry(g, RULES)
        assert sol["order"] == ["AB", "ZB", "LONG"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def _geometry(self):
        return _geom([
            _pad(50, 300, "A"), _pad(950, 300, "A"),
            _pad(500, 50, "B"), _pad(500, 550, "B"),
            _pad(100, 100, "C"), _pad(900, 100, "C"), _pad(500, 200, "C"),
        ])

    def test_same_input_same_output(self):
        s1 = route_geometry(copy.deepcopy(self._geometry()), RULES)
        s2 = route_geometry(copy.deepcopy(self._geometry()), RULES)
        assert s1 == s2

    def test_input_order_independent(self):
        g1 = self._geometry()
        g2 = self._geometry()
        g2["pads"] = list(reversed(g2["pads"]))
        s1 = route_geometry(g1, RULES)
        s2 = route_geometry(g2, RULES)
        for key in ("tracks", "vias", "nets", "summary", "order"):
            assert s1[key] == s2[key]


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------


class TestDegenerate:
    def test_empty_geometry_fails_cleanly(self):
        sol = route_geometry({}, RULES)
        assert sol == {"ok": False, "reason": sol["reason"]}
        assert "bbox" in sol["reason"] or "pads" in sol["reason"]

    def test_board_with_no_nets(self):
        sol = route_geometry(_geom([_pad(500, 300, "")]), RULES)
        assert sol["ok"]
        assert sol["summary"]["nets_total"] == 0
        assert sol["summary"]["completion"] == 1.0
        assert sol["tracks"] == [] and sol["vias"] == []
        assert sol["validation"]["ok"]

    def test_single_pad_net_skipped(self):
        g = _geom([_pad(500, 300, "LONELY"),
                   _pad(100, 300, "SIG"), _pad(900, 300, "SIG")])
        sol = route_geometry(g, RULES)
        assert sol["nets"]["LONELY"]["status"] == "skipped"
        assert sol["summary"]["skipped"] == 1
        assert sol["summary"]["completion"] == 1.0  # skips don't count

    def test_bad_rules_reported(self):
        g = _geom([_pad(100, 300, "S"), _pad(900, 300, "S")])
        sol = route_geometry(g, {"clearance_mils": -5})
        assert sol["ok"] is False
        assert "clearance" in sol["reason"]

    def test_bad_pitch_reported(self):
        g = _geom([_pad(100, 300, "S"), _pad(900, 300, "S")])
        sol = route_geometry(g, RULES, grid_pitch_mils=0)
        assert sol["ok"] is False

    def test_non_dict_geometry_reported(self):
        assert route_geometry(None, RULES)["ok"] is False  # type: ignore

    def test_coincident_terminals(self):
        # Two pads of one net snapping to the same cell: routable with
        # no grid copper at all.
        g = _geom([_pad(500, 300, "S"), _pad(505, 300, "S")])
        sol = route_geometry(g, RULES)
        assert sol["nets"]["S"]["status"] == "routed"
        assert sol["validation"]["ok"]

    def test_expansion_budget_fails_fast(self):
        g = _geom([_pad(100, 300, "S"), _pad(900, 300, "S")])
        sol = route_geometry(g, RULES,
                             options=RouterOptions(max_expansions=1))
        assert sol["nets"]["S"]["status"] == "failed"
