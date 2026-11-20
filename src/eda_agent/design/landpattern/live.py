# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint as it is in a PcbLib, read live, in the land-pattern model.

The package tools draw and check the footprint the engine WOULD build. A
footprint already in a library, from a vendor or drawn by hand years ago,
needs the same: a picture to set beside the datasheet, and a comparison
with another footprint (the vendor's against ours, rev A against rev B).

Which layer is the courtyard is not a name: Altium records it as the
mechanical layer's KIND ("Courtyard Top", "Assembly Top", "3D Body Top"),
which lib_get_mech_layers reads. A layer whose kind is not set is matched
by the caller's courtyard_layer / assembly_layer, and is otherwise left as
"other", never guessed from a number.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

from .model import (Arc, Body3D, Circle, FootprintModel, Line, Pad, Polygon,
                    Text)

#: Layer roles by Altium's mechanical layer kind (MechKindToString).
_ROLE_BY_KIND = {
    "courtyard top": "courtyard", "courtyard bottom": "courtyard",
    "assembly top": "assembly", "assembly bottom": "assembly",
    "component outline top": "assembly",
    "component outline bottom": "assembly",
    "3d body top": "body3d", "3d body bottom": "body3d",
    "designator top": "designator", "designator bottom": "designator",
    "component center top": "center", "component center bottom": "center",
}
_SILK = {"topoverlay": "silk", "bottomoverlay": "silk"}

_SHAPES = {"roundrectangle": "roundrect", "rectangle": "rect",
           "rect": "rect", "round": "circle", "circle": "circle",
           "octagonal": "rect", "oval": "oval", "obround": "oval"}


def layer_roles(mech_layers: Optional[list] = None, *,
                courtyard_layer: str = "",
                assembly_layer: str = "") -> dict[str, str]:
    """{layer name, lower case: role}. Both the MechanicalN name and the
    layer's own name are keys, since primitives report either."""
    roles: dict[str, str] = dict(_SILK)
    for entry in mech_layers or []:
        role = _ROLE_BY_KIND.get(str(entry.get("kind", "")).strip().lower())
        if role:
            for key in (entry.get("layer"), entry.get("name")):
                if key:
                    roles[str(key).lower()] = role
    if courtyard_layer:
        roles[courtyard_layer.lower()] = "courtyard"
    if assembly_layer:
        roles[assembly_layer.lower()] = "assembly"
    return roles


def _role(layer: str, roles: dict[str, str]) -> str:
    return roles.get(str(layer or "").lower(), "other")


def _pad(p: dict) -> Pad:
    hole = float(p.get("hole_mm") or 0.0)
    shape = _SHAPES.get(str(p.get("shape", "")).lower(), "rect")
    w, h = float(p.get("w_mm") or 0.0), float(p.get("h_mm") or 0.0)
    if shape == "circle" and abs(w - h) > 1e-6:
        shape = "oval"
    return Pad(
        number=str(p.get("name", "")),
        x=float(p.get("copper_x_mm", p.get("x_mm", 0.0)) or 0.0),
        y=float(p.get("copper_y_mm", p.get("y_mm", 0.0)) or 0.0),
        width=w, height=h, shape=shape,
        # Altium's corner percent is of HALF the smaller side.
        corner_ratio=float(p.get("corner_pct") or 0.0) / 200.0,
        rotation=float(p.get("rotation") or 0.0),
        kind="tht" if hole > 0 else "smd", hole=hole,
        hole_length=float(p.get("hole_w_mm") or 0.0),
        hole_rotation=float(p.get("hole_rotation") or 0.0),
        plated=bool(p.get("plated", True)),
        paste=bool(p.get("paste_top_enabled", hole <= 0)),
        paste_expansion=float(p.get("paste_expansion_mm") or 0.0),
        mask_expansion=float(p.get("mask_expansion_mm") or 0.0))


def _bbox(graphics) -> Optional[tuple[float, float, float, float]]:
    xs: list[float] = []
    ys: list[float] = []
    for g in graphics:
        if isinstance(g, Line):
            xs += [g.x1, g.x2]
            ys += [g.y1, g.y2]
        elif isinstance(g, (Arc, Circle)):
            xs += [g.x - g.r, g.x + g.r]
            ys += [g.y - g.r, g.y + g.r]
        elif isinstance(g, Polygon):
            xs += [x for x, _ in g.points]
            ys += [y for _, y in g.points]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def model_from_live(pads_reply: dict, prims_reply: dict,
                    mech_layers: Optional[list] = None, *,
                    courtyard_layer: str = "",
                    assembly_layer: str = "") -> FootprintModel:
    """The footprint lib_get_pad_geometry and lib_get_footprint_primitives
    read, as a FootprintModel; graphics carry their layer ROLE (silk,
    assembly, courtyard, body3d, designator, center, other)."""
    roles = layer_roles(mech_layers, courtyard_layer=courtyard_layer,
                        assembly_layer=assembly_layer)
    pads = [_pad(p) for p in (pads_reply or {}).get("pads") or []]
    graphics: list = []
    for t in (prims_reply or {}).get("tracks") or []:
        graphics.append(Line(layer=_role(t.get("layer"), roles),
                             x1=float(t["x1"]), y1=float(t["y1"]),
                             x2=float(t["x2"]), y2=float(t["y2"]),
                             width=float(t.get("width") or 0.0)))
    for a in (prims_reply or {}).get("arcs") or []:
        start, end = float(a.get("start", 0.0)), float(a.get("end", 360.0))
        sweep = (end - start) % 360.0 or 360.0
        layer = _role(a.get("layer"), roles)
        if sweep >= 359.999:
            # A whole circle (a pin 1 dot, a round courtyard): an SVG arc
            # whose ends meet draws nothing.
            graphics.append(Circle(layer=layer, x=float(a["xc"]),
                                   y=float(a["yc"]), r=float(a["r"]),
                                   width=float(a.get("width") or 0.0)))
            continue
        graphics.append(Arc(layer=layer, x=float(a["xc"]), y=float(a["yc"]),
                            r=float(a["r"]), start=start, sweep=sweep,
                            width=float(a.get("width") or 0.0)))
    for key in ("regions", "fills"):
        for f in (prims_reply or {}).get(key) or []:
            x1, y1, x2, y2 = (float(f[k]) for k in ("x1", "y1", "x2", "y2"))
            graphics.append(Polygon(
                layer=_role(f.get("layer"), roles),
                points=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                width=0.0, filled=True))
    for t in (prims_reply or {}).get("texts") or []:
        graphics.append(Text(layer=_role(t.get("layer"), roles),
                             text=str(t.get("text", "")),
                             x=float(t.get("x") or 0.0),
                             y=float(t.get("y") or 0.0),
                             height=float(t.get("size") or 1.0),
                             thickness=0.1))
    court = _bbox([g for g in graphics if g.layer == "courtyard"])
    courtyard = Polygon(layer="courtyard", width=0.05, points=(
        [(court[0], court[1]), (court[2], court[1]), (court[2], court[3]),
         (court[0], court[3])] if court else []))
    copper = [p.bbox() for p in pads]
    if copper:
        bx0 = min(b[0] for b in copper)
        by0 = min(b[1] for b in copper)
        bx1 = max(b[2] for b in copper)
        by1 = max(b[3] for b in copper)
    else:
        bx0 = by0 = bx1 = by1 = 0.0
    # The footprint's Height field is often left at 0 while a 3D body says
    # how tall the part is; the taller of the two is the part's height.
    height = float((prims_reply or {}).get("height_mm") or 0.0)
    standoff = 0.0
    for body in (prims_reply or {}).get("body_models") or []:
        height = max(height, float(body.get("overall_height_mm") or 0.0))
        standoff = max(standoff, float(body.get("standoff_mm") or 0.0))
    one = next((p for p in pads if p.number == "1"), pads[0] if pads else None)
    name = str((prims_reply or {}).get("name") or (pads_reply or {}).get("name")
               or "footprint")
    return FootprintModel(
        name=name, description=str((pads_reply or {}).get("description") or ""),
        family="library", density="as drawn", pads=pads, graphics=graphics,
        courtyard=courtyard,
        ref=Text(layer="assembly", text="", x=0.0, y=0.0, height=0.5,
                 thickness=0.1),
        body=Body3D(bx0, by0, bx1, by1, standoff, height),
        pin1=(one.x, one.y) if one else (0.0, 0.0),
        origin="footprint origin",
        source={"library_path": (prims_reply or {}).get("library_path"),
                "read": "live"})


def role_summary(model: FootprintModel) -> dict[str, Any]:
    """Per role: how many primitives, and their extent in mm."""
    out: dict[str, Any] = {}
    for role in sorted({g.layer for g in model.graphics}):
        items = [g for g in model.graphics if g.layer == role]
        box = _bbox(items)
        out[role] = {"count": len(items),
                     "bbox": [round(v, 4) for v in box] if box else None}
    return out


def silk_over_copper(model: FootprintModel, clearance: float = 0.0) -> list[dict]:
    """Silkscreen lines that touch a pad: ink on a land the solder needs.

    A line counts when any point of it, widened by its own half width and
    ``clearance`` (the mask opening the fab keeps silk out of), falls on a
    pad's copper box. Each hit names the pad and the line's ends in mm.
    """
    hits: list[dict] = []
    boxes = [(p.number, p.bbox()) for p in model.pads]
    for g in model.graphics:
        if g.layer != "silk" or not isinstance(g, Line):
            continue
        grow = g.width / 2.0 + max(0.0, clearance)
        length = math.hypot(g.x2 - g.x1, g.y2 - g.y1)
        steps = max(1, int(length / 0.02))
        touched = set()
        for i in range(steps + 1):
            t = i / steps
            x = g.x1 + (g.x2 - g.x1) * t
            y = g.y1 + (g.y2 - g.y1) * t
            for number, (x0, y0, x1, y1) in boxes:
                if x0 - grow < x < x1 + grow and y0 - grow < y < y1 + grow:
                    touched.add(number)
        for number in sorted(touched, key=_natural):
            hits.append({"pad": number, "line": [round(g.x1, 4), round(g.y1, 4),
                                                 round(g.x2, 4), round(g.y2, 4)],
                         "width": g.width})
    return hits


def _is_slot(p: Pad) -> bool:
    return p.kind != "smd" and p.hole_length > p.hole > 0


def _slot_angle(p: Pad) -> float:
    """The direction a slot runs in the footprint, 0 to 180 degrees."""
    return (p.rotation + p.hole_rotation) % 180.0


def compare_live(a: FootprintModel, b: FootprintModel, *,
                 tol: float = 0.001) -> dict[str, Any]:
    """Pad by pad and role by role, what differs between two footprints.

    Pads are matched by number. A position, size, shape, rotation, hole or
    paste difference over ``tol`` mm (or any discrete difference) is
    listed, and so is ``slot_angle`` when two slots run different ways; a
    pad only one side has is ``only_in_a`` / ``only_in_b``.
    """
    pa = {p.number: p for p in a.pads}
    pb = {p.number: p for p in b.pads}
    pads: list[dict] = []
    for num in sorted(set(pa) & set(pb), key=_natural):
        x, y = pa[num], pb[num]
        diff: dict[str, Any] = {}
        for field in ("x", "y", "width", "height", "hole", "hole_length",
                      "paste_expansion", "mask_expansion"):
            va, vb = getattr(x, field), getattr(y, field)
            if abs(va - vb) > tol:
                diff[field] = [round(va, 4), round(vb, 4)]
        if abs(((x.rotation - y.rotation) + 180) % 360 - 180) > 0.01:
            diff["rotation"] = [x.rotation, y.rotation]
        # A slot runs at the pad rotation plus its own hole rotation, and
        # reads the same turned half a turn. Two slots of one size and one
        # pad rotation differ only here when one runs across its pad.
        if _is_slot(x) and _is_slot(y):
            sa, sb = _slot_angle(x), _slot_angle(y)
            if abs(((sa - sb) + 90) % 180 - 90) > 0.01:
                diff["slot_angle"] = [round(sa, 4), round(sb, 4)]
        for field in ("shape", "kind", "plated", "paste"):
            if getattr(x, field) != getattr(y, field):
                diff[field] = [getattr(x, field), getattr(y, field)]
        if x.shape == y.shape == "roundrect" and \
                abs(x.corner_radius - y.corner_radius) > tol:
            diff["corner_radius"] = [round(x.corner_radius, 4),
                                     round(y.corner_radius, 4)]
        if diff:
            pads.append({"pad": num, **diff})
    ra, rb = role_summary(a), role_summary(b)
    roles: dict[str, Any] = {}
    for role in sorted(set(ra) | set(rb)):
        sa, sb = ra.get(role), rb.get(role)
        if sa is None or sb is None:
            roles[role] = {"a": sa, "b": sb}
            continue
        boxes = sa["bbox"], sb["bbox"]
        moved = (boxes[0] and boxes[1] and
                 max(abs(u - v) for u, v in zip(*boxes)) > tol)
        if sa["count"] != sb["count"] or moved:
            roles[role] = {"a": sa, "b": sb}
    height = (a.body.z1, b.body.z1)
    same = not pads and not roles and set(pa) == set(pb) \
        and abs(height[0] - height[1]) <= tol
    return {
        "same": same,
        "pad_count": [len(pa), len(pb)],
        "only_in_a": sorted(set(pa) - set(pb), key=_natural),
        "only_in_b": sorted(set(pb) - set(pa), key=_natural),
        "pads_differing": pads,
        "layers_differing": roles,
        "height_mm": [round(height[0], 4), round(height[1], 4)],
        "tolerance_mm": tol,
    }


def _natural(name: str):
    """Pad numbers in human order: 2 before 10, A2 before A10."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t)
            for t in re.split(r"(\d+)", str(name)) if t]


__all__ = ["layer_roles", "model_from_live", "role_summary", "compare_live",
           "silk_over_copper"]
