# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Each symbol and footprint built in Altium against KiCad's original.

The expected side is read from KiCad's own text, not from the plan that
built the library, so a conversion that is wrong in the plan shows here.
The Altium side is what the library tools read back. Each function
returns the differences as sentences; an empty list means the same.

Symbols: every pin's wire end, length, part and whether it is hidden, and
every part's rectangles, line segments, arcs and texts. Footprints: every
pad's number, centre, size, angle, shape, drill and plating, and that
each KiCad drawing layer the importer carries has drawing in Altium.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable, Optional

from ...core.kicad_sexpr_edit import all_of, first
from ...libimport.kicad.sexpr import loads
from ...units import MILS_PER_MM

#: Mils a symbol coordinate may differ by before it counts.
SYMBOL_TOLERANCE = 1.5
#: Degrees an arc's ends may differ by.
ANGLE_TOLERANCE = 1.0
#: Millimetres a pad dimension may differ by.
PAD_TOLERANCE_MM = 0.005

_DIRS = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}

#: KiCad footprint drawing layers the importer carries, and the Altium
#: layer each lands on (the library emitter's map).
FOOTPRINT_LAYERS = {
    "F.SilkS": "TopOverlay", "B.SilkS": "BottomOverlay",
    "F.Fab": "Mechanical13", "B.Fab": "Mechanical14",
    "F.Cu": "TopLayer", "B.Cu": "BottomLayer", "Cmts.User": "Mechanical1",
}

#: Where a pad with no copper (a paste or mask opening) lands: a region.
APERTURE_LAYERS = {"F.Paste": "TopPaste", "B.Paste": "BottomPaste",
                   "F.Mask": "TopSolder", "B.Mask": "BottomSolder"}


def _mil(v: float) -> float:
    return float(v) * MILS_PER_MM


def _num(node: Optional[list], i: int = 1, default: float = 0.0) -> float:
    try:
        return float(node[i]) if node and len(node) > i else default
    except (TypeError, ValueError):
        return default


def _hidden(node: list) -> bool:
    if "hide" in node[1:]:
        return True
    h = first(node, "hide")
    return bool(h) and (len(h) < 2 or str(h[1]) == "yes")


