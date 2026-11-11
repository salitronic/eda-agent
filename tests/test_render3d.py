# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The board render: the scene built from what Altium reports, and the
picture made from it.

The outline walk once dropped the first point of every arc, so a straight
edge meeting an arc was joined by a diagonal: wedges where a mounting tab
met the board. The side picture decides, texel by texel, what a fab
would leave there.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from eda_agent import render3d
from eda_agent.render3d import board as B
from eda_agent.render3d import engine as E

# A 1000 x 1000 mil square board with a semicircular tab on top: the tab's
# arc starts where its straight side ends.
OUTLINE = [
    {"kind": "line", "x": 0, "y": 0},
    {"kind": "line", "x": 1000, "y": 0},
    {"kind": "line", "x": 1000, "y": 1000},
    {"kind": "line", "x": 600, "y": 1000},
    {"kind": "arc", "x": 600, "y": 1000, "cx": 500, "cy": 1000, "radius": 100,
     "angle1": 0, "angle2": 180},
    {"kind": "line", "x": 400, "y": 1000},
    {"kind": "line", "x": 0, "y": 1000},
]


def _geom(**extra):
    g = {"outline": OUTLINE, "tracks": [], "arcs": [], "pads": [], "vias": [],
         "regions": [], "texts": [], "components": []}
    g.update(extra)
    return g


def test_an_arc_is_walked_from_its_own_start_point():
    pts = B.outline_points(OUTLINE)
    assert (600.0, 1000.0) in [(round(x, 6), round(y, 6)) for x, y in pts]
    i = [round(p[0], 6) for p in pts].index(600.0)
    # Straight on from the start: the next point is on the arc, not a
    # chord cut across from the corner before it.
    nxt = pts[i + 1]
    assert abs(math.dist(nxt, (500, 1000)) - 100) < 1e-6
    assert max(y for _, y in pts) == pytest.approx(1100, abs=0.5)


def test_an_arc_given_end_first_is_walked_from_the_vertex():
    outline = [dict(v) for v in OUTLINE]
    outline[4].update(angle1=180, angle2=0)       # same arc, other way round
    outline[4].update(x=600, y=1000)
    pts = B.outline_points(outline)
    assert all(y <= 1000.5 or abs(math.dist(p, (500, 1000)) - 100) < 1e-6
               for p in pts for y in [p[1]])


def test_the_thickness_is_the_stack_and_a_bad_stack_falls_back():
    stack = {"layers": [{"copper_thickness_mils": 1.4, "dielectric_height_mils": 60},
                        {"copper_thickness_mils": 1.4, "dielectric_height_mils": 0}]}
    assert B.board_thickness_mm(stack) == pytest.approx(62.8 * 0.0254)
    assert B.board_thickness_mm({}) == 1.6


def _picture(geom, side="top", look=None):
    pts = B.outline_points(geom["outline"])
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    frame = (min(xs) - 2, min(ys) - 2, max(xs) - min(xs) + 4, max(ys) - min(ys) + 4)
    return B.side_picture(geom, [], side, frame, 0.2, pts, look or B.Look())


def _at(pic, x, y, k=0.2, x0=-2, y0=-2, h_mils=1104):
    """The texel under board point (x, y) mils."""
    col = int((x - x0) * k)
    row = int((y0 + h_mils - y) * k)
    return pic.rgba[row, col], pic.params[row, col]


def test_a_pad_is_exposed_finish_and_silk_over_it_is_clipped():
    pad = {"x": 300, "y": 300, "x_size": 100, "y_size": 100, "shape": "Rectangular",
           "layer": "TopLayer", "hole_size": 0}
    silk = {"x1": 200, "y1": 300, "x2": 600, "y2": 300, "width": 10, "layer": "TopOverlay"}
    pic = _picture(_geom(pads=[pad], tracks=[silk]))
    rgba, params = _at(pic, 300, 300)
    assert tuple(rgba[:3]) == B.FINISHES["enig"][0]
    assert params[1] == 255 and params[2] == 0          # metal, no mask coat
    rgba, params = _at(pic, 500, 300)
    assert tuple(rgba[:3]) == B.SILKS["white"]
    rgba, params = _at(pic, 700, 700)
    assert tuple(rgba[:3]) == B.MASKS["green"] and params[2] == 255


