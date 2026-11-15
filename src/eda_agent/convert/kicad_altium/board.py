# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The board half of the import: a KiCad board as this server's tool calls.

The board is flipped about the top of its outline and moved so the
outline's lower-left corner is Altium's origin. Footprints keep KiCad's
angle on both sides: seen in Altium's Y-up frame, a KiCad part on the back
is its library footprint mirrored top to bottom and then turned, which is
what Altium does with a part flipped to the bottom (measured on a header
at 90 degrees: every pad on KiCad's).

Nets: Altium names a net from the schematic, KiCad from its own rules, so
the same net can have two names. Copper takes the Altium name of the net
its pads are on (``net_map``); a net with no schematic pin keeps KiCad's.
"""
from __future__ import annotations

import fnmatch
import math
from typing import Any, Optional

from .plan import _clean
from .verify import FOOTPRINT_LAYERS
from ...units import MILS_PER_MM

_TOL = 1e-3


def _k(mm: float) -> float:
    return round(mm * MILS_PER_MM, 4)


class Frame:
    """KiCad board millimetres (Y down) to Altium mils (Y up)."""

    def __init__(self, board: dict[str, Any]):
        xs, ys = [], []
        for item in board.get("outline") or []:
            for key in ("start", "mid", "end", "center"):
                if key in item:
                    xs.append(item[key][0])
                    ys.append(item[key][1])
            for p in item.get("points") or []:
                xs.append(p[0])
                ys.append(p[1])
            if item["kind"] == "circle" and "center" in item and "end" in item:
                r = math.dist(item["center"], item["end"])
                xs += [item["center"][0] - r, item["center"][0] + r]
                ys += [item["center"][1] - r, item["center"][1] + r]
        if not xs:
            for f in board.get("footprints") or []:
                xs.append(f["at"][0])
                ys.append(f["at"][1])
        self.x0 = min(xs) if xs else 0.0
        self.y0 = max(ys) if ys else 0.0

    def __call__(self, p: list[float]) -> list[float]:
        return [_k(p[0] - self.x0), _k(self.y0 - p[1])]


def copper_layer_names(copper: list[str]) -> dict[str, str]:
    """KiCad copper layers to Altium's: F.Cu Top, B.Cu Bottom, the inner
    ones MidLayer1 and on in stack order."""
    out = {"F.Cu": "TopLayer", "B.Cu": "BottomLayer"}
    inner = [c for c in copper if c not in ("F.Cu", "B.Cu")]
    inner.sort(key=lambda n: int(n[2:-3]) if n[2:-3].isdigit() else 0)
    for i, name in enumerate(inner, 1):
        out[name] = f"MidLayer{i}"
    return out


# --- outline ---------------------------------------------------------------------


def _centre(a, m, b) -> Optional[tuple[float, float]]:
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax ** 2 + ay ** 2) * (by - cy) + (bx ** 2 + by ** 2) * (cy - ay)
          + (cx ** 2 + cy ** 2) * (ay - by)) / d
    uy = ((ax ** 2 + ay ** 2) * (cx - bx) + (bx ** 2 + by ** 2) * (ax - cx)
          + (cx ** 2 + cy ** 2) * (bx - ax)) / d
    return ux, uy


def _ccw(a, m, b) -> bool:
    return (m[0] - a[0]) * (b[1] - m[1]) - (m[1] - a[1]) * (b[0] - m[0]) > 0


def _edges(board: dict[str, Any], frame: Frame) -> tuple[list[dict], list[dict]]:
    """Outline edges in Altium's frame, and the closed shapes (circles,
    rectangles, polygons) that are a loop on their own."""
    edges, loops = [], []
    for item in board.get("outline") or []:
        kind = item["kind"]
        if kind == "line":
            edges.append({"a": frame(item["start"]), "b": frame(item["end"])})
        elif kind == "arc":
            a, m, b = frame(item["start"]), frame(item["mid"]), frame(item["end"])
            c = _centre(a, m, b)
            if c is None:
                edges.append({"a": a, "b": b})
            else:
                edges.append({"a": a, "b": b, "centre": [round(c[0], 4), round(c[1], 4)],
                              "ccw": _ccw(a, m, b)})
        elif kind == "circle":
            c = frame(item["center"])
            loops.append({"shape": "circle", "centre": c,
                          "radius": _k(math.dist(item["center"], item["end"]))})
        elif kind == "rect":
            a, b = frame(item["start"]), frame(item["end"])
            loops.append({"shape": "polygon", "points": [
                [a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]]})
        elif kind == "poly":
            loops.append({"shape": "polygon", "points": [frame(p) for p in item["points"]]})
    return edges, loops


def _close(p, q) -> bool:
    return abs(p[0] - q[0]) <= _TOL * MILS_PER_MM and abs(p[1] - q[1]) <= _TOL * MILS_PER_MM


def _chain(edges: list[dict]) -> list[list[dict]]:
    """Edges joined end to end into closed loops, each edge turned to run
    the loop's way."""
    left = list(edges)
    loops = []
    while left:
        loop = [left.pop(0)]
        while not _close(loop[-1]["b"], loop[0]["a"]):
            tail = loop[-1]["b"]
            for i, e in enumerate(left):
                if _close(e["a"], tail):
                    loop.append(left.pop(i))
                    break
                if _close(e["b"], tail):
                    e = left.pop(i)
                    flipped = {"a": e["b"], "b": e["a"]}
                    if "centre" in e:
                        flipped.update(centre=e["centre"], ccw=not e["ccw"])
                    loop.append(flipped)
                    break
            else:
                break
        if _close(loop[-1]["b"], loop[0]["a"]):
            loops.append(loop)
    return loops