def _unit_style(name: str) -> tuple[int, int]:
    m = re.search(r"_(\d+)_(\d+)$", name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# --- symbols ---------------------------------------------------------------------


def expected_symbol(text: str) -> dict[str, Any]:
    """What a KiCad symbol says, in Altium's frame (mils, Y up; a KiCad
    symbol is already Y up): ``parts`` (how many), ``pins`` {number: {x,
    y, length, part, hidden}} with x, y the wire end, and per part
    ``rects``, ``segments``, ``arcs`` and ``texts``."""
    node = loads(text)
    if node and node[0] == "kicad_symbol_lib":
        node = first(node, "symbol")
    subs = [s for s in all_of(node, "symbol")]
    units = sorted({_unit_style(str(s[1]))[0] for s in subs} - {0}) or [1]
    styles = sorted({_unit_style(str(s[1]))[1] for s in subs} - {0}) or [1]
    style = styles[0]
    parts = len(units)
    out: dict[str, Any] = {"parts": parts, "pins": {},
                           "art": {u: {"rects": [], "segments": [], "arcs": [], "texts": []}
                                   for u in range(1, parts + 1)}}
    bodies = [(0, node)] + [(_unit_style(str(s[1]))[0], s) for s in subs
                            if _unit_style(str(s[1]))[1] in (0, style)]
    for unit, body in bodies:
        owners = [units.index(unit) + 1] if unit in units else list(range(1, parts + 1))
        for pin in all_of(body, "pin"):
            at = first(pin, "at") or []
            number = first(pin, "number")
            num = str(number[1]) if number and len(number) > 1 else ""
            out["pins"][num] = {"x": _mil(_num(at, 1)), "y": _mil(_num(at, 2)),
                                "length": _mil(_num(first(pin, "length"))),
                                "part": owners[0] if len(owners) == 1 else 0,
                                "hidden": _hidden(pin)}
        art = _symbol_art(body)
        for u in owners:
            for key, items in art.items():
                out["art"][u][key].extend(items)
    # A hidden pin stacked on another (same part, same wire end) is shown in
    # Altium, where a hidden pin joins no wire.
    ends = Counter((p["part"], round(p["x"]), round(p["y"])) for p in out["pins"].values())
    for p in out["pins"].values():
        if ends[(p["part"], round(p["x"]), round(p["y"]))] > 1:
            p["hidden"] = False
    return out


def _symbol_art(body: list) -> dict[str, list]:
    rects, segments, arcs, texts = [], [], [], []
    for r in all_of(body, "rectangle"):
        s, e = first(r, "start") or [], first(r, "end") or []
        xs = sorted((_mil(_num(s, 1)), _mil(_num(e, 1))))
        ys = sorted((_mil(_num(s, 2)), _mil(_num(e, 2))))
        rects.append((xs[0], ys[0], xs[1], ys[1]))
    for tag in ("polyline", "bezier"):
        for p in all_of(body, tag):
            pts = [(_mil(_num(xy, 1)), _mil(_num(xy, 2)))
                   for xy in all_of(first(p, "pts") or [], "xy")]
            for a, b in zip(pts, pts[1:]):
                if math.dist(a, b) > SYMBOL_TOLERANCE:
                    segments.append((a, b))
    for c in all_of(body, "circle"):
        ctr = first(c, "center") or []
        arcs.append((_mil(_num(ctr, 1)), _mil(_num(ctr, 2)), _mil(_num(first(c, "radius"))),
                     0.0, 360.0))
    for a in all_of(body, "arc"):
        pts = [first(a, k) or [] for k in ("start", "mid", "end")]
        p = [(_mil(_num(q, 1)), _mil(_num(q, 2))) for q in pts]
        arc = _arc_through(*p)
        if arc:
            arcs.append(arc)
    for t in all_of(body, "text"):
        if len(t) > 1 and str(t[1]).strip():
            texts.append(str(t[1]))
    return {"rects": rects, "segments": segments, "arcs": arcs, "texts": texts}


def _arc_through(a, m, b) -> Optional[tuple]:
    """Centre, radius and counter-clockwise start and end angles of the
    arc from a through m to b."""
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = math.dist((ux, uy), a)

    def ang(p):
        return math.degrees(math.atan2(p[1] - uy, p[0] - ux)) % 360.0

    s, mid, e = ang(a), ang(m), ang(b)
    # Counter-clockwise from s to e must pass the middle point; if not,
    # the arc runs the other way and its ends swap.
    if (mid - s) % 360.0 > (e - s) % 360.0:
        s, e = e, s
    return (ux, uy, r, s, e)


def _close(a: float, b: float, tol: float) -> bool:
    return abs(float(a) - float(b)) <= tol


def _angle_close(a: float, b: float) -> bool:
    d = abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)
    return d <= ANGLE_TOLERANCE


def _match(want: list, got: list, same) -> tuple[list, list]:
    """Pair items one to one; what is left on each side."""
    left = list(got)
    missing = []
    for w in want:
        for i, g in enumerate(left):
            if same(w, g):
                del left[i]
                break
        else:
            missing.append(w)
    return missing, left


def _same_rect(w, g) -> bool:
    return all(_close(a, b, SYMBOL_TOLERANCE) for a, b in zip(w, g))


def _same_segment(w, g) -> bool:
    (a, b), (c, d) = w, g
    return ((math.dist(a, c) <= SYMBOL_TOLERANCE and math.dist(b, d) <= SYMBOL_TOLERANCE)
            or (math.dist(a, d) <= SYMBOL_TOLERANCE and math.dist(b, c) <= SYMBOL_TOLERANCE))


def _same_arc(w, g) -> bool:
    if not (_close(w[0], g[0], SYMBOL_TOLERANCE) and _close(w[1], g[1], SYMBOL_TOLERANCE)
            and _close(w[2], g[2], SYMBOL_TOLERANCE)):
        return False
    full_w = abs((w[4] - w[3]) % 360.0) < ANGLE_TOLERANCE and abs(w[4] - w[3]) > 1
    full_g = abs((g[4] - g[3]) % 360.0) < ANGLE_TOLERANCE and abs(g[4] - g[3]) > 1
    if full_w or full_g:
        return full_w == full_g
    return _angle_close(w[3], g[3]) and _angle_close(w[4], g[4])


