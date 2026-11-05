# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Where a pad's custom thermal relief spokes go, from its relief outline.

Altium places a custom spoke at a point on the pad's relief outline, named
by the outline segment and a fraction along it (from a line's start vertex,
or from an arc's Angle1 counter-clockwise). ``pcb.pad_relief`` reads that
outline; this module turns the names a person or a board processor uses --
a side ``N`` / ``E`` / ``S`` / ``W`` with an optional offset, a corner
``NE`` / ``SE`` / ``SW`` / ``NW``, or an angle in degrees -- into
``segment:fraction`` and the board point the spoke will land on.

It has no opinions: every name is resolved from the outline read in the
same call, never from a table (the edge order differs by shape: a rounded
rectangle runs clockwise from W, a plain rectangle counter-clockwise, round
and oblong ends are arcs), and a name the outline cannot carry is refused
with the reason, never moved somewhere near.
"""
from __future__ import annotations

import math
import re
from typing import Any

#: Name -> direction in degrees, 0 = east, counter-clockwise.
SIDES = {"E": 0.0, "N": 90.0, "W": 180.0, "S": 270.0}
CORNERS = {"NE": 45.0, "NW": 135.0, "SW": 225.0, "SE": 315.0}
FRAMES = ("pad", "board")

_EPS = 1e-6
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


class SpokeError(ValueError):
    """A spoke the outline cannot carry; the message says why."""


def _diff(a: float, b: float) -> float:
    """Smallest angle between two directions, in degrees (0 .. 180)."""
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def _angle(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx)) % 360.0


def _span(seg: dict[str, Any]) -> float:
    span = (float(seg["a2"]) - float(seg["a1"])) % 360.0
    return span if span > _EPS else 360.0


def segments(outline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The outline with each line's end point (the next segment's start)."""
    segs = []
    n = len(outline)
    for k, seg in enumerate(outline):
        s = dict(seg)
        s["index"] = k
        s["x"], s["y"] = float(seg["x"]), float(seg["y"])
        if s.get("kind") == "line":
            nxt = outline[(k + 1) % n]
            s["x2"], s["y2"] = float(nxt["x"]), float(nxt["y"])
        else:
            for key in ("cx", "cy", "r", "a1", "a2"):
                s[key] = float(seg[key])
        segs.append(s)
    return segs


def point_on(seg: dict[str, Any], fraction: float) -> tuple[float, float]:
    """The board point at ``fraction`` along one segment."""
    if seg["kind"] == "line":
        return (seg["x"] + fraction * (seg["x2"] - seg["x"]),
                seg["y"] + fraction * (seg["y2"] - seg["y"]))
    a = math.radians(seg["a1"] + fraction * _span(seg))
    return seg["cx"] + seg["r"] * math.cos(a), seg["cy"] + seg["r"] * math.sin(a)


def _normal(seg: dict[str, Any], cx: float, cy: float) -> float:
    """Direction of a line's outward normal (away from the pad centre)."""
    dx, dy = seg["x2"] - seg["x"], seg["y2"] - seg["y"]
    nx, ny = dy, -dx
    mx, my = (seg["x"] + seg["x2"]) / 2 - cx, (seg["y"] + seg["y2"]) / 2 - cy
    if nx * mx + ny * my < 0:
        nx, ny = -nx, -ny
    return _angle(nx, ny)


def _is_cut(prev, seg, nxt, cx, cy) -> bool:
    if not all(x["kind"] == "line" for x in (prev, seg, nxt)):
        return False
    mine = _normal(seg, cx, cy)
    return all(_diff(mine, _normal(x, cx, cy)) < 67.5 for x in (prev, nxt))


def _closest(cands: list[tuple[float, Any]], name: str) -> Any:
    """The one candidate nearest the asked direction; a tie is refused."""
    cands.sort(key=lambda c: c[0])
    if len(cands) > 1 and cands[1][0] - cands[0][0] < _EPS:
        raise SpokeError(f"{name}: two outline segments face that way equally")
    return cands[0][1]


def _side(segs, name, offset, d, frame_rot, cx, cy, width):
    lines = [s for s in segs if s["kind"] == "line"
             and math.hypot(s["x2"] - s["x"], s["y2"] - s["y"]) > _EPS]
    cands = [(_diff(_normal(s, cx, cy), d), s) for s in lines]
    cands = [c for c in cands if c[0] < 45.0 - _EPS]
    if not cands:
        raise SpokeError(f"{name}: no straight side of this outline faces that way "
                         "(round and oblong ends are arcs: give the angle in degrees)")
    seg = _closest(cands, name)
    if offset == 0:
        return seg, 0.5
    if abs(offset) > 1 + _EPS:
        raise SpokeError(f"{name}: offset {offset:+g} is outside -1 .. +1")
    length = math.hypot(seg["x2"] - seg["x"], seg["y2"] - seg["y"])
    room = (length - width) / 2
    if room <= _EPS:
        raise SpokeError(f"{name}: the side is {length:g} mil long; a {width:g} mil "
                         "spoke has no room to move along it")
    # + is east on N / S and north on E / W, in the chosen frame.
    plus = math.radians((0.0 if name[0] in "NS" else 90.0) + frame_rot)
    ux, uy = (seg["x2"] - seg["x"]) / length, (seg["y2"] - seg["y"]) / length
    sign = 1.0 if ux * math.cos(plus) + uy * math.sin(plus) >= 0 else -1.0
    return seg, 0.5 + sign * offset * room / length


def _corner(segs, name, d, cx, cy):
    # A rounded corner is an arc: its middle.
    arcs = [(_diff(s["a1"] + _span(s) / 2, d), s) for s in segs
            if s["kind"] != "line" and _span(s) < 180.0]
    arcs = [c for c in arcs if c[0] < 45.0 - _EPS]
    if arcs:
        return _closest(arcs, name), 0.5
    # A cut corner (octagon) is a side turning about 45 degrees to both of
    # its straight neighbours (a rectangle's sides turn 90).
    n = len(segs)
    cut = [(_diff(_normal(s, cx, cy), d), s) for k, s in enumerate(segs)
           if _is_cut(segs[k - 1 if k else n - 1], s, segs[(k + 1) % n], cx, cy)]
    cut = [c for c in cut if c[0] < 45.0 - _EPS]
    if cut:
        return _closest(cut, name), 0.5
    # A square corner is where two sides meet: the start of the next side.
    joints = [(_diff(_angle(s["x"] - cx, s["y"] - cy), d), s) for k, s in enumerate(segs)
              if s["kind"] == "line" and segs[k - 1 if k else n - 1]["kind"] == "line"]
    joints = [c for c in joints if c[0] < 45.0 - _EPS]
    if not joints:
        raise SpokeError(f"{name}: this outline has no corner that way")
    return _closest(joints, name), 0.0


def _degrees(segs, name, d, cx, cy):
    ux, uy = math.cos(math.radians(d)), math.sin(math.radians(d))
    best = None
    for s in segs:
        if s["kind"] == "line":
            ex, ey = s["x2"] - s["x"], s["y2"] - s["y"]
            den = ux * (-ey) - uy * (-ex)
            if abs(den) < _EPS:
                continue
            rx, ry = s["x"] - cx, s["y"] - cy
            t = (rx * (-ey) - ry * (-ex)) / den
            f = (ux * ry - uy * rx) / den
            if t > _EPS and -_EPS <= f <= 1 + _EPS:
                hit = (t, s, min(max(f, 0.0), 1.0))
            else:
                continue
        else:
            ox, oy = cx - s["cx"], cy - s["cy"]
            b = ox * ux + oy * uy
            disc = b * b - (ox * ox + oy * oy - s["r"] ** 2)
            if disc < 0:
                continue
            t = -b + math.sqrt(disc)
            if t <= _EPS:
                continue
            phi = _angle(ox + t * ux, oy + t * uy)
            along = (phi - s["a1"]) % 360.0
            span = _span(s)
            if along > span + 1e-4:
                if 360.0 - along < 1e-4:
                    along = 0.0
                else:
                    continue
            hit = (t, s, min(along / span, 1.0))
        if best is None or hit[0] > best[0]:
            best = hit
    if best is None:
        raise SpokeError(f"{name}: no point of the outline lies that way")
    return best[1], best[2]


def parse(token: str) -> tuple[str, float]:
    """``"N:+0.3"`` -> ``("N", 0.3)``; ``"NE"`` -> ``("NE", 0)``; ``"30"`` -> ``("30", 0)``."""
    text = str(token).strip().upper()
    name, _, off = text.partition(":")
    name = name.strip()
    if off.strip():
        if name not in SIDES:
            raise SpokeError(f"{token}: only a side N / E / S / W takes an offset")
        try:
            return name, float(off)
        except ValueError:
            raise SpokeError(f"{token}: the offset is a number from -1 to +1") from None
    if name in SIDES or name in CORNERS or _NUMBER.match(name):
        return name, 0.0
    raise SpokeError(f"{token}: a spoke is N / E / S / W (with :offset), "
                     "NE / SE / SW / NW, or an angle in degrees")


def resolve(pad: dict[str, Any], spokes: list[str], frame: str = "pad",
            width: float | None = None) -> list[dict[str, Any]]:
    """Each spoke as ``segment``, ``fraction`` and the board point ``x`` / ``y``.

    ``pad`` is a ``pcb.pad_relief`` read: ``outline``, ``x`` / ``y`` (the
    pad centre), ``pad_rotation`` and ``width``. In the ``pad`` frame the
    names turn with the pad; in the ``board`` frame north is the top of the
    screen. Raises :class:`SpokeError` for a spoke the outline cannot take.
    """
    if frame not in FRAMES:
        raise SpokeError(f"frame is {' or '.join(FRAMES)}")
    segs = segments(pad.get("outline") or [])
    if not segs:
        raise SpokeError("the pad has no relief outline (thru-hole pads are not covered)")
    cx, cy = float(pad["x"]), float(pad["y"])
    rot = float(pad.get("pad_rotation") or 0.0) if frame == "pad" else 0.0
    w = float(width if width is not None else pad.get("width") or 0.0)
    sizes = [float(pad[k]) for k in ("size_x", "size_y") if pad.get(k) is not None]
    if sizes and w >= min(sizes):
        # A spoke as wide as the pad has no edge to leave from: a blob.
        raise SpokeError(f"a spoke {w:g} mil wide is not narrower than the pad "
                         f"({min(sizes):g} mil): give a smaller width")
    out = []
    for token in spokes:
        name, offset = parse(token)
        if name in SIDES:
            seg, frac = _side(segs, name, offset, SIDES[name] + rot, rot, cx, cy, w)
        elif name in CORNERS:
            seg, frac = _corner(segs, name, CORNERS[name] + rot, cx, cy)
        else:
            seg, frac = _degrees(segs, name, float(name) + rot, cx, cy)
        x, y = point_on(seg, frac)
        out.append({"spoke": str(token).strip(), "segment": seg["index"],
                    "fraction": round(frac, 6), "x": round(x, 4), "y": round(y, 4)})
    return out


def points(resolved: list[dict[str, Any]]) -> str:
    """The ``points`` parameter of ``pcb.pad_relief`` action spokes."""
    # Plain decimals: Altium's script reads no exponent ("3e-06" is refused).
    return ";".join(f"{r['segment']}:{r['fraction']:.6f}" for r in resolved)
