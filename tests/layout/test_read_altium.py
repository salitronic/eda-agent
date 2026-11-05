# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The live board read, on canned replies in Altium's internal units.

The Pascal handler answers in coords (10000 per mil). These replies are
built by hand in those units, so every expected mil value is the coord
divided by 10000 and nothing else.
"""

from __future__ import annotations

import pytest

from eda_agent.layout.read_altium import WrongBoard, read_board

C = 10000  # coords per mil
FILE = r"C:\boards\demo.PcbDoc"


def _replies(pad_pages=None):
    pads = pad_pages or [[
        {"comp": "U1", "name": "1", "x": 100 * C, "y": 200 * C, "rotation": 90.0,
         "layer": "TopLayer", "net": "VCC", "mode": 0, "simple": True,
         "top": ["roundrect", 20 * C, 60 * C], "mid": ["round", 0, 0],
         "bot": ["round", 0, 0],
         "copper": [["TopLayer", "roundrect", 20 * C, 60 * C, 25, 0, 0]],
         "hole": 0, "hole_type": "round", "hole_width": 0, "hole_rotation": 0,
         "plated": True},
        {"comp": "", "name": "MH1", "x": 0, "y": 0, "rotation": 0.0,
         "layer": "MultiLayer", "net": "", "mode": 0, "simple": True,
         "top": ["round", 120 * C, 120 * C], "mid": ["round", 120 * C, 120 * C],
         "bot": ["round", 120 * C, 120 * C],
         "copper": [["TopLayer", "round", 0, 0, 0, 0, 0],
                    ["BottomLayer", "round", 120 * C, 120 * C, 0, 0, 0]],
         "hole": 110 * C, "hole_type": "round", "hole_width": 0,
         "hole_rotation": 0, "plated": True},
    ]]
    board = {"file": FILE, "origin": [0, 0],
             "outline": [[0, 0], [1000 * C, 0], [1000 * C, 500 * C], [0, 500 * C]],
             "layers": [{"id": "TopLayer", "name": "Top", "kind": "signal",
                         "order": 0, "plane_net": "", "copper": 14000},
                        {"id": "InternalPlane1", "name": "GND", "kind": "plane",
                         "order": 1, "plane_net": "GND", "copper": 14000},
                        {"id": "BottomLayer", "name": "Bottom", "kind": "signal",
                         "order": 2, "plane_net": "", "copper": 14000}],
             "mech_layers": [{"id": "Mechanical15", "name": "Mech 15",
                              "kind": "Courtyard Top"}]}
    comps = {"components": [
        {"ref": "U1", "footprint": "QFN", "comment": "", "x": 150 * C, "y": 200 * C,
         "rotation": 90.0, "layer": "TopLayer", "locked": True, "height": 40 * C,
         "bbox": [0, 0, 0, 0],
         "mech": [{"t": [100 * C, 150 * C, 200 * C, 150 * C, 2 * C], "layer": "Mechanical15"},
                  {"t": [100 * C, 250 * C, 200 * C, 250 * C, 2 * C], "layer": "Mechanical15"},
                  {"t": [0, 0, 999 * C, 999 * C, 1 * C], "layer": "Mechanical1"}],
         "bodies": []},
        {"ref": "C1", "footprint": "0402", "comment": "", "x": 500 * C, "y": 100 * C,
         "rotation": 0.0, "layer": "BottomLayer", "locked": False, "height": 0,
         "bbox": [0, 0, 0, 0], "mech": [],
         "bodies": [{"layer": "Mechanical13", "bbox": [480 * C, 90 * C, 520 * C, 110 * C],
                     "height": 22 * C, "standoff": 0}]},
    ]}
    copper = {"copper": [
        {"k": "track", "v": [100 * C, 200 * C, 300 * C, 200 * C, 8 * C],
         "layer": "TopLayer", "net": "VCC", "comp": "", "keepout": False, "in_polygon": False},
        {"k": "via", "v": [300 * C, 200 * C, 24 * C, 12 * C], "low": "TopLayer",
         "high": "BottomLayer", "layer": "MultiLayer", "net": "VCC", "comp": "",
         "keepout": False, "in_polygon": False},
        {"k": "region", "kind": 1, "cutout": True,
         "pts": [[400 * C, 200 * C], [450 * C, 200 * C], [450 * C, 250 * C]], "holes": [],
         "layer": "MultiLayer", "net": "", "comp": "", "keepout": False, "in_polygon": False},
        {"k": "region", "kind": 0, "cutout": False,
         "pts": [[0, 0], [100 * C, 0], [100 * C, 100 * C]], "holes": [],
         "layer": "BottomLayer", "net": "GND", "comp": "", "keepout": False, "in_polygon": True},
        {"k": "fill", "v": [0, 0, 10 * C, 20 * C, 0.0], "layer": "TopLayer", "net": "",
         "comp": "", "keepout": True, "in_polygon": False},
    ], "more": False, "count": 5}
    rules = {"rules": [{"name": "Clearance", "kind": 0, "enabled": True, "priority": 1,
                        "scope1": "All", "scope2": "All",
                        "descriptor": "Clearance Constraint (Gap=6mil) (All),(All)"}],
             "typed": [{"name": "Clearance", "gap": 6 * C}],
             "rooms": [{"name": "PSU", "scope": "InComponentClass('PSU')",
                        "confine_in": True, "bbox": [0, 0, 300 * C, 300 * C]}]}
    classes = {"net_classes": [{"name": "Power", "nets": ["VCC", "GND"]}],
               "diff_pairs": [{"name": "USB", "positive": "DP", "negative": "DN"}]}

    calls = []

    def send(cmd, params):
        calls.append(params)
        section = params["section"]
        if section == "board":
            return board
        if section == "components":
            return comps
        if section == "copper":
            return copper
        if section == "rules":
            return rules
        if section == "classes":
            return classes
        if section == "pads":
            page = int(params["offset"]) // int(params["limit"])
            chunk = pads[page]
            return {"pads": chunk, "count": len(chunk), "more": page + 1 < len(pads)}
        raise AssertionError(section)

    return send, calls


def test_units_are_exact():
    send, _ = _replies()
    b = read_board(send)
    assert b.outline == [(0, 0), (1000, 0), (1000, 500), (0, 500)]
    u1 = b.pads[0]
    assert (u1.x, u1.y, u1.rotation) == (100.0, 200.0, 90.0)
    assert u1.copper[0].w == 20.0 and u1.copper[0].h == 60.0
    assert u1.copper[0].corner_pct == 25.0
    assert b.tracks[0].width == 8.0
    assert b.vias[0].diameter == 24.0 and b.vias[0].hole == 12.0
    assert b.layers[1].copper_mils == pytest.approx(1.4)


def test_layer_stack_and_planes():
    send, _ = _replies()
    b = read_board(send)
    assert b.copper_layers() == ["TopLayer", "InternalPlane1", "BottomLayer"]
    assert b.signal_layers() == ["TopLayer", "BottomLayer"]
    assert b.layers[1].plane_net == "GND"


def test_an_empty_stack_entry_falls_back_to_the_pads_own_size():
    send, _ = _replies()
    mh = read_board(send).pads[1]
    top = [c for c in mh.copper if c.layer == "TopLayer"][0]
    assert top.w == 120.0, "the empty top entry should fall back to the top size"
    assert mh.hole == 110.0 and not mh.is_smd


def test_pads_are_paged_until_the_reply_says_done():
    first = [{"comp": "R1", "name": str(i), "x": i * C, "y": 0, "rotation": 0,
              "layer": "TopLayer", "net": "", "mode": 0, "simple": True,
              "top": ["rect", 10 * C, 10 * C], "mid": ["round", 0, 0],
              "bot": ["round", 0, 0],
              "copper": [["TopLayer", "rect", 10 * C, 10 * C, 0, 0, 0]],
              "hole": 0, "hole_type": "round", "hole_width": 0,
              "hole_rotation": 0, "plated": True} for i in range(3)]
    send, calls = _replies([first[:2], first[2:]])
    b = read_board(send, page=2)
    assert [p.name for p in b.pads] == ["0", "1", "2"]
    offsets = [c["offset"] for c in calls if c["section"] == "pads"]
    assert offsets == ["0", "2"]


def test_courtyard_by_layer_kind_and_the_fallbacks():
    send, _ = _replies()
    b = read_board(send)
    u1 = b.component("U1")
    assert u1.courtyard_source == "courtyard"
    xs = [x for x, _ in u1.courtyard]
    ys = [y for _, y in u1.courtyard]
    # Two courtyard lines, 2 mil wide: the extent includes half the pen.
    assert (min(xs), max(xs), min(ys), max(ys)) == (99.0, 201.0, 149.0, 251.0)
    assert u1.locked
    c1 = b.component("C1")
    assert c1.side == "bottom"
    assert c1.courtyard_source == "body"
    assert c1.height == 22.0


def test_copper_kinds_are_sorted():
    send, _ = _replies()
    b = read_board(send)
    assert len(b.cutouts) == 1
    kinds = sorted(r.kind for r in b.regions)
    assert kinds == ["keepout", "pour"]
    assert b.outline_shape().holes, "the cutout did not reach the outline"


def test_rules_carry_typed_values_and_rooms():
    send, _ = _replies()
    b = read_board(send)
    assert b.rules[0].values == {"gap": 6.0}
    assert b.rules[0].descriptor.startswith("Clearance Constraint")
    assert b.rooms[0].name == "PSU"
    assert b.net_classes == {"Power": ["VCC", "GND"]}
    assert b.diff_pairs[0].positive == "DP"


def test_an_arc_in_the_outline_is_flattened_within_tolerance():
    from eda_agent.layout.read_altium import _outline_points

    # A quarter circle of radius 100 from (100, 0) round to (0, 100).
    raw = [[100 * C, 0, 0, 0, 100 * C, 0.0, 90.0], [0, 100 * C], [0, 0]]
    pts = _outline_points(raw, tol=0.05)
    assert pts[0] == (100.0, 0.0)
    assert pts[-1] == (0.0, 0.0)
    radii = [(x * x + y * y) ** 0.5 for x, y in pts[:-1]]
    assert all(99.9 <= r <= 100.0 + 1e-9 for r in radii)


def test_the_wrong_board_is_refused_before_anything_else_is_read():
    send, calls = _replies()
    with pytest.raises(WrongBoard):
        read_board(send, expect_file=r"C:\other\client.PcbDoc")
    assert [c["section"] for c in calls] == ["board"], (
        "the read went on past the board check, so a fixture could carry "
        "data from a board nobody asked for")


def test_the_right_board_is_read_whatever_the_slashes_and_case():
    send, _ = _replies()
    b = read_board(send, expect_file="c:/BOARDS/demo.pcbdoc")
    assert b.name == "demo.PcbDoc"


def _copper_only(entries):
    """The canned replies with the copper section replaced."""
    send, calls = _replies()

    def patched(cmd, params):
        if params["section"] == "copper":
            return {"copper": entries, "more": False, "count": len(entries)}
        return send(cmd, params)

    return patched


_COMMON = {"comp": "", "keepout": False}


def test_poured_copper_takes_the_net_of_the_pour_that_made_it():
    tri = [[0, 0], [100 * C, 0], [100 * C, 100 * C]]
    b = read_board(_copper_only([
        {"k": "region", "kind": 0, "cutout": False, "copper": True, "pts": tri,
         "holes": [], "layer": "TopLayer", "net": "", "in_polygon": True,
         "pour": "GND_L01", "pour_net": "GND", **_COMMON},
    ]))
    r = b.regions[0]
    assert (r.kind, r.net, r.source) == ("pour", "GND", "polygon:GND_L01")


def test_a_region_that_is_not_copper_is_read_as_a_cutout():
    tri = [[0, 0], [100 * C, 0], [100 * C, 100 * C]]
    b = read_board(_copper_only([
        {"k": "region", "kind": 1, "cutout": False, "copper": False, "pts": tri,
         "holes": [], "layer": "BottomLayer", "net": "", "in_polygon": False,
         **_COMMON},
    ]))
    assert [r.kind for r in b.regions] == ["cutout"]
    assert not b.cutouts, "a polygon cutout is not a hole in the board"


def test_a_via_keeps_only_the_layer_sizes_that_differ():
    b = read_board(_copper_only([
        {"k": "via", "v": [0, 0, 16 * C, 8 * C], "low": "TopLayer",
         "high": "BottomLayer",
         "sizes": [["TopLayer", 16 * C], ["MidLayer1", 8 * C], ["BottomLayer", 16 * C]],
         "layer": "MultiLayer", "net": "GND", "in_polygon": False, **_COMMON},
    ]))
    v = b.vias[0]
    assert v.layer_diameters == {"MidLayer1": 8.0}
    assert v.shape_on("MidLayer1").r == 4.0
    assert v.shape_on("TopLayer").r == 8.0


def test_a_removed_pad_leaves_only_its_barrel():
    pad = {"comp": "J1", "name": "1", "x": 0, "y": 0, "rotation": 0.0,
           "layer": "MultiLayer", "net": "SIG", "mode": 0, "simple": True,
           "top": ["round", 60 * C, 60 * C], "mid": ["round", 60 * C, 60 * C],
           "bot": ["round", 60 * C, 60 * C],
           "copper": [["TopLayer", "round", 60 * C, 60 * C, 0, 0, 0, False],
                      ["MidLayer1", "round", 60 * C, 60 * C, 0, 0, 0, True]],
           "hole": 35 * C, "hole_type": "round", "hole_width": 0,
           "hole_rotation": 0, "plated": True}
    send, _ = _replies([[pad]])
    p = read_board(send).pads[0]
    by_layer = {c.layer: c for c in p.copper}
    assert by_layer["TopLayer"].w == 60.0
    assert (by_layer["MidLayer1"].w, by_layer["MidLayer1"].h) == (35.0, 35.0)


def test_a_pour_inside_another_takes_its_own_net_not_the_outer_one():
    # Replies from before the read named each pour's owner: the owner is
    # found from geometry. The inner pour's edge lies ON its boundary, and
    # it lies inside both boundaries, so it needs both the on-edge rule and
    # the smallest-boundary tie-break to come out right.
    from eda_agent.layout.model import LayoutBoard, Region
    from eda_agent.layout.read_altium import _pour_nets

    outer_b = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    inner_b = [(100, 100), (400, 100), (400, 400), (100, 400)]
    b = LayoutBoard(regions=[
        Region("MidLayer1", outer_b, [], "+3V3", "pour_boundary", source="polygon:outer"),
        Region("MidLayer1", inner_b, [], "CORE", "pour_boundary", source="polygon:inner"),
        Region("MidLayer1", [(5, 5), (995, 5), (995, 995), (5, 995)],
               [[(95, 95), (405, 95), (405, 405), (95, 405)]], "", "pour"),
        Region("MidLayer1", list(inner_b), [], "", "pour"),
    ])
    _pour_nets(b)
    assert [r.net for r in b.regions[2:]] == ["+3V3", "CORE"]


def _nested_pours(inner_net_read):
    """An outer +3V3 pour round an inner CORE pour on one layer: the outer
    pour's copper voids round the inner boundary and round a CORE via; the
    inner pour's copper holds the CORE via and voids round a +3V3 via. The
    inner copper is read with ``inner_net_read``."""
    from eda_agent.layout.model import Layer, LayoutBoard, Region, Rule, Via

    outer_b = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    inner_b = [(100, 100), (400, 100), (400, 400), (100, 400)]
    hole = [(270, 270), (330, 270), (330, 330), (270, 330)]
    b = LayoutBoard(layers=[Layer("TopLayer", order=0), Layer("MidLayer1", order=1),
                            Layer("BottomLayer", order=2)],
                    rules=[Rule("Clearance", "clearance", values={"gap": 5.0},
                                descriptor="Clearance Constraint (Gap=5mil) (All),(All)")])
    b.regions = [
        Region("MidLayer1", outer_b, [], "+3V3", "pour_boundary", source="polygon:outer"),
        Region("MidLayer1", inner_b, [], "CORE", "pour_boundary", source="polygon:inner"),
        Region("MidLayer1", [(5, 5), (995, 5), (995, 995), (5, 995)],
               [[(95, 95), (405, 95), (405, 405), (95, 405)]], "+3V3", "pour",
               source="polygon:outer"),
        Region("MidLayer1", list(inner_b), [hole], inner_net_read, "pour",
               source="polygon:outer" if inner_net_read == "+3V3" else "polygon:inner"),
    ]
    b.vias = [Via(200, 200, 16, 8, "TopLayer", "BottomLayer", "CORE"),
              Via(300, 300, 16, 8, "TopLayer", "BottomLayer", "+3V3")]
    return b


def test_poured_copper_named_for_the_outer_pour_goes_to_the_one_its_contents_prove():
    from eda_agent.layout.drc import run_drc
    from eda_agent.layout.read_altium import repair_pour_nets

    b = _nested_pours("+3V3")
    assert run_drc(b).violations, "read as the outer net, the CORE via overlaps it"
    assert repair_pour_nets(b) == 1
    inner = b.regions[3]
    assert (inner.net, inner.source) == ("CORE", "polygon:inner")
    assert b.regions[2].net == "+3V3"
    assert not run_drc(b).violations


def test_poured_copper_that_holds_only_its_own_net_is_left_as_read():
    from eda_agent.layout.read_altium import repair_pour_nets

    b = _nested_pours("CORE")
    assert repair_pour_nets(b) == 0
    assert [r.net for r in b.regions[2:]] == ["+3V3", "CORE"]


def test_poured_copper_no_boundary_explains_is_left_as_read():
    from eda_agent.layout.read_altium import repair_pour_nets

    b = _nested_pours("+3V3")
    del b.regions[1]            # no CORE boundary: nothing to give it to
    assert repair_pour_nets(b) == 0
    assert b.regions[2].net == "+3V3"


def test_a_cutout_that_is_the_board_shape_is_not_a_hole():
    board_shape = [[0, 0], [1000 * C, 0], [1000 * C, 500 * C], [0, 500 * C]]
    b = read_board(_copper_only([
        {"k": "region", "kind": 3, "cutout": True, "copper": False, "pts": board_shape,
         "holes": [], "layer": "MultiLayer", "net": "", "in_polygon": False, **_COMMON},
        {"k": "region", "kind": 3, "cutout": True, "copper": False,
         "pts": [[10 * C, 10 * C], [20 * C, 10 * C], [20 * C, 20 * C]],
         "holes": [], "layer": "MultiLayer", "net": "", "in_polygon": False, **_COMMON},
    ]))
    assert len(b.cutouts) == 1 and len(b.cutouts[0]) == 3


def test_cutouts_that_hold_pads_or_the_whole_board_are_not_holes():
    from eda_agent.layout.model import LayoutBoard, Pad, PadCopper
    from eda_agent.layout.read_altium import clean_cutouts
    b = LayoutBoard(outline=[(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
    b.pads = [Pad("R1", "1", 250, 250, net="A", copper=[PadCopper("TopLayer", "rect", 10, 10)])]
    b.cutouts = [
        [(0, 0), (500, 0), (500, 500), (0, 500)],          # holds the pad
        [(0, 0), (1000, 0), (1000, 900), (0, 900)],        # most of the board
        [(700, 700), (800, 700), (800, 800), (700, 800)],  # a real hole
    ]
    # One of them holds a pad, so on this board the kind names regions:
    # every one of them goes, the padless ones too.
    assert clean_cutouts(b) == 3
    assert b.cutouts == []
    # With no pad in any and small ones only, a real hole stays.
    b.cutouts = [[(700, 700), (800, 700), (800, 800), (700, 800)]]
    assert clean_cutouts(b) == 0 and len(b.cutouts) == 1


def test_a_cutout_drawn_wholly_on_the_board_edge_is_a_region_not_a_hole():
    from eda_agent.layout.model import LayoutBoard
    from eda_agent.layout.read_altium import clean_cutouts
    # Two squares joined by a narrow strip: a rigid-flex board whose flex
    # strip came back with the cutout kind.
    b = LayoutBoard(outline=[(0, 0), (400, 0), (400, 180), (600, 180), (600, 0), (1000, 0),
                             (1000, 400), (600, 400), (600, 220), (400, 220), (400, 400), (0, 400)])
    strip = [(400, 180), (600, 180), (600, 220), (400, 220)]
    hole = [(100, 100), (200, 100), (200, 200), (100, 200)]
    notch = [(950, 350), (1050, 350), (1050, 450), (950, 450)]
    b.cutouts = [strip, hole, notch]
    assert clean_cutouts(b) == 1
    assert b.cutouts == [hole, notch]


def test_an_unnamed_pours_copper_is_still_set_aside_for_routing():
    """The round LED board's GND pours had no names: their copper read as a
    fixed region, and routing +3V3 after pouring took 8 minutes and failed
    until the agent emptied the pours by hand."""
    from eda_agent.layout.route.job import _without_old_pours
    sq = [[0, 0], [100 * C, 0], [100 * C, 100 * C], [0, 100 * C]]
    b = read_board(_copper_only([
        {"k": "polygon", "pts": sq, "layer": "TopLayer", "net": "GND", "name": "",
         "in_polygon": False, **_COMMON},
        {"k": "region", "kind": 0, "cutout": False, "copper": True, "pts": sq,
         "holes": [], "layer": "TopLayer", "net": "", "in_polygon": True,
         "pour": "", "pour_net": "GND", **_COMMON},
    ]))
    assert {r.source for r in b.regions} == {"polygon:"}
    out, dropped = _without_old_pours(b)
    assert [r.kind for r in out.regions] == ["pour_boundary"]
    assert dropped == ["TopLayer:GND"]