def _fmt(v) -> str:
    if isinstance(v, (tuple, list)):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    if isinstance(v, float):
        return format(round(v, 4) + 0.0, "g")
    return str(v)


def compare_symbol(want: dict[str, Any], pins: list[dict], art: dict[int, dict]) -> list[str]:
    """Differences between KiCad's symbol and Altium's.

    ``pins`` as lib_get_component_details reads them (Location, length,
    orientation, hidden); ``art`` per part {rects, segments, arcs, texts}
    as read from the symbol's primitives.
    """
    out: list[str] = []
    got: dict[str, dict] = {}
    for p in pins:
        dx, dy = _DIRS[int(p.get("orientation", 0)) % 4]
        ln = float(p.get("length", 0))
        got[str(p.get("designator", ""))] = {
            "x": float(p.get("x", 0)) + dx * ln, "y": float(p.get("y", 0)) + dy * ln,
            "length": ln, "hidden": bool(p.get("hidden"))}
    for num, w in want["pins"].items():
        g = got.pop(num, None)
        if g is None:
            out.append(f"pin {num} is missing")
            continue
        if math.dist((w["x"], w["y"]), (g["x"], g["y"])) > SYMBOL_TOLERANCE:
            out.append(f"pin {num} connects at {_fmt((g['x'], g['y']))}, "
                       f"KiCad at {_fmt((w['x'], w['y']))}")
        if not _close(w["length"], g["length"], SYMBOL_TOLERANCE):
            out.append(f"pin {num} is {_fmt(g['length'])} mil long, KiCad {_fmt(w['length'])}")
        if w["hidden"] != g["hidden"]:
            out.append(f"pin {num} is {'hidden' if g['hidden'] else 'shown'}, "
                       f"KiCad {'hidden' if w['hidden'] else 'shown'}")
    for num in sorted(got):
        out.append(f"pin {num} is not in KiCad's symbol")
    for part, w in want["art"].items():
        g = art.get(part, {})
        where = f"part {part}: " if want["parts"] > 1 else ""
        for key, same, noun in (("rects", _same_rect, "rectangle"),
                                ("segments", _same_segment, "line"),
                                ("arcs", _same_arc, "arc")):
            missing, extra = _match(w[key], g.get(key, []), same)
            out += [f"{where}{noun} {_fmt(m)} is missing" for m in missing]
            out += [f"{where}{noun} {_fmt(e)} is not in KiCad's symbol" for e in extra]
        lost = Counter(w["texts"]) - Counter(g.get("texts", []))
        out += [f"{where}text {t!r} is missing" for t in lost.elements()]
    return out


def symbol_art_from_rows(rects: Iterable[dict], lines: Iterable[dict],
                         arcs: Iterable[dict], labels: Iterable[dict]) -> dict[str, list]:
    """One part's primitives from obj_query rows into compare_symbol's form."""
    def f(row, key):
        return float(row.get(key, 0) or 0)

    out: dict[str, list] = {"rects": [], "segments": [], "arcs": [], "texts": []}
    for r in rects:
        xs = sorted((f(r, "Location.X"), f(r, "Corner.X")))
        ys = sorted((f(r, "Location.Y"), f(r, "Corner.Y")))
        out["rects"].append((xs[0], ys[0], xs[1], ys[1]))
    for ln in lines:
        a = (f(ln, "Location.X"), f(ln, "Location.Y"))
        b = (f(ln, "Corner.X"), f(ln, "Corner.Y"))
        if math.dist(a, b) > SYMBOL_TOLERANCE:
            out["segments"].append((a, b))
    for a in arcs:
        out["arcs"].append((f(a, "Location.X"), f(a, "Location.Y"), f(a, "Radius"),
                            f(a, "StartAngle"), f(a, "EndAngle")))
    for t in labels:
        if str(t.get("Text", "")).strip():
            out["texts"].append(str(t["Text"]))
    return out


# --- footprints ------------------------------------------------------------------


