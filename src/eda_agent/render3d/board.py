# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A bare board as a scene: a slab with a picture of each side.

Each side of the board is one texture: what a fabricated board shows
there, texel by texel (solder mask over copper or over laminate, exposed
copper in its finish, silkscreen, drilled holes cut out). Its parameter
map gives each texel its roughness, metal and mask gloss, and the height
of its surface: copper standing under the mask, the ink of the silk,
the mask sagging into tented vias. The slab's edges, the hole walls and the
solder joints of fitted parts are real geometry.

Everything comes from what Altium reports: the geometry read
(generic.get_pcb_geometry), the stack (pcb.get_layer_stackup) and the
copper each pour poured (pcb.pour_copper). Units are mils in, mm out,
with the board's bounding box centred on the origin and its bottom face
at z = 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..units import MM_PER_MIL


#: Solder mask colours, sRGB, as fabs sell them.
MASKS = {
    "green": (6, 80, 44), "black": (12, 12, 14), "blue": (8, 32, 92),
    "red": (118, 12, 14), "white": (222, 222, 218), "purple": (50, 18, 70),
    "yellow": (196, 156, 10), "matte_black": (16, 16, 18), "matte_green": (18, 70, 34),
}
#: Exposed copper finishes: sRGB colour, roughness, metallic.
FINISHES = {
    "enig": ((240, 196, 112), 0.22, 1.0),
    "hasl": ((210, 212, 214), 0.30, 1.0),
    "osp": ((222, 140, 96), 0.25, 1.0),
    "bare": ((222, 140, 96), 0.30, 1.0),
}
#: How much of the copper under it a mask lets through: the traces of a
#: green board read lighter and yellower than the bare laminate around them.
MASK_TRANSLUCENCY = {"green": 0.32, "blue": 0.3, "red": 0.3, "purple": 0.3, "yellow": 0.2,
                     "white": 0.08, "black": 0.04, "matte_black": 0.03, "matte_green": 0.35}
SILKS = {"white": (238, 238, 232), "black": (20, 20, 20), "yellow": (230, 200, 40)}
#: Reflowed solder: sRGB, roughness.
SOLDER = ((222, 224, 228), 0.1)
#: How far up the edge its corners are rounded towards the faces, mm.
EDGE_ROUND_MM = 0.12
#: Relief, mm: the full scale of the height map and what stands how high.
RELIEF_MM = 0.08
COPPER_MM, MASK_MM, INK_MM = 0.035, 0.02, 0.012
LAMINATE = (196, 188, 150)
EDGE = (178, 168, 132)


@dataclass
class Look:
    mask: str = "green"
    finish: str = "enig"
    silk: str = "white"
    #: Mask opening past a pad's copper, mils, when the pad does not say.
    mask_expansion_mils: float = 2.5
    #: Vias covered by mask (tented), as most boards are made.
    tent_vias: bool = True

    def problem(self) -> Optional[str]:
        if self.mask not in MASKS:
            return f"mask is one of {', '.join(MASKS)}"
        if self.finish not in FINISHES:
            return f"finish is one of {', '.join(FINISHES)}"
        if self.silk not in SILKS:
            return f"silk is one of {', '.join(SILKS)}"
        return None


@dataclass
class Mesh:
    """Triangles with per-vertex normals and uvs, and a material each."""
    pos: list = field(default_factory=list)
    nrm: list = field(default_factory=list)
    uv: list = field(default_factory=list)
    tri: list = field(default_factory=list)
    mat: list = field(default_factory=list)

    def vertex(self, p, n, uv=(0.0, 0.0)) -> int:
        self.pos.append(p)
        self.nrm.append(n)
        self.uv.append(uv)
        return len(self.pos) - 1

    def face(self, a: int, b: int, c: int, material: int) -> None:
        self.tri.append((a, b, c))
        self.mat.append(material)

    def arrays(self) -> dict[str, np.ndarray]:
        return {
            "positions": np.asarray(self.pos, dtype="<f4").reshape(-1, 3),
            "normals": np.asarray(self.nrm, dtype="<f4").reshape(-1, 3),
            "uvs": np.asarray(self.uv, dtype="<f4").reshape(-1, 2),
            "indices": np.asarray(self.tri, dtype="<u4").reshape(-1, 3),
            "material_ids": np.asarray(self.mat, dtype="<u4"),
        }


def board_thickness_mm(stackup: dict[str, Any]) -> float:
    """Copper plus dielectric of every layer; 1.6 mm when unreadable."""
    total = 0.0
    for layer in (stackup or {}).get("layers") or []:
        try:
            total += float(layer.get("copper_thickness_mils") or 0)
            total += float(layer.get("dielectric_height_mils") or 0)
        except (TypeError, ValueError):
            continue
    mm = total * MM_PER_MIL
    return mm if 0.2 <= mm <= 10 else 1.6


def _arc_points(cx, cy, r, a1, a2, step_deg=4.0):
    """Points along a counter-clockwise arc from a1 to a2, degrees."""
    sweep = (a2 - a1) % 360.0 or 360.0
    n = max(2, int(math.ceil(sweep / step_deg)) + 1)
    return [(cx + r * math.cos(math.radians(a1 + sweep * i / (n - 1))),
             cy + r * math.sin(math.radians(a1 + sweep * i / (n - 1)))) for i in range(n)]