def _area(points: list[list[float]]) -> float:
    return abs(sum(p[0] * q[1] - q[0] * p[1]
                   for p, q in zip(points, points[1:] + points[:1]))) / 2


def outline_step(board: dict[str, Any], frame: Frame) -> tuple[Optional[dict], list[str]]:
    """The pcb_set_board_shape call for the largest closed outline."""
    edges, shapes = _edges(board, frame)
    candidates = []
    for loop in _chain(edges):
        pts = []
        for e in loop:
            if "centre" in e:
                pts.append({"x": e["a"][0], "y": e["a"][1], "arc_center": e["centre"],
                            "clockwise": not e["ccw"]})
            else:
                pts.append([e["a"][0], e["a"][1]])
        flat = [[p["x"], p["y"]] if isinstance(p, dict) else p for p in pts]
        candidates.append((_area(flat), {"shape": "polygon", "points": pts}))
    for s in shapes:
        if s["shape"] == "circle":
            candidates.append((math.pi * s["radius"] ** 2, {
                "shape": "circle", "center_x": s["centre"][0], "center_y": s["centre"][1],
                "radius": s["radius"]}))
        else:
            candidates.append((_area(s["points"]), s))
    warnings = []
    if not candidates:
        return None, ["no closed Edge.Cuts outline; the board keeps Altium's default shape"]
    candidates.sort(key=lambda c: -c[0])
    if len(candidates) > 1:
        warnings.append(f"{len(candidates) - 1} inner Edge.Cuts cut-out(s) not imported")
    return {"tool": "pcb_set_board_shape", "args": candidates[0][1]}, warnings


# --- stack ------------------------------------------------------------------------


def stack_step(board: dict[str, Any], doc: str) -> Optional[dict]:
    """pcb_make_layer_stack from KiCad's stack-up: copper and dielectrics,
    top to bottom."""
    layers = []
    names = copper_layer_names(board["copper_layers"])
    for layer in board.get("stackup") or []:
        t = layer["type"].lower()
        if layer["name"].endswith(".Cu"):
            layers.append({"kind": "copper", "name": _altium_layer_title(names[layer["name"]]),
                           "thickness_mils": _k(layer["thickness_mm"] or 0.035)})
        elif t in ("core", "prepreg"):
            d = {"kind": "dielectric", "type": t,
                 "thickness_mils": _k(layer["thickness_mm"] or 0.1)}
            if layer["epsilon_r"]:
                d["constant"] = layer["epsilon_r"]
            if layer["loss_tangent"]:
                d["loss_tangent"] = layer["loss_tangent"]
            if layer["material"]:
                d["material"] = layer["material"]
            layers.append(d)
    if not any(l["kind"] == "copper" for l in layers):
        copper = board["copper_layers"]
        if len(copper) <= 2:
            return None
        # No stack-up written: KiCad's own default, the board's thickness
        # shared equally between the gaps.
        gap = _k(max(board.get("thickness_mm") or 1.6, 0.2) / (len(copper) - 1))
        order = ["F.Cu"] + sorted((c for c in copper if c not in ("F.Cu", "B.Cu")),
                                  key=lambda n: int(n[2:-3]) if n[2:-3].isdigit() else 0) + ["B.Cu"]
        for i, c in enumerate(order):
            if i:
                layers.append({"kind": "dielectric", "type": "core" if i % 2 else "prepreg",
                               "thickness_mils": gap})
            layers.append({"kind": "copper", "name": _altium_layer_title(names[c]),
                           "thickness_mils": _k(0.035)})
    return {"tool": "pcb_make_layer_stack", "args": {"layers": layers, "dry_run": False,
                                                     "board_path": doc}}