def expected_footprint(text: str) -> dict[str, Any]:
    """What a KiCad footprint in its library frame says, in Altium's frame
    (mm, Y up): ``pads`` [{name, x, y, w, h, angle, round, drill (sorted
    pair or None), plated, smd}] and ``layers`` (KiCad drawing layer ->
    item count)."""
    node = loads(text)
    pads = []
    openings = []
    for pad in all_of(node, "pad"):
        at = first(pad, "at") or []
        size = first(pad, "size") or []
        drill = first(pad, "drill")
        kind = str(pad[2]) if len(pad) > 2 else ""
        shape = str(pad[3]) if len(pad) > 3 else ""
        layers = [str(v) for v in (first(pad, "layers") or [])[1:]]
        if kind == "smd" and not any(lay.endswith(".Cu") for lay in layers):
            # A paste or mask opening with no copper.
            w, h = _box(_num(size, 1), _num(size, 2), _num(at, 3))
            for lay in layers:
                if lay in APERTURE_LAYERS:
                    openings.append({"x": _num(at, 1), "y": -_num(at, 2), "w": w, "h": h,
                                     "layer": APERTURE_LAYERS[lay]})
            continue
        hole = None
        if drill and kind != "smd":
            nums = [float(v) for v in drill[1:] if _is_num(v)]
            if nums:
                hole = tuple(sorted((nums[0], nums[1] if len(nums) > 1 else nums[0])))
        if shape == "custom":
            # Its copper beyond the anchor, and the openings over it, are
            # regions in the exact outline.
            from ...libimport.kicad.reader import _custom_pad_outlines
            copper = ["TopLayer", "BottomLayer"] if kind != "smd" else \
                ["BottomLayer" if "B.Cu" in layers else "TopLayer"]
            targets = [(c, "copper") for c in copper] + [
                (APERTURE_LAYERS[lay], "opening") for lay in layers if lay in APERTURE_LAYERS]
            for outline in _custom_pad_outlines(pad, _num(at, 1), _num(at, 2), _num(at, 3)):
                xs = [p[0] for p in outline]
                ys = [-p[1] for p in outline]
                for lay, what in targets:
                    openings.append({"x": (min(xs) + max(xs)) / 2, "y": (min(ys) + max(ys)) / 2,
                                     "w": max(xs) - min(xs), "h": max(ys) - min(ys),
                                     "layer": lay, "what": f"pad {pad[1]} {what}"})
        pads.append({"name": str(pad[1]) if len(pad) > 1 else "",
                     "x": _num(at, 1), "y": -_num(at, 2), "angle": _num(at, 3),
                     "w": _num(size, 1), "h": _num(size, 2),
                     "round": shape in ("circle", "oval"), "custom": shape == "custom",
                     "drill": hole, "plated": kind != "np_thru_hole", "smd": kind == "smd"})
    layers: Counter = Counter()
    for tag in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_text"):
        for item in all_of(node, tag):
            lay = first(item, "layer")
            if lay and len(lay) > 1:
                layers[str(lay[1])] += 1
    return {"pads": pads, "openings": openings, "layers": dict(layers)}