def outline_points(outline: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """The board outline as a closed polyline in mils, arcs walked."""
    pts: list[tuple[float, float]] = []
    for v in outline or []:
        x, y = float(v.get("x", 0)), float(v.get("y", 0))
        if v.get("kind") == "arc" and v.get("radius"):
            cx, cy, r = float(v["cx"]), float(v["cy"]), float(v["radius"])
            a1, a2 = float(v.get("angle1", 0)), float(v.get("angle2", 0))
            arc = _arc_points(cx, cy, r, a1, a2, step_deg=2.0)
            # The vertex is where the arc starts; walk it from that end.
            if math.dist(arc[-1], (x, y)) < math.dist(arc[0], (x, y)):
                arc.reverse()
            for p in arc:
                if not pts or math.dist(pts[-1], p) > 1e-6:
                    pts.append(p)
        elif not pts or math.dist(pts[-1], (x, y)) > 1e-6:
            pts.append((x, y))
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-6:
        pts.pop()
    return pts


class Canvas:
    """A one-bit picture of the board in mils, at a fixed resolution."""

    def __init__(self, x0: float, y0: float, w_mils: float, h_mils: float, px_per_mil: float):
        self.x0, self.y0, self.k = x0, y0, px_per_mil
        self.w = max(1, int(round(w_mils * px_per_mil)))
        self.h = max(1, int(round(h_mils * px_per_mil)))
        self.img = Image.new("L", (self.w, self.h), 0)
        self.draw = ImageDraw.Draw(self.img)

    def p(self, x: float, y: float) -> tuple[float, float]:
        # Image rows run top down; board y runs up.
        return ((x - self.x0) * self.k, (self.y0 + self.h / self.k - y) * self.k)

    def polygon(self, pts: Iterable[tuple[float, float]], fill: int = 255) -> None:
        pts = [self.p(x, y) for x, y in pts]
        if len(pts) >= 3:
            self.draw.polygon(pts, fill=fill)

    def disc(self, x: float, y: float, d: float, fill: int = 255) -> None:
        cx, cy = self.p(x, y)
        r = max(d * self.k / 2, 0.5)
        self.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)

    def line(self, x1, y1, x2, y2, width, fill: int = 255) -> None:
        """A track: a stroke with round ends."""
        w = max(width * self.k, 1.0)
        self.draw.line([self.p(x1, y1), self.p(x2, y2)], fill=fill, width=int(round(w)))
        self.disc(x1, y1, width, fill)
        self.disc(x2, y2, width, fill)

    def arc(self, cx, cy, r, a1, a2, width, fill: int = 255) -> None:
        pts = _arc_points(cx, cy, r, a1, a2, step_deg=3.0)
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            self.line(xa, ya, xb, yb, width, fill)

    def array(self) -> np.ndarray:
        return np.asarray(self.img, dtype=np.uint8) > 127