def _altium_layer_title(layer: str) -> str:
    if layer == "TopLayer":
        return "Top Layer"
    if layer == "BottomLayer":
        return "Bottom Layer"
    return "Mid-Layer " + layer[len("MidLayer"):]


# --- nets -------------------------------------------------------------------------


def net_map(board: dict[str, Any], altium_pin_net: dict[str, str]) -> dict[str, str]:
    """KiCad net name -> Altium net name, through the pads each net is on."""
    votes: dict[str, dict[str, int]] = {}
    for f in board.get("footprints") or []:
        for p in f["pads"]:
            if not p["net"]:
                continue
            alt = altium_pin_net.get(f"{f['reference']}.{p['number']}")
            if alt:
                votes.setdefault(p["net"], {}).setdefault(alt, 0)
                votes[p["net"]][alt] += 1
    out = {}
    for net in board.get("nets") or []:
        v = votes.get(net)
        out[net] = max(v, key=v.get) if v else _clean(net)
    return out


# --- copper -----------------------------------------------------------------------


def _arc(a, m, b) -> Optional[dict[str, float]]:
    c = _centre(a, m, b)
    if c is None:
        return None
    r = math.dist(c, a)
    s = math.degrees(math.atan2(a[1] - c[1], a[0] - c[0])) % 360
    e = math.degrees(math.atan2(b[1] - c[1], b[0] - c[0])) % 360
    if not _ccw(a, m, b):
        s, e = e, s
    return {"x_center": round(c[0], 4), "y_center": round(c[1], 4), "radius": round(r, 4),
            "start_angle": round(s, 4), "end_angle": round(e, 4)}


def label_fields(label: Optional[dict[str, Any]], key: str, frame: Frame) -> dict[str, Any]:
    """A part's designator (key ``name``) or comment (``comment``) as KiCad
    draws it, for pcb_place_components: shown or hidden, its layer, height,
    stroke, angle and mirror, and the point its middle lands on: KiCad's
    anchor, which is the middle of the text unless it is justified."""
    if not label:
        return {}
    out: dict[str, Any] = {f"{key}_shown": not label["hidden"]}
    layer = FOOTPRINT_LAYERS.get(label["layer"])
    if layer:
        out[f"{key}_layer"] = layer
    x, y = frame(label["at"])
    out.update({f"{key}_height": _k(label["height"]), f"{key}_stroke": _k(label["stroke"]),
                f"{key}_angle": label["angle"], f"{key}_mirror": bool(label["mirror"]),
                f"{key}_x": x, f"{key}_y": y})
    return out