def test_a_hole_and_the_outside_are_cut_out():
    via = {"x": 500, "y": 500, "size": 40, "hole_size": 20}
    pic = _picture(_geom(vias=[via]), look=B.Look(tent_vias=False))
    assert _at(pic, 500, 500)[0][3] == 0
    assert _at(pic, 100, 1080)[0][3] == 0                # beside the tab


def test_a_tented_via_is_mask_sagging_into_the_drill():
    via = {"x": 500, "y": 500, "size": 40, "hole_size": 20}
    pic = _picture(_geom(vias=[via]))
    centre, ring = _at(pic, 500, 500), _at(pic, 517, 500)
    assert centre[0][3] == 255 and tuple(centre[0][:3]) == B.MASKS["green"]
    assert ring[1][3] > centre[1][3]                     # copper stands under the ring


def test_pads_of_a_fitted_part_show_solder_and_get_joints():
    pad = {"x": 300, "y": 300, "x_size": 60, "y_size": 40, "shape": "Rectangular",
           "layer": "TopLayer", "hole_size": 0, "comp": "R1"}
    other = dict(pad, x=700, comp="TP1")
    geom = _geom(pads=[pad, other])
    geom["components"] = [{"des": "R1", "x": 350, "y": 300, "layer": "TopLayer"}]
    pts = B.outline_points(geom["outline"])
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    frame = (min(xs) - 2, min(ys) - 2, max(xs) - min(xs) + 4, max(ys) - min(ys) + 4)
    pic = B.side_picture(geom, [], "top", frame, 0.2, pts, B.Look(), frozenset({"R1"}))
    assert tuple(_at(pic, 300, 300)[0][:3]) == B.SOLDER[0]
    assert tuple(_at(pic, 700, 300)[0][:3]) == B.FINISHES["enig"][0]
    mesh = B.Mesh()
    assert B.solder_joints(mesh, geom, frozenset({"R1"}), lambda x, y: (x * 0.0254, y * 0.0254),
                           1.6, 5) == 1
    z = [p[2] for p in mesh.pos]
    # It climbs towards the part: highest on the inner side, flat at the outer edge.
    assert max(z) > 1.65 and min(z) >= 1.6
    inner = max(p[2] for p in mesh.pos if p[0] > 300 * 0.0254)
    outer = max(p[2] for p in mesh.pos if p[0] < 272 * 0.0254)
    assert inner > outer


def test_copper_under_mask_is_lighter_than_bare_mask():
    track = {"x1": 100, "y1": 800, "x2": 900, "y2": 800, "width": 40, "layer": "TopLayer"}
    pic = _picture(_geom(tracks=[track]))
    over_cu = _at(pic, 500, 800)[0][:3].astype(int)
    bare = _at(pic, 500, 600)[0][:3].astype(int)
    assert over_cu.sum() > bare.sum()


def test_bottom_text_runs_left_from_its_anchor():
    text = {"x": 800, "y": 500, "text": "ABC", "size": 60, "rotation": 0,
            "layer": "BottomOverlay", "hidden": False}
    pic = _picture(_geom(texts=[text]), side="bottom")
    silk = np.all(pic.rgba[..., :3] == B.SILKS["white"], axis=-1)
    rows, cols = np.nonzero(silk)
    xs = cols / 0.2 - 2
    ys = (-2 + 1104) - rows / 0.2
    assert xs.max() <= 801 and xs.min() < 760
    assert ys.min() >= 495 and ys.max() > 520            # above the anchor


def test_views_and_backgrounds():
    assert E.view_for("top", None, None, 1).ortho
    v = E.view_for("iso", 10, 20, 2)
    assert (v.azimuth, v.elevation, v.zoom, v.ortho) == (10, 20, 2, False)
    assert E.background_settings("transparent") == ({"background": 1}, None)
    for name in ("studio", "charcoal", "desk"):
        assert E.background_settings(name)[1].name == name
    assert E.background_settings("#ff0000")[0]["background"] == 2
    with pytest.raises(ValueError):
        E.background_settings("purple-ish")


def _scene():
    return B.build(_geom(vias=[{"x": 500, "y": 500, "size": 40, "hole_size": 20}]),
                   {}, [], B.Look(), px_per_mm=8, supersample=1)