def _rotated_rect(x, y, w, h, rot_deg):
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    return [(x + dx * c - dy * s, y + dx * s + dy * c)
            for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def _pad_shape(cv: Canvas, p: dict[str, Any], grow: float = 0.0, fill: int = 255) -> None:
    """A pad's land, grown by ``grow`` mils on every side."""
    x, y = float(p.get("x", 0)), float(p.get("y", 0))
    w = float(p.get("x_size", 0)) + 2 * grow
    h = float(p.get("y_size", 0)) + 2 * grow
    rot = float(p.get("rotation", 0) or 0)
    shape = str(p.get("shape", "")).lower()
    if w <= 0 or h <= 0:
        return
    if "round" in shape and "rect" in shape:
        r = min(w, h) * 0.25
        cv.polygon(_rotated_rect(x, y, w - 2 * r, h, rot), fill)
        cv.polygon(_rotated_rect(x, y, w, h - 2 * r, rot), fill)
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        for sx in (-1, 1):
            for sy in (-1, 1):
                dx, dy = sx * (w / 2 - r), sy * (h / 2 - r)
                cv.disc(x + dx * c - dy * s, y + dx * s + dy * c, 2 * r, fill)
        return
    if "round" in shape:
        if abs(w - h) < 1e-6:
            cv.disc(x, y, w, fill)
            return
        # An oval: a stroke as long as the pad, as wide as its short side.
        long_, short = max(w, h), min(w, h)
        half = (long_ - short) / 2
        ang = math.radians(rot + (0 if w >= h else 90))
        dx, dy = half * math.cos(ang), half * math.sin(ang)
        cv.line(x - dx, y - dy, x + dx, y + dy, short, fill)
        return
    if "oct" in shape:
        k = min(w, h) * 0.29
        pts = [(-w / 2 + k, -h / 2), (w / 2 - k, -h / 2), (w / 2, -h / 2 + k), (w / 2, h / 2 - k),
               (w / 2 - k, h / 2), (-w / 2 + k, h / 2), (-w / 2, h / 2 - k), (-w / 2, -h / 2 + k)]
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        cv.polygon([(x + a * c - b * s, y + a * s + b * c) for a, b in pts], fill)
        return
    cv.polygon(_rotated_rect(x, y, w, h, rot), fill)


def _stadium(cx: float, cy: float, length: float, width: float, angle_deg: float,
             n: int = 24) -> list[tuple[float, float]]:
    """A slot or oval outline: a stroke ``length`` long overall and
    ``width`` wide, along angle_deg; a circle when length <= width."""
    r = width / 2
    half = max(0.0, (length - width) / 2)
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for end, start in ((half, -90.0), (-half, 90.0)):
        for i in range(n // 2 + 1):
            t = math.radians(start + 180.0 * i / (n // 2))
            lx, ly = end + r * math.cos(t), r * math.sin(t)
            pts.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return pts


def hole_outline(p: dict[str, Any]) -> list[tuple[float, float]]:
    """A pad's drill as a closed outline in mils: round, or a slot."""
    d = float(p.get("hole_size", 0) or 0)
    width = float(p.get("hole_width", 0) or 0)
    x, y = float(p.get("x", 0)), float(p.get("y", 0))
    if str(p.get("hole_type", "")).lower().startswith("slot") and width > d:
        ang = float(p.get("rotation", 0) or 0) + float(p.get("hole_rotation", 0) or 0)
        return _stadium(x, y, width, d, ang)
    return _stadium(x, y, d, d, 0.0)


def pad_outline(p: dict[str, Any]) -> list[tuple[float, float]]:
    """A pad's land as a closed outline in mils, by its shape."""
    x, y = float(p.get("x", 0)), float(p.get("y", 0))
    w, h = float(p.get("x_size", 0) or 0), float(p.get("y_size", 0) or 0)
    rot = float(p.get("rotation", 0) or 0)
    shape = str(p.get("shape", "")).lower()
    c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    turn = lambda pts: [(x + a * c - b * s_, y + a * s_ + b * c) for a, b in pts]
    if "round" in shape and "rect" not in shape:
        return _stadium(x, y, max(w, h), min(w, h), rot + (0 if w >= h else 90))
    if "oct" in shape:
        k = min(w, h) * 0.29
        return turn([(-w / 2 + k, -h / 2), (w / 2 - k, -h / 2), (w / 2, -h / 2 + k),
                     (w / 2, h / 2 - k), (w / 2 - k, h / 2), (-w / 2 + k, h / 2),
                     (-w / 2, h / 2 - k), (-w / 2, -h / 2 + k)])
    r = min(w, h) * 0.25 if "round" in shape else 0.0
    pts = []
    for cx_, cy_, a0 in ((w / 2 - r, -h / 2 + r, -90), (w / 2 - r, h / 2 - r, 0),
                         (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180)):
        for i in range(7 if r else 1):
            t = math.radians(a0 + 90 * i / 6) if r else 0.0
            pts.append((cx_ + r * math.cos(t), cy_ + r * math.sin(t)) if r else
                       (math.copysign(w / 2, cx_), math.copysign(h / 2, cy_)))
    return turn(pts)


def _reach(outline: list[tuple[float, float]], cx: float, cy: float, angle: float) -> float:
    """How far a ray from (cx, cy) at ``angle`` runs to the outline."""
    dx, dy = math.cos(angle), math.sin(angle)
    best = 0.0
    n = len(outline)
    for i in range(n):
        (ax, ay), (bx, by) = outline[i], outline[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((ax - cx) * ey - (ay - cy) * ex) / den
        u = ((ax - cx) * dy - (ay - cy) * dx) / den
        if t > 0 and -1e-9 <= u <= 1 + 1e-9:
            best = max(best, t)
    return best


def _hole(cv: Canvas, p: dict[str, Any], fill: int = 255) -> None:
    d = float(p.get("hole_size", 0) or 0)
    if d <= 0:
        return
    x, y = float(p.get("x", 0)), float(p.get("y", 0))
    width = float(p.get("hole_width", 0) or 0)
    if str(p.get("hole_type", "")).lower().startswith("slot") and width > d:
        ang = math.radians(float(p.get("rotation", 0) or 0) + float(p.get("hole_rotation", 0) or 0))
        half = (width - d) / 2
        cv.line(x - half * math.cos(ang), y - half * math.sin(ang),
                x + half * math.cos(ang), y + half * math.sin(ang), d, fill)
    else:
        cv.disc(x, y, d, fill)


def _box(pts) -> tuple[float, float, float, float]:
    xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _on_side(layer: str, side: str) -> bool:
    return layer == ("TopLayer" if side == "top" else "BottomLayer")


def _texts(cv: Canvas, texts, layer: str) -> None:
    for t in texts or []:
        if t.get("layer") != layer or t.get("hidden") or not str(t.get("text", "")).strip():
            continue
        size = float(t.get("size", 0) or 0)
        if size <= 0:
            continue
        px = max(4, int(round(size * cv.k * 1.15)))
        try:
            font = ImageFont.truetype("arial.ttf", px)
        except OSError:
            font = ImageFont.load_default()
        text = str(t["text"])
        bbox = font.getbbox(text)
        tw, th = bbox[2] - bbox[0] + 2, bbox[3] - bbox[1] + 2
        stamp = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(stamp).text((-bbox[0] + 1, -bbox[1] + 1), text, fill=255, font=font)
        # Bottom text is mirrored so it reads from below: it runs from its
        # anchor to the left, seen from above.
        mirrored = bool(t.get("mirrored", layer.startswith("Bottom")))
        if mirrored:
            stamp = stamp.transpose(Image.FLIP_LEFT_RIGHT)
        rot = float(t.get("rotation", 0) or 0)
        if rot:
            stamp = stamp.rotate(rot, expand=True, resample=Image.BICUBIC)
        # The anchor is the text's lower corner before turning; corners
        # here are in board directions (y up), turned about the anchor.
        x, y = cv.p(float(t.get("x", 0)), float(t.get("y", 0)))
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        x0 = -tw if mirrored else 0
        corners = [(x0, 0), (x0 + tw, 0), (x0, th), (x0 + tw, th)]
        rx = [a * c - b * s for a, b in corners]
        ry = [a * s + b * c for a, b in corners]
        left, top = x + min(rx), y - max(ry)
        cv.img.paste(255, (int(round(left)), int(round(top))), stamp)


@dataclass
class SidePicture:
    rgba: np.ndarray
    params: np.ndarray


def side_picture(geom: dict[str, Any], pours: list[dict[str, Any]], side: str,
                 frame: tuple[float, float, float, float], px_per_mil: float,
                 outline: list[tuple[float, float]], look: Look,
                 soldered: frozenset = frozenset()) -> SidePicture:
    """The picture of one side: colour with alpha, and its parameter map
    (roughness, metal, coat, and the surface height in alpha). Pads of
    the ``soldered`` designators show solder instead of their finish."""
    x0, y0, w, h = frame
    copper_layer = "TopLayer" if side == "top" else "BottomLayer"
    silk_layer = "TopOverlay" if side == "top" else "BottomOverlay"

    board = Canvas(x0, y0, w, h, px_per_mil)
    board.polygon(outline)

    cu = Canvas(x0, y0, w, h, px_per_mil)
    # Pours first: their clearance holes are cut with fill 0 and would
    # erase any track drawn before them.
    for pour in pours:
        if pour.get("layer") != copper_layer:
            continue
        for piece in pour.get("copper") or []:
            for ring in piece.get("outlines") or []:
                cu.polygon([tuple(p) for p in ring])
            for ring in piece.get("holes") or []:
                cu.polygon([tuple(p) for p in ring], fill=0)
    for t in geom.get("tracks") or []:
        if t.get("layer") == copper_layer:
            cu.line(t["x1"], t["y1"], t["x2"], t["y2"], float(t.get("width", 0) or 0))
    for a in geom.get("arcs") or []:
        if a.get("layer") == copper_layer:
            cu.arc(a["cx"], a["cy"], a["r"], a["start"], a["end"], float(a.get("width", 0) or 0))
    # Altium also reports each pour's outline as a region, without the
    # pour's clearances: drawn, it would fill every gap.
    outlines = [_box(ring) for pour in pours if pour.get("layer") == copper_layer
                for piece in pour.get("copper") or [] for ring in piece.get("outlines") or []]
    for r in geom.get("regions") or []:
        if r.get("layer") == copper_layer and len(r.get("pts") or []) >= 3:
            box = _box(r["pts"])
            if any(max(abs(u - v) for u, v in zip(box, o)) < 2.0 for o in outlines):
                continue
            cu.polygon([tuple(p) for p in r["pts"]])
    pads = [p for p in geom.get("pads") or []
            if p.get("layer") in (copper_layer, "MultiLayer")]
    for p in pads:
        _pad_shape(cu, p)
    for v in geom.get("vias") or []:
        cu.disc(v["x"], v["y"], float(v.get("size", 0) or 0))
        if look.tent_vias:
            # The mask over a tented via sags into the drill: no copper there.
            cu.disc(v["x"], v["y"], float(v.get("hole_size", 0) or 0), fill=0)

    opening = Canvas(x0, y0, w, h, px_per_mil)
    for p in pads:
        _pad_shape(opening, p, grow=look.mask_expansion_mils)
    tinned = Canvas(x0, y0, w, h, px_per_mil)
    for p in pads:
        if p.get("comp") in soldered and p.get("plated", True) is not False:
            _pad_shape(tinned, p)
    if not look.tent_vias:
        for v in geom.get("vias") or []:
            opening.disc(v["x"], v["y"], float(v.get("size", 0) or 0) + 2 * look.mask_expansion_mils)

    silk = Canvas(x0, y0, w, h, px_per_mil)
    for t in geom.get("tracks") or []:
        if t.get("layer") == silk_layer:
            silk.line(t["x1"], t["y1"], t["x2"], t["y2"], float(t.get("width", 0) or 0))
    for a in geom.get("arcs") or []:
        if a.get("layer") == silk_layer:
            silk.arc(a["cx"], a["cy"], a["r"], a["start"], a["end"], float(a.get("width", 0) or 0))
    for r in geom.get("regions") or []:
        if r.get("layer") == silk_layer:
            silk.polygon([tuple(p) for p in r.get("pts") or []])
    _texts(silk, geom.get("texts"), silk_layer)

    holes = Canvas(x0, y0, w, h, px_per_mil)
    for p in geom.get("pads") or []:
        _hole(holes, p)
    if not look.tent_vias:
        for v in geom.get("vias") or []:
            holes.disc(v["x"], v["y"], float(v.get("hole_size", 0) or 0))

    on_board, copper, open_, ink, hole = (board.array(), cu.array(), opening.array(),
                                          silk.array(), holes.array())
    solder = tinned.array() if soldered else None
    # One category per texel, coloured through a table: far lighter than
    # a float colour per texel at this size.
    cat = np.full(on_board.shape, MASK_BARE, np.uint8)
    cat[copper] = MASK_COPPER
    cat[open_ & copper] = EXPOSED_COPPER
    cat[open_ & ~copper] = LAMINATE_OPEN
    cat[ink & ~open_] = SILK                 # fabs clip silk off exposed copper
    if solder is not None:
        cat[solder & open_] = SOLDERED
    cat[~on_board | hole] = NOTHING
    colours, params = _tables(look)
    params = params[cat]
    # Height, in steps of RELIEF_MM / 255: copper, then mask over it, then ink.
    step = 255.0 / RELIEF_MM
    height = np.zeros(cat.shape, np.uint8)
    height += (copper * round(COPPER_MM * step)).astype(np.uint8)
    height += ((~open_ & on_board) * round(MASK_MM * step)).astype(np.uint8)
    height += ((ink & ~open_) * round(INK_MM * step)).astype(np.uint8)
    params[..., 3] = height
    return SidePicture(rgba=colours[cat], params=params)


NOTHING, MASK_BARE, MASK_COPPER, EXPOSED_COPPER, LAMINATE_OPEN, SILK, SOLDERED = range(7)


def _tables(look: Look) -> tuple[np.ndarray, np.ndarray]:
    """Colour with alpha, and roughness / metallic / coat, per category."""
    mask = MASKS[look.mask]
    # Matte masks scatter: little clear gloss on top.
    coat = 0.25 if look.mask.startswith("matte") else 1.0
    t = MASK_TRANSLUCENCY.get(look.mask, 0.2)
    lift = tuple(min(255, int(c * (1 + 0.9 * t) + t * k)) for c, k in zip(mask, (75, 55, 10)))
    finish_rgb, finish_rough, finish_metal = FINISHES[look.finish]
    rows = {
        NOTHING: ((0, 0, 0, 0), (0.5, 0.0, 0.0)),
        MASK_BARE: ((*mask, 255), (0.55, 0.0, coat)),
        MASK_COPPER: ((*lift, 255), (0.5, 0.0, coat)),
        EXPOSED_COPPER: ((*finish_rgb, 255), (finish_rough, finish_metal, 0.0)),
        LAMINATE_OPEN: ((*LAMINATE, 255), (0.8, 0.0, 0.0)),
        SILK: ((*SILKS[look.silk], 255), (0.75, 0.0, 0.0)),
        SOLDERED: ((*SOLDER[0], 255), (SOLDER[1], 1.0, 0.0)),
    }
    colours = np.zeros((len(rows), 4), np.uint8)
    params = np.zeros((len(rows), 4), np.uint8)
    for k, (rgba, (r, m, c)) in rows.items():
        colours[k] = rgba
        params[k] = (round(r * 255), round(m * 255), round(c * 255), 255)
    return colours, params


def _downsample(a: np.ndarray, k: int) -> np.ndarray:
    """Average k x k blocks (Pillow's box filter, alpha weighted)."""
    if k <= 1:
        return a
    img = Image.fromarray(np.ascontiguousarray(a), "RGBA")
    return np.asarray(img.resize((a.shape[1] // k, a.shape[0] // k), Image.BOX))


def _downsample_params(a: np.ndarray, k: int, px_per_mm: float) -> np.ndarray:
    """The parameter map at the final size: roughness / metal / coat
    averaged, the height averaged on its own (it is not an alpha to
    weigh by) and softened as copper's edges are under mask."""
    h = a[..., 3]
    rgb = np.dstack([a[..., :3], np.full(h.shape, 255, np.uint8)])
    out = np.array(_downsample(rgb, k))
    hs = Image.fromarray(np.ascontiguousarray(h), "L")
    if k > 1:
        hs = hs.resize((h.shape[1] // k, h.shape[0] // k), Image.BOX)
    from PIL import ImageFilter
    soft = np.asarray(hs.filter(ImageFilter.GaussianBlur(max(0.6, 0.025 * px_per_mm))), np.float32)
    out[..., 3] = np.clip(soft + 0.5, 0, 255).astype(np.uint8)
    return out


@dataclass
class BoardScene:
    mesh: Mesh
    materials: list[dict[str, Any]]
    textures: list[tuple[int, int, bytes, Optional[bytes]]]
    thickness_mm: float
    size_mm: tuple[float, float]
    #: Board centre in mils: where the scene's origin sits on the board.
    centre_mils: tuple[float, float]
    notes: list[str]
    #: The parts, from the board's STEP export (parts.PartsMesh), or None.
    parts: Any = None


def build(geom: dict[str, Any], stackup: dict[str, Any], pours: list[dict[str, Any]],
          look: Look, max_texture: int = 6144, px_per_mm: float = 60.0,
          supersample: int = 2, soldered: Iterable[str] = ()) -> BoardScene:
    """The board as triangles, materials and two side textures. Pads of
    the ``soldered`` designators (the parts drawn on it) get solder
    joints instead of their bare finish."""
    soldered = frozenset(soldered)
    notes: list[str] = []
    outline = outline_points(geom.get("outline") or [])
    if len(outline) < 3:
        raise ValueError("the board has no outline to render")
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    margin = 2.0
    x0, y0 = min(xs) - margin, min(ys) - margin
    w_mils, h_mils = max(xs) - min(xs) + 2 * margin, max(ys) - min(ys) + 2 * margin
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    px_per_mil = px_per_mm * MM_PER_MIL
    longest = max(w_mils, h_mils) * px_per_mil
    if longest > max_texture:
        px_per_mil *= max_texture / longest
        notes.append(f"texture capped at {max_texture} px: "
                     f"{px_per_mil / MM_PER_MIL:.0f} px/mm")
    thick = board_thickness_mm(stackup)

    textures = []
    for side in ("top", "bottom"):
        pic = side_picture(geom, pours, side, (x0, y0, w_mils, h_mils),
                           px_per_mil * supersample, outline, look, soldered)
        rgba = _downsample(pic.rgba, supersample)[::-1]       # rows bottom up
        params = _downsample_params(pic.params, supersample, px_per_mil / MM_PER_MIL)[::-1]
        th, tw = rgba.shape[:2]
        textures.append((tw, th, np.ascontiguousarray(rgba).tobytes(),
                         np.ascontiguousarray(params).tobytes()))

    mm = MM_PER_MIL

    def at(x, y):
        return ((x - cx) * mm, (y - cy) * mm)

    materials = [
        {"base": (0.8, 0.8, 0.8), "roughness": 0.5, "texture": 0, "bump": RELIEF_MM},  # top
        {"base": (0.8, 0.8, 0.8), "roughness": 0.5, "texture": 1, "bump": RELIEF_MM},  # bottom
        {"base": _lin(EDGE), "roughness": 0.8, "texture": 2, "bump": 0.012},  # edge
        {"base": _lin(FINISHES[look.finish][0]), "roughness": 0.3,
         "metallic": 1.0},                                                   # plated barrel
        {"base": _lin(LAMINATE), "roughness": 0.85},                         # unplated wall
        {"base": _lin(SOLDER[0]), "roughness": SOLDER[1], "metallic": 1.0},  # solder
    ]
    mesh = Mesh()
    # The two faces: one quad each over the texture's frame.
    corners = [(x0, y0), (x0 + w_mils, y0), (x0 + w_mils, y0 + h_mils), (x0, y0 + h_mils)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    top = [mesh.vertex((*at(x, y), thick), (0, 0, 1), uv) for (x, y), uv in zip(corners, uvs)]
    mesh.face(top[0], top[1], top[2], 0)
    mesh.face(top[0], top[2], top[3], 0)
    bot = [mesh.vertex((*at(x, y), 0.0), (0, 0, -1), uv) for (x, y), uv in zip(corners, uvs)]
    mesh.face(bot[0], bot[2], bot[1], 1)
    mesh.face(bot[0], bot[3], bot[2], 1)
    # The edge: a wall along the outline, its texture running along it.
    pts = outline
    n = len(pts)
    perimeter = sum(math.dist(pts[i], pts[(i + 1) % n]) for i in range(n)) * MM_PER_MIL
    textures.append(_edge_texture(perimeter, thick, stackup, look))
    walked = 0.0
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
               for i in range(n))
    ccw = area > 0
    for i in range(n):
        (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
        dx, dy = xb - xa, yb - ya
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        nx, ny = (dy / length, -dx / length) if ccw else (-dy / length, dx / length)
        ua = walked / perimeter
        walked += length * MM_PER_MIL
        ub = walked / perimeter
        # The cutter and the mask round both corners a little: the wall
        # turns towards each face over its last EDGE_ROUND_MM, which is
        # what catches the light along a real board's edge.
        r = min(EDGE_ROUND_MM, thick / 4)
        tilt = 0.7071
        rows = [(0.0, (nx * tilt, ny * tilt, -tilt)), (r, (nx, ny, 0.0)),
                (thick - r, (nx, ny, 0.0)), (thick, (nx * tilt, ny * tilt, tilt))]
        ring = [(mesh.vertex((*at(xa, ya), z), n, (ua, z / thick)),
                 mesh.vertex((*at(xb, yb), z), n, (ub, z / thick))) for z, n in rows]
        for (a0, b0), (a1, b1) in zip(ring, ring[1:]):
            mesh.face(a0, b0, b1, 2)
            mesh.face(a0, b1, a1, 2)
    # Hole walls: plated for vias and plated pads.
    holes = 0
    for p in list(geom.get("pads") or []) + [dict(v, plated=True) for v in geom.get("vias") or []]:
        d = float(p.get("hole_size", 0) or 0)
        if d <= 0:
            continue
        plated = p.get("plated", True) is not False
        _barrel(mesh, [at(u, v) for u, v in hole_outline(p)], thick, 3 if plated else 4)
        holes += 1
    if soldered:
        joints = solder_joints(mesh, geom, soldered, at, thick, 5)
        if joints:
            notes.append(f"{joints} solder joints")
    return BoardScene(mesh=mesh, materials=materials, textures=textures, thickness_mm=thick,
                      size_mm=((max(xs) - min(xs)) * mm, (max(ys) - min(ys)) * mm),
                      centre_mils=(cx, cy), notes=notes)


def _stack_bands(stackup: dict[str, Any], thick: float) -> list[tuple[str, float, float]]:
    """The board's layers from the bottom up: (kind, z0, z1) in mm, kind
    copper, core, prepreg or mask, scaled to the board's thickness."""
    layers = (stackup or {}).get("layers") or []
    rows: list[tuple[str, float]] = []
    for layer in layers:
        try:
            cu = float(layer.get("copper_thickness_mils") or 0) * MM_PER_MIL
            de = float(layer.get("dielectric_height_mils") or 0) * MM_PER_MIL
        except (TypeError, ValueError):
            continue
        if cu > 0:
            rows.append(("copper", cu))
        if de > 0:
            kind = "core" if "core" in str(layer.get("dielectric_type", "")).lower() else "prepreg"
            rows.append((kind, de))
    if not rows:
        rows = [("copper", 0.035), ("core", thick - 0.07), ("copper", 0.035)]
    total = sum(h for _, h in rows) or thick
    k = thick / total
    bands, z = [], thick
    for kind, h in rows:                       # the stack is listed top down
        bands.append((kind, z - h * k, z))
        z -= h * k
    return sorted(bands, key=lambda b: b[1])


def _edge_texture(length_mm: float, thick: float, stackup: dict[str, Any], look: Look,
                  seed: int = 3) -> tuple[int, int, bytes, bytes]:
    """The routed edge as the cutter leaves it: the laminate's glass weave
    ply by ply in its resin, the resin-rich seams where copper is pulled
    back, the solder mask's lip at both faces, and the router's marks."""
    rng = np.random.default_rng(seed)
    w = int(min(8192, max(256, length_mm * 24)))
    h = int(min(512, max(64, thick * 140)))
    u = (np.arange(w, dtype=np.float32) + 0.5) * (length_mm / w)            # mm along
    z = (np.arange(h, dtype=np.float32) + 0.5) * (thick / h)                # mm up
    Z, U = np.meshgrid(z, u, indexing="ij")
    resin = np.array([158, 146, 104], np.float32)
    glass = np.array([206, 198, 166], np.float32)
    mix = np.full(Z.shape, 0.45, np.float32)              # share of glass seen
    height = np.zeros(Z.shape, np.float32)
    tint = np.zeros(Z.shape + (3,), np.float32)
    for kind, z0, z1 in _stack_bands(stackup, thick):
        inside = (Z >= z0) & (Z < z1)
        if kind == "copper":
            # Pulled back from the edge: a seam of resin where it would be.
            mix[inside] = 0.0
            height[inside] -= 0.6
            # Pulled back, it still shows through the laminate as a
            # brown shadow a few times its own thickness wide.
            zc = (z0 + z1) / 2
            shade = np.exp(-((Z - zc) / max(0.03, 2.5 * (z1 - z0))) ** 2)
            tint -= shade[..., None] * np.array([34, 36, 30], np.float32)
            continue
        ply = 0.09 if kind == "core" else 0.06
        plies = max(1, int(round((z1 - z0) / ply)))
        pitch = (z1 - z0) / plies
        for i in range(plies):
            # Plies are pressed, not machined: each waves a little along
            # the board and is a little thicker in places.
            zc = (z0 + (i + 0.5) * pitch
                  + pitch * 0.12 * np.sin(U / rng.uniform(6, 18) + rng.uniform(0, 6))
                  + pitch * 0.06 * np.sin(U / rng.uniform(1.5, 3) + rng.uniform(0, 6)))
            spread = pitch * 0.32 * (1 + 0.25 * np.sin(U / rng.uniform(4, 12) + rng.uniform(0, 6)))
            across = np.exp(-((Z - zc) / spread) ** 2)
            # Warp bundles cut across show as lenses, the fill as streaks;
            # the two take turns along the weave.
            phase = rng.uniform(0, 2 * np.pi)
            period = rng.uniform(0.42, 0.55)
            lens = 0.5 + 0.5 * np.cos(2 * np.pi * U / period + phase)
            jitter = 0.15 * np.sin(2 * np.pi * U / rng.uniform(3, 9) + rng.uniform(0, 6))
            strength = (0.8 if kind == "core" else 0.5) * across * (0.75 + 0.25 * lens + jitter)
            mix = np.where(inside, np.maximum(mix * (1 - across * 0.6), strength), mix)
            height += inside * across * lens * 0.5
        if kind == "prepreg":
            tint[inside] += (4, 2, -10)                    # more resin: yellower
    # The router: fine marks across the cut, and a scuffed, fibrous face.
    marks = rng.standard_normal(w).astype(np.float32)
    marks = np.convolve(marks, np.ones(3) / 3, mode="same")
    marks = (marks / (marks.std() or 1.0))[None, :] * np.ones((h, 1), np.float32)
    fuzz = rng.standard_normal(Z.shape).astype(np.float32)
    drift = np.repeat(np.repeat(rng.standard_normal((max(2, h // 24), max(2, w // 60))),
                                24, 0), 60, 1)[:h, :w].astype(np.float32)
    drift = np.pad(drift, ((0, max(0, h - drift.shape[0])), (0, max(0, w - drift.shape[1]))), "edge")
    # The odd resin-rich pocket where the weave opened up.
    pockets = np.zeros(Z.shape, np.float32)
    for _ in range(int(length_mm / 3)):
        cu_, cz = rng.uniform(0, length_mm), rng.uniform(0, thick)
        pockets += np.exp(-(((U - cu_) / rng.uniform(0.15, 0.5)) ** 2
                            + ((Z - cz) / rng.uniform(0.02, 0.05)) ** 2))
    mix = np.clip(mix + 0.05 * fuzz + 0.04 * drift - 0.5 * np.clip(pockets, 0, 1), 0, 1)
    # Resin and glass are never even along a board: broad lighter and
    # darker reaches, seen from afar.
    reach = np.interp(u, np.linspace(0, length_mm, max(4, int(length_mm / 3))),
                      rng.normal(0, 1, max(4, int(length_mm / 3))))
    reach = np.convolve(reach, np.ones(9) / 9, mode="same").astype(np.float32)[None, :]
    rgb = resin[None, None, :] * (1 - mix[..., None]) + glass[None, None, :] * mix[..., None]
    rgb = rgb * (1 + 0.012 * marks[..., None] + 0.02 * fuzz[..., None]
                 + 0.07 * reach[..., None]) + tint
    # Glass fibres the cutter left standing: pale specks.
    specks = rng.uniform(size=Z.shape) > 0.996
    rgb[specks] = rgb[specks] * 0.4 + 0.6 * np.array([236, 234, 222], np.float32)
    height += 0.08 * marks + 0.15 * fuzz
    # The mask's lip: it runs round the corner a little at both faces.
    mask = np.array(MASKS[look.mask], np.float32)
    lip = 0.05 * (1 + 0.4 * np.abs(np.sin(U / rng.uniform(4, 9))))
    for at_top in (True, False):
        d = (thick - Z) if at_top else Z
        cover = np.clip((lip - d) / 0.008 + 0.5, 0, 1)[..., None]
        rgb = rgb * (1 - cover) + mask[None, None, :] * cover
        height += cover[..., 0] * 0.8
    rgba = np.dstack([np.clip(rgb, 0, 255), np.full((h, w), 255.0)]).astype(np.uint8)
    params = np.zeros((h, w, 4), np.uint8)
    params[..., 0] = 200                                   # rough cut
    params[..., 2] = np.clip(np.where(height > 0.75, 120, 0), 0, 255)
    hs = height - height.min()
    params[..., 3] = np.clip(hs / (hs.max() or 1.0) * 255, 0, 255).astype(np.uint8)
    return w, h, np.ascontiguousarray(rgba).tobytes(), np.ascontiguousarray(params).tobytes()


def _lin(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def f(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return tuple(f(float(c)) for c in rgb)


def _barrel(mesh: Mesh, outline: list[tuple[float, float]], thick: float,
            material: int) -> None:
    """The inside wall of a drilled hole or slot along its outline (mm),
    facing in."""
    n = len(outline)
    cx = sum(p[0] for p in outline) / n
    cy = sum(p[1] for p in outline) / n
    ring = []
    for i in range(n + 1):
        x, y = outline[i % n]
        px, py = outline[(i - 1) % n]
        qx, qy = outline[(i + 1) % n]
        tx, ty = qx - px, qy - py
        nx, ny = -ty, tx
        if nx * (cx - x) + ny * (cy - y) < 0:
            nx, ny = -nx, -ny
        k = math.hypot(nx, ny) or 1.0
        ring.append((mesh.vertex((x, y, 0.0), (nx / k, ny / k, 0)),
                     mesh.vertex((x, y, thick), (nx / k, ny / k, 0))))
    for (a0, a1), (b0, b1) in zip(ring, ring[1:]):
        mesh.face(a0, b1, b0, material)
        mesh.face(a0, a1, b1, material)


def _square_to_disc(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return a * np.sqrt(1 - b * b / 2), b * np.sqrt(1 - a * a / 2)


def _grid_mesh(mesh: Mesh, pts: np.ndarray, up: float, material: int) -> None:
    """A grid of points (rows, cols, 3) as triangles, normals from its slopes."""
    du = np.gradient(pts, axis=1)
    dv = np.gradient(pts, axis=0)
    nrm = np.cross(du, dv)
    nrm *= np.sign(nrm[..., 2:3] * up + 1e-12)
    nrm /= np.linalg.norm(nrm, axis=2, keepdims=True) + 1e-12
    rows, cols = pts.shape[:2]
    ids = np.empty((rows, cols), np.int64)
    for i in range(rows):
        for j in range(cols):
            ids[i, j] = mesh.vertex(tuple(map(float, pts[i, j])), tuple(map(float, nrm[i, j])))
    for i in range(rows - 1):
        for j in range(cols - 1):
            a, b, c, d = ids[i, j], ids[i, j + 1], ids[i + 1, j + 1], ids[i + 1, j]
            # Wind so the face points the way the normals do.
            if up > 0:
                mesh.face(a, b, c, material)
                mesh.face(a, c, d, material)
            else:
                mesh.face(a, c, b, material)
                mesh.face(a, d, c, material)


def solder_joints(mesh: Mesh, geom: dict[str, Any], soldered: frozenset, at, thick: float,
                  material: int, n: int = 16) -> int:
    """Reflowed solder on every pad of a fitted part, as it wets a joint:
    a smooth meniscus climbing from the pad's outer edge towards the
    part's terminal on surface pads, a cone round the pin on through-hole
    pads. Returns how many joints were made."""
    mm = MM_PER_MIL
    centres = {c.get("des"): (float(c.get("x", 0)), float(c.get("y", 0)), c.get("layer"))
               for c in geom.get("components") or []}
    count = 0
    g = np.linspace(-1.0, 1.0, n)
    A, B = np.meshgrid(g, g)
    for p in geom.get("pads") or []:
        des = p.get("comp")
        if des not in soldered:
            continue
        w, h = float(p.get("x_size", 0) or 0), float(p.get("y_size", 0) or 0)
        if w <= 0 or h <= 0:
            continue
        x, y = float(p.get("x", 0)), float(p.get("y", 0))
        rot = math.radians(float(p.get("rotation", 0) or 0))
        c, s_ = math.cos(rot), math.sin(rot)
        round_ = "round" in str(p.get("shape", "")).lower() and "rect" not in str(p.get("shape", "")).lower()
        layer = p.get("layer")
        hole = float(p.get("hole_size", 0) or 0)
        if layer == "MultiLayer" and hole > 0:
            if p.get("plated", True) is False:
                continue                       # an unplated hole takes no solder
            comp_side = centres.get(des, (0, 0, "TopLayer"))[2]
            # From the drill's edge out to the land's, whatever their shapes:
            # an oblong pad round a slot gets an oblong joint.
            land, drill = pad_outline(p), hole_outline(p)
            ang = np.linspace(0.0, 2 * math.pi, 4 * n + 1)
            r_in = np.array([_reach(drill, x, y, a) for a in ang]) * 1.03
            r_out = np.array([_reach(land, x, y, a) for a in ang]) * 0.97
            if not (r_out > r_in).all():
                continue
            rad = np.linspace(0.0, 1.0, n)
            R, T = np.meshgrid(rad, ang)
            rr = r_in[:, None] + (r_out - r_in)[:, None] * R
            ring_w = float(np.median(r_out - r_in))
            for z0, up, scale in ((thick, 1.0, 0.35 if comp_side == "TopLayer" else 1.0),
                                  (0.0, -1.0, 0.35 if comp_side == "BottomLayer" else 1.0)):
                height = min(0.45, ring_w * mm * 0.9) * scale
                zz = height * (1 - R) ** 2
                xy = np.array([at(u, v) for u, v in zip((x + rr * np.cos(T)).ravel(),
                                                        (y + rr * np.sin(T)).ravel())])
                xy = xy.T.reshape(2, *R.shape)
                pts = np.dstack([xy[0], xy[1], z0 + up * (zz + 0.002)])
                _grid_mesh(mesh, pts, up, material)
            count += 1
            continue
        if layer not in ("TopLayer", "BottomLayer"):
            continue
        z0, up = (thick, 1.0) if layer == "TopLayer" else (0.0, -1.0)
        cx, cy, _ = centres.get(des, (x, y, layer))
        # Where the part's middle lies, in the pad's own frame.
        dx, dy = cx - x, cy - y
        lx, ly = dx * c + dy * s_, -dx * s_ + dy * c
        hw, hh = w / 2 * 0.98, h / 2 * 0.98
        # Reflowed solder covers the whole land in a thin rounded pillow,
        # and climbs the part's terminal in a concave fillet.
        tall = min(0.35, 0.45 * min(w, h) * mm)
        pillow = min(0.05, 0.12 * min(w, h) * mm)
        rim = np.sqrt(np.clip((1 - np.maximum(np.abs(A), np.abs(B))) / 0.12, 0, 1))
        if abs(lx) / hw < 0.3 and abs(ly) / hh < 0.3:
            # A pad under the part's middle (a thermal pad): a low dome.
            edge = 1 - np.maximum(np.abs(A), np.abs(B)) ** 6
            zz = pillow * rim + 0.15 * tall * np.sqrt(np.clip(edge, 0, 1))
        else:
            # s runs from the pad's outer edge (0) to its inner edge (1).
            if abs(lx) / hw >= abs(ly) / hh:
                along, across, sign = A, B, math.copysign(1.0, lx)
            else:
                along, across, sign = B, A, math.copysign(1.0, ly)
            sv = (1 + along * sign) / 2
            rise = np.clip(sv / 0.75, 0, 1) ** 2
            inner = np.where(sv > 0.85, 1 - ((sv - 0.85) / 0.15) ** 2, 1.0)
            side = np.sqrt(np.clip(1 - np.abs(across) ** 6, 0, 1))
            zz = pillow * rim + tall * rise * np.clip(inner, 0, 1) * side
        a_, b_ = (_square_to_disc(A, B) if round_ else (A, B))
        lxp, lyp = a_ * hw, b_ * hh
        wx = x + lxp * c - lyp * s_
        wy = y + lxp * s_ + lyp * c
        xy = np.array([at(u, v) for u, v in zip(wx.ravel(), wy.ravel())]).T.reshape(2, *A.shape)
        pts = np.dstack([xy[0], xy[1], z0 + up * (zz + 0.002)])
        _grid_mesh(mesh, pts, up, material)
        count += 1
    return count