def _box(width: float, height: float, angle: float) -> tuple[float, float]:
    """The width and height a rectangle covers once turned by angle."""
    a = math.radians(angle)
    return (abs(width * math.cos(a)) + abs(height * math.sin(a)),
            abs(width * math.sin(a)) + abs(height * math.cos(a)))


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _norm_layer(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def compare_footprint(want: dict[str, Any], pads: list[dict],
                      prims: dict[str, Any]) -> list[str]:
    """Differences between KiCad's footprint and Altium's.

    ``pads`` as lib_get_pad_geometry reads them (mm); ``prims`` as
    lib_get_footprint_primitives reads them.
    """
    out: list[str] = []
    left = list(pads)
    for w in want["pads"]:
        cands = [g for g in left if str(g.get("name", "")) == w["name"]]
        if not cands:
            out.append(f"pad {w['name'] or '(no number)'} at {_fmt((w['x'], w['y']))} mm is missing")
            continue
        g = min(cands, key=lambda g: math.dist((w["x"], w["y"]),
                                               (float(g.get("x_mm", 0)), float(g.get("y_mm", 0)))))
        left.remove(g)
        label = f"pad {w['name'] or '(no number)'}"
        gx, gy = float(g.get("x_mm", 0)), float(g.get("y_mm", 0))
        if math.dist((w["x"], w["y"]), (gx, gy)) > PAD_TOLERANCE_MM:
            out.append(f"{label} is at {_fmt((gx, gy))} mm, KiCad {_fmt((w['x'], w['y']))}")
        if not w["custom"]:
            out += _pad_shape(label, w, g)
        hole = None
        if float(g.get("hole_mm", 0) or 0) > 0:
            hw = float(g.get("hole_w_mm", 0) or 0)
            hm = float(g.get("hole_mm", 0))
            hole = tuple(sorted((hm, hw if hw > 0 else hm)))
        if (w["drill"] is None) != (hole is None) or (
                hole and not all(_close(a, b, PAD_TOLERANCE_MM) for a, b in zip(hole, w["drill"]))):
            out.append(f"{label} drill is {_fmt(hole) if hole else 'none'} mm, "
                       f"KiCad {_fmt(w['drill']) if w['drill'] else 'none'}")
        if w["drill"] is not None and bool(g.get("plated", True)) != w["plated"]:
            out.append(f"{label} is {'plated' if g.get('plated', True) else 'unplated'}, "
                       f"KiCad {'plated' if w['plated'] else 'unplated'}")
    for g in left:
        out.append(f"pad {g.get('name') or '(no number)'} is not in KiCad's footprint")

    regions = list(prims.get("regions") or [])
    for o in want.get("openings", []):
        def fits(r):
            x1, x2 = sorted((float(r.get("x1", 0)), float(r.get("x2", 0))))
            y1, y2 = sorted((float(r.get("y1", 0)), float(r.get("y2", 0))))
            tol = PAD_TOLERANCE_MM * 4
            return (_norm_layer(r.get("layer", "")) == _norm_layer(o["layer"])
                    and _close((x1 + x2) / 2, o["x"], tol) and _close((y1 + y2) / 2, o["y"], tol)
                    and _close(x2 - x1, o["w"], tol) and _close(y2 - y1, o["h"], tol))
        hit = next((r for r in regions if fits(r)), None)
        if hit is None:
            out.append(f"{o['layer']} {o.get('what', 'opening')} {_fmt((o['w'], o['h']))} mm at "
                       f"{_fmt((o['x'], o['y']))} is missing")
        else:
            regions.remove(hit)

    drawn: Counter = Counter()
    for key in ("tracks", "arcs", "regions", "fills", "texts"):
        for p in prims.get(key) or []:
            drawn[_norm_layer(p.get("layer", ""))] += 1
            if p.get("layer_name"):
                drawn[_norm_layer(p["layer_name"])] += 1
    for k_layer, n in sorted(want["layers"].items()):
        a_layer = FOOTPRINT_LAYERS.get(k_layer)
        if a_layer is None or k_layer in ("F.Cu", "B.Cu"):
            continue
        if not drawn.get(_norm_layer(a_layer)):
            out.append(f"{k_layer} has {n} item(s) in KiCad and {a_layer} has none")
    return out


def _pad_shape(label: str, w: dict, g: dict) -> list[str]:
    gw, gh = float(g.get("w_mm", 0)), float(g.get("h_mm", 0))
    rot = float(g.get("rotation", 0) or 0)
    shape = str(g.get("shape", ""))
    out = []

    def box(width, height, angle):
        a = math.radians(angle)
        return (abs(width * math.cos(a)) + abs(height * math.sin(a)),
                abs(width * math.sin(a)) + abs(height * math.cos(a)))

    # Compared as the box each pad covers on the board, which does not
    # depend on whether a size is stored turned or a turn stored.
    wb, gb = box(w["w"], w["h"], w["angle"]), box(gw, gh, rot)
    if not (_close(wb[0], gb[0], PAD_TOLERANCE_MM * 4) and _close(wb[1], gb[1], PAD_TOLERANCE_MM * 4)):
        out.append(f"{label} covers {_fmt(gb)} mm, KiCad {_fmt(wb)}")
    is_round = shape == "round"
    if w["round"] != is_round and not (w["round"] and shape == "roundrectangle"
                                       and int(g.get("corner_pct", 0) or 0) >= 100):
        out.append(f"{label} is {shape}, KiCad {'round or oval' if w['round'] else 'rectangular'}")
    return out