def test_the_scene_has_two_faces_an_edge_and_a_barrel():
    scene = _scene()
    arrays = scene.mesh.arrays()
    assert len(scene.textures) == 3                  # top, bottom, edge
    mats = set(arrays["material_ids"].tolist())
    assert {0, 1, 2, 3} <= mats                          # top, bottom, edge, plated wall
    z = arrays["positions"][:, 2]
    assert z.min() == 0 and z.max() == pytest.approx(scene.thickness_mm)


def test_without_the_tracer_a_top_view_is_flat_and_an_angled_one_says_why(monkeypatch):
    monkeypatch.setattr(E, "tracer", lambda: None)
    scene = _scene()
    img, info = E.render(scene, E.view_for("top", None, None, 1), 320, 240, "draft", "studio")
    assert img.size == (320, 240) and info["renderer"] == "flat"
    with pytest.raises(RuntimeError, match=r"eda-agent\[render3d\]"):
        E.render(scene, E.view_for("iso", None, None, 1), 320, 240, "draft", "studio")


def test_with_the_tracer_the_board_is_in_the_picture():
    if E.tracer() is None:
        pytest.skip("eda-agent-render is not installed")
    scene = _scene()
    img, info = E.render(scene, E.view_for("iso", None, None, 1), 160, 100, "draft",
                         "transparent")
    alpha = np.asarray(img)[..., 3]
    assert info["renderer"].startswith("path tracer")
    assert 0.05 < (alpha > 0).mean() < 0.95             # the board, and space around it


def test_the_tool_refuses_bad_choices_before_reading_the_board(monkeypatch):
    import asyncio

    from eda_agent.tools import render as tool

    monkeypatch.setattr(tool, "get_bridge", lambda: (_ for _ in ()).throw(AssertionError))
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    tool.register_render_tools(_Mcp())
    fn = captured["pcb_render_3d"]
    for kw, words in (({"mask_color": "pink"}, "mask"), ({"quality": "ultra"}, "quality"),
                      ({"view": "above"}, "view"), ({"width": 5}, "pixels"),
                      ({"background": "nope"}, "background")):
        out = asyncio.run(fn(**kw))
        assert out["ok"] is False and words in out["reason"], kw


def test_a_track_through_a_pour_keeps_its_copper():
    track = {"x1": 100, "y1": 500, "x2": 900, "y2": 500, "width": 10, "layer": "TopLayer"}
    pour = {"layer": "TopLayer", "copper": [{
        "outlines": [[(0, 0), (1000, 0), (1000, 1000), (0, 1000)]],
        "holes": [[(50, 485), (950, 485), (950, 515), (50, 515)]]}]}
    geom = _geom(tracks=[track])
    pts = B.outline_points(geom["outline"])
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    frame = (min(xs) - 2, min(ys) - 2, max(xs) - min(xs) + 4, max(ys) - min(ys) + 4)
    pic = B.side_picture(geom, [pour], "top", frame, 0.2, pts, B.Look())
    lift = tuple(_at(pic, 500, 600)[0][:3])                 # mask over the pour
    assert tuple(_at(pic, 500, 500)[0][:3]) == lift          # the track: copper too
    assert tuple(_at(pic, 500, 512)[0][:3]) == B.MASKS["green"]   # its clearance


def test_the_edge_shows_the_stack_layer_by_layer():
    stack = {"layers": [
        {"copper_thickness_mils": 1.4, "dielectric_type": "PrePreg", "dielectric_height_mils": 8},
        {"copper_thickness_mils": 0.6, "dielectric_type": "Core", "dielectric_height_mils": 42},
        {"copper_thickness_mils": 0.6, "dielectric_type": "PrePreg", "dielectric_height_mils": 8},
        {"copper_thickness_mils": 1.4, "dielectric_type": "none", "dielectric_height_mils": 0}]}
    bands = B._stack_bands(stack, 1.6)
    assert [k for k, _, _ in bands] == ["copper", "prepreg", "copper", "core",
                                        "copper", "prepreg", "copper"]
    assert bands[0][1] == pytest.approx(0) and bands[-1][2] == pytest.approx(1.6)
    w, h, rgba, params = B._edge_texture(50.0, 1.6, stack, B.Look())
    pic = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).astype(int)
    rows = pic[..., :3].mean(axis=(1, 2))
    # Rows run bottom up: the mask's lip at both faces is green, not laminate.
    for r in (0, h - 1):
        g = pic[r, :, :3].mean(axis=0)
        assert g[1] > g[0] + 20
    # A plane's seam of resin is darker than the core's glass around it.
    plane = bands[4]
    seam = int((plane[1] + plane[2]) / 2 / 1.6 * h)
    core = int((bands[3][1] + bands[3][2]) / 2 / 1.6 * h)
    assert rows[seam] < rows[core]