def board_plan(board: dict[str, Any], doc: str, pcblib: str,
               footprint_names: dict[str, str], nets: dict[str, str],
               unique_ids: dict[str, str], net_classes: dict[str, Any]) -> dict[str, Any]:
    """Every step that draws the board, once the schematic is compiled."""
    frame = Frame(board)
    layers = copper_layer_names(board["copper_layers"])
    warnings: list[str] = []
    steps: list[dict[str, Any]] = [
        {"tool": "app_set_active_document", "args": {"file_path": doc}}]
    shape, w = outline_step(board, frame)
    warnings += w
    if shape:
        steps.append(shape)
    stack = stack_step(board, doc)
    if stack:
        # The stack tool refuses an unsaved board.
        steps.append({"tool": "app_save_document", "args": {"file_path": doc}})
        steps.append(stack)

    used = sorted({nets.get(n, n) for n in board["nets"]})
    if used:
        steps.append({"tool": "pcb_create_nets_from_list", "args": {"nets": used}})

    placements = []
    for f in board["footprints"]:
        name = footprint_names.get(f["lib_id"])
        if not name:
            warnings.append(f"{f['reference'] or f['lib_id']}: footprint not built, not placed")
            continue
        back = str(f["layer"]).startswith("B.")
        x, y = frame(f["at"])
        p: dict[str, Any] = {"footprint": name, "library_path": pcblib, "x": x, "y": y,
                             "rotation": round((f["angle"] or 0.0) % 360, 4),
                             "layer": "BottomLayer" if back else "TopLayer"}
        if f["reference"]:
            p["designator"] = f["reference"]
        if f["value"]:
            p["comment"] = f["value"]
        labels = f.get("labels") or {}
        p.update(label_fields(labels.get("name"), "name", frame))
        p.update(label_fields(labels.get("comment"), "comment", frame))
        if f["reference"] in unique_ids:
            p["unique_id"] = unique_ids[f["reference"]]
        pad_nets = {pad["number"]: nets.get(pad["net"], pad["net"]) for pad in f["pads"]
                    if pad["net"] and pad["number"]}
        if pad_nets:
            p["pad_nets"] = pad_nets
        placements.append(p)
    for i in range(0, len(placements), 200):
        steps.append({"tool": "pcb_place_components",
                      "args": {"placements": placements[i:i + 200], "board_path": doc}})

    unknown_layers: set[str] = set()

    def layer_of(name: str) -> Optional[str]:
        if name not in layers:
            unknown_layers.add(name)
            return None
        return layers[name]

    tracks = []
    dots = 0
    for t in board["tracks"]:
        lay = layer_of(t["layer"])
        if lay is None or t["start"] is None or t["end"] is None:
            continue
        a, b = frame(t["start"]), frame(t["end"])
        if a == b:
            # A track that starts where it ends draws nothing more than the
            # copper it touches, and Altium refuses one.
            dots += 1
            continue
        tracks.append({"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "width": _k(t["width"]),
                       "layer": lay, "net_name": nets.get(t["net"], t["net"])})
    for i in range(0, len(tracks), 2000):
        steps.append({"tool": "pcb_place_tracks", "args": {"tracks": tracks[i:i + 2000],
                                                           "board_path": doc}})
    arcs = []
    for t in board["arcs"]:
        lay = layer_of(t["layer"])
        if lay is None:
            continue
        a = _arc(frame(t["start"]), frame(t["mid"]), frame(t["end"]))
        if a is None:
            continue
        arcs.append({**a, "width": _k(t["width"]), "layer": lay,
                     "net": nets.get(t["net"], t["net"])})
    for i in range(0, len(arcs), 1000):
        steps.append({"tool": "pcb_place_arcs", "args": {"arcs": arcs[i:i + 1000],
                                                         "board_path": doc}})
    vias = []
    for v in board["vias"]:
        pair = [layer_of(n) for n in v["layers"][:2]] if len(v["layers"]) >= 2 else []
        if v["kind"] != "through" or (pair and set(pair) != {"TopLayer", "BottomLayer"}):
            warnings.append(f"a {v['kind']} via at {v['at']} needs a drill pair; placed through")
        x, y = frame(v["at"])
        vias.append({"x": x, "y": y, "size": _k(v["size"]), "hole_size": _k(v["drill"]),
                     "net": nets.get(v["net"], v["net"])})
    for i in range(0, len(vias), 1000):
        steps.append({"tool": "pcb_place_vias", "args": {"vias": vias[i:i + 1000],
                                                         "board_path": doc}})

    pours = []
    keepouts = 0
    for z in sorted(board["zones"], key=lambda z: -z["priority"]):
        if z["keepout"] is not None:
            keepouts += 1
            continue
        if len(z["outline"]) < 3:
            continue
        for lname in z["layers"]:
            if "*" in lname:
                targets = [l for l in board["copper_layers"]
                           if fnmatch.fnmatch(l, lname.replace("*", "*"))]
            else:
                targets = [lname]
            for t in targets:
                lay = layer_of(t)
                if lay is None:
                    continue
                name = _clean(f"{z['name'] or z['net'] or 'Pour'}_{lay}_{len(pours) + 1}")
                steps.append({"tool": "pcb_place_polygon", "args": {
                    "points": [frame(p) for p in z["outline"]],
                    "net": nets.get(z["net"], z["net"]), "layer": lay, "name": name,
                    "pour": False}})
                pours.append(name)
    if keepouts:
        warnings.append(f"{keepouts} keep-out zone(s) not imported")
    if dots:
        warnings.append(f"{dots} track(s) of no length left out")
    if unknown_layers:
        warnings.append(f"copper on layers not in the stack, skipped: {sorted(unknown_layers)}")

    steps += rule_steps(board, nets, net_classes)
    if pours:
        steps.append({"tool": "pcb_repour_polygons", "args": {"names": pours,
                                                             "board_path": doc}})
    for tag, n in (board.get("unsupported") or {}).items():
        warnings.append(f"{n} board {tag} item(s) not imported")
    return {"steps": steps, "warnings": warnings, "pours": pours,
            "placements": placements, "frame": [frame.x0, frame.y0]}