def test_the_tool_asks_for_a_backdrop_before_reading_the_board(monkeypatch):
    import asyncio

    from eda_agent.render3d.backdrops import BACKDROPS
    from eda_agent.tools import render as tool

    monkeypatch.setattr(tool, "get_bridge", lambda: (_ for _ in ()).throw(AssertionError))
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    tool.register_render_tools(_Mcp())
    out = asyncio.run(captured["pcb_render_3d"]())
    assert out["ok"] is False and out["needs_choice"] == "background"
    # Every backdrop the renderer has is offered, and nothing it lacks.
    named = {k for k in out["choices"] if k[0] not in "#<"} - {"transparent"}
    assert named == set(BACKDROPS)


def test_a_plated_hole_has_a_copper_barrel_and_an_unplated_one_bare_laminate():
    pth = {"x": 300, "y": 500, "x_size": 70, "y_size": 70, "shape": "Round",
           "hole_size": 40, "layer": "MultiLayer", "plated": True}
    npth = dict(pth, x=700, x_size=40, y_size=40, plated=False)
    scene = B.build(_geom(pads=[pth, npth]), {}, [], B.Look(), px_per_mm=8, supersample=1)
    arrays = scene.mesh.arrays()
    walls = {}
    for tri, m in zip(arrays["indices"], arrays["material_ids"]):
        x = arrays["positions"][tri, 0].mean()
        if m in (3, 4):
            walls.setdefault(int(m), []).append(x)
    # Board x 300 mils sits left of the centre, 700 right of it.
    assert walls[3] and max(walls[3]) < 0                 # plated: copper wall
    assert walls[4] and min(walls[4]) > 0                 # unplated: laminate wall
    pic = _picture(_geom(pads=[pth, npth]), look=B.Look(mask_expansion_mils=20))
    assert tuple(_at(pic, 300 + 30, 500)[0][:3]) == B.FINISHES["enig"][0]   # plated ring
    assert tuple(_at(pic, 700 + 30, 500)[0][:3]) == B.LAMINATE               # no copper


def test_a_joint_follows_an_oblong_pad_round_a_slot():
    pad = {"x": 500, "y": 500, "x_size": 120, "y_size": 50, "shape": "Round",
           "hole_size": 25, "hole_width": 90, "hole_type": "Slot", "hole_rotation": 0,
           "rotation": 0, "layer": "MultiLayer", "plated": True, "comp": "J1"}
    geom = _geom(pads=[pad], components=[{"des": "J1", "x": 500, "y": 500,
                                          "layer": "TopLayer"}])
    mesh = B.Mesh()
    k = 0.0254
    assert B.solder_joints(mesh, geom, frozenset({"J1"}), lambda x, y: (x * k, y * k),
                           1.6, 5) == 1
    xs = [p[0] / k for p in mesh.pos]
    ys = [p[1] / k for p in mesh.pos]
    assert max(xs) - min(xs) > 110 and max(ys) - min(ys) < 55      # oblong, as the pad
    # Unplated, the same hole takes no solder.
    assert B.solder_joints(B.Mesh(), _geom(pads=[dict(pad, plated=False)],
                                           components=geom["components"]),
                           frozenset({"J1"}), lambda x, y: (x * k, y * k), 1.6, 5) == 0


def test_the_other_side_is_the_board_turned_over_not_mirrored():
    arrays = {"positions": np.array([[1, 2, 0], [1, 2, 1.6]], "<f4"),
              "normals": np.array([[0, 0, -1], [0, 1, 0]], "<f4")}
    out = E._turned_over(arrays, 1.6)
    # The bottom face comes up; x keeps its sense, so text reads as it should.
    assert np.allclose(out["positions"], [[1, -2, 1.6], [1, -2, 0]])
    assert out["normals"].tolist() == [[0, 0, 1], [0, -1, 0]]