def class_members(board: dict[str, Any], net_classes: dict[str, Any]) -> dict[str, list[str]]:
    """Each KiCad net class's nets, from its assignments and patterns."""
    out: dict[str, list[str]] = {}
    for net, cls in (net_classes.get("assignments") or {}).items():
        names = cls if isinstance(cls, list) else [cls]
        for c in names:
            out.setdefault(str(c), []).append(net)
    for pat in net_classes.get("patterns") or []:
        cls, rule = str(pat.get("netclass", "")), str(pat.get("pattern", ""))
        for net in board.get("nets") or []:
            if rule and fnmatch.fnmatchcase(net, rule) and net not in out.get(cls, []):
                out.setdefault(cls, []).append(net)
    return out


def rule_steps(board: dict[str, Any], nets: dict[str, str],
               net_classes: dict[str, Any]) -> list[dict[str, Any]]:
    """Net classes, a clearance and width rule for each, and the board's
    defaults on Altium's own Clearance and Width rules."""
    steps: list[dict[str, Any]] = []
    classes = {c.get("name"): c for c in net_classes.get("classes") or []}
    rules = net_classes.get("rules") or {}
    default = classes.get("Default") or {}
    min_w = rules.get("min_track_width") or default.get("track_width")
    gap = default.get("clearance") or rules.get("min_clearance")
    if gap:
        steps.append({"tool": "pcb_set_rule_properties",
                      "args": {"name": "Clearance", "gap_mm": float(gap)}})
    if default.get("track_width"):
        steps.append({"tool": "pcb_set_rule_properties", "args": {
            "name": "Width", "min_width_mm": float(min_w),
            "favored_width_mm": float(default["track_width"]),
            "max_width_mm": max(float(default["track_width"]) * 10, 2.0)}})
    members = class_members(board, net_classes)
    for name, cls in classes.items():
        if name == "Default":
            continue
        nets_in = sorted({nets.get(n, n) for n in members.get(name, [])})
        if not nets_in:
            continue
        alt = _clean(name)
        steps.append({"tool": "pcb_create_net_class",
                      "args": {"name": alt, "nets": ",".join(nets_in)}})
        if cls.get("clearance"):
            steps.append({"tool": "pcb_create_design_rule", "args": {
                "name": f"Clearance_{alt}", "rule_type": "clearance",
                "value": _k(float(cls["clearance"])), "scope": f"InNetClass('{alt}')",
                "net_scope": "different_nets"}})
        if cls.get("track_width"):
            tw = _k(float(cls["track_width"]))
            mw = _k(float(min_w)) if min_w else tw
            steps.append({"tool": "pcb_create_design_rule", "args": {
                "name": f"Width_{alt}", "rule_type": "width", "value": min(mw, tw),
                "max_value": max(tw * 10, 80.0), "favored_value": int(round(tw)),
                "scope": f"InNetClass('{alt}')"}})
    return steps


def pad_positions(board: dict[str, Any]) -> dict[str, list[list[float]]]:
    """Every pad's centre in Altium's frame, ``REF.PAD`` -> [[x, y], ...]:
    a list, since one number can name several pads (stitching, a thermal
    pad split in four)."""
    frame = Frame(board)
    out: dict[str, list[list[float]]] = {}
    for f in board["footprints"]:
        for p in f["pads"]:
            if f["reference"] and p["number"]:
                out.setdefault(f"{f['reference']}.{p['number']}", []).append(frame(p["at"]))
    return out
