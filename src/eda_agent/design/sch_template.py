# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A schematic drawn from a hand template: the agent plans, this executes.

The agent lays the sheet out the way an engineer would, every coordinate on
the grid: parts at planned positions, rails as straight wires, capacitors
hanging to ground, a divider whose midpoint runs straight to its pin. It
writes that down as a template; this module turns the template into exact
drawing primitives and checks them before anything reaches the EDA tool.

A template is a dict::

    {"parts":  [{"designator": "U1", "lib_reference": "TPS62130",
                 "library_path": "...SchLib", "x": 3000, "y": 4000,
                 "rotation": 0, "value": "TPS62130"}],
     "wires":  [{"net": "VIN", "path": ["U1.PVIN", [2500, 4600], "C1.1"]}],
     "ports":  [{"net": "+12V", "at": [2500, 4600], "style": "bar"}],
     "labels": [{"net": "PG", "at": [3900, 3600]}],
     "nets":   {"VIN": ["U1.11", "C1.1"]}}

A multi-part symbol takes one entry per part placed, the same designator
with ``"part": 2`` and so on (absent means part 1); its pins are reached by
designator, whichever part shows them, so ``"U2.B22"`` finds the pin on U2's
part 2.

A path point is ``[x, y]`` in mils or a pin anchor ``"U1.11"`` / ``"U1.PVIN"``
(the pin's electrical end, worked out from the placed symbol). Two points
that differ in both x and y get one corner, horizontal first, or vertical
first with ``"corner": "vh"`` on the wire. Junctions are not drawn: Altium
adds its own wherever a wire ends on another or three ends meet. Wires are
split wherever a pin end, another wire's end or a port sits on them, so
every connection is a vertex.

``nets`` is optional: the netlist the drawing must produce, checked pin by
pin. ``net`` on a wire, port or label names what it carries; two names on
one connected piece of copper is a short and is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Direction of a pin's orientation code (0 right, 1 up, 2 left, 3 down).
_DIRS = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}

POWER_PORT_STYLES = ("circle", "arrow", "bar", "wave",
                     "gnd_power", "gnd_signal", "gnd_earth")
GROUND_STYLES = ("gnd_power", "gnd_signal", "gnd_earth")

#: Half the body thickness assumed for a two-pin part (resistor, capacitor):
#: its pins give only the body's length.
_SLIM_HALF = 40
#: Half the thickness given to a multi-pin body whose pins all sit on one line.
_FLAT_HALF = 50


class TemplateError(ValueError):
    """The template cannot be read at all (not a checkable drawing fault)."""


@dataclass(frozen=True)
class PinEnd:
    designator: str
    number: str
    name: str
    x: int          # electrical end, where a wire connects
    y: int
    root_x: int     # body side
    root_y: int
    orientation: int
    #: Which part of a multi-part symbol shows the pin (1 for most parts).
    part: int = 1


@dataclass
class Segment:
    x1: int
    y1: int
    x2: int
    y2: int
    net: str = ""
    wire: int = -1          # index of the template wire it came from

    @property
    def horizontal(self) -> bool:
        return self.y1 == self.y2

    def ends(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return (self.x1, self.y1), (self.x2, self.y2)

    def contains(self, x: int, y: int) -> bool:
        """True if (x, y) lies on the segment, ends included."""
        if self.horizontal:
            return y == self.y1 and min(self.x1, self.x2) <= x <= max(self.x1, self.x2)
        return x == self.x1 and min(self.y1, self.y2) <= y <= max(self.y1, self.y2)

    def interior(self, x: int, y: int) -> bool:
        return self.contains(x, y) and (x, y) not in self.ends()


@dataclass
class Drawing:
    parts: list[dict] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    ports: list[dict] = field(default_factory=list)
    labels: list[dict] = field(default_factory=list)
    pins: dict[str, list[PinEnd]] = field(default_factory=dict)
    bodies: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    netlist: dict[str, list[str]] = field(default_factory=dict)
    unconnected_pins: list[str] = field(default_factory=list)
    auto_junctions: list[tuple[int, int]] = field(default_factory=list)
    #: Netlist names made up for groups nothing names (N001, ...).
    unnamed: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "wire_segments": len(self.segments),
            "auto_junctions": [list(p) for p in self.auto_junctions],
            "netlist": self.netlist,
            "unconnected_pins": self.unconnected_pins,
        }


# --- pins -------------------------------------------------------------------


def _rotate(x: int, y: int, rotation: int) -> tuple[int, int]:
    """Rotate a symbol-local point counter-clockwise by 0/90/180/270."""
    r = rotation % 360
    if r == 0:
        return x, y
    if r == 90:
        return -y, x
    if r == 180:
        return -x, -y
    return y, -x


def place_symbol_pins(designator: str, lib_pins: Iterable[dict], x: int, y: int,
                      rotation: int = 0, part: int = 1) -> list[PinEnd]:
    """Sheet positions of a symbol's pins once placed at (x, y), rotated.

    ``lib_pins`` are rows as lib_get_pin_list returns them: designator,
    name, x, y (the pin's body-side root, symbol-local), orientation and
    length. Alternate display modes and hidden pins are skipped, and so
    are the pins of every part but ``part``: the sheet shows that one.
    """
    if rotation % 90:
        raise TemplateError(f"{designator}: rotation must be 0, 90, 180 or 270")
    turns = (rotation % 360) // 90
    out: list[PinEnd] = []
    for p in lib_pins:
        if int(p.get("display_mode", 0) or 0) != 0 or p.get("hidden") in (True, "true"):
            continue
        # Only the placed part's pins, and Part Zero's, which go with every part.
        owner = int(p.get("owner_part_id", 1) or 0)
        if owner not in (0, part):
            continue
        rx, ry = _rotate(int(p.get("x", 0)), int(p.get("y", 0)), rotation)
        orient = (int(p.get("orientation", 0)) + turns) % 4
        length = int(p.get("length", 0))
        dx, dy = _DIRS[orient]
        out.append(PinEnd(designator, str(p.get("designator", "")),
                          str(p.get("name", "")),
                          x + rx + dx * length, y + ry + dy * length,
                          x + rx, y + ry, orient, part))
    return out


def pins_from_sheet(rows: Iterable[dict]) -> dict[str, list[PinEnd]]:
    """Pins as generic.get_sch_doc_pins reads them off a placed sheet."""
    out: dict[str, list[PinEnd]] = {}
    for r in rows:
        ref = str(r.get("refdes", "")).strip()
        if not ref:
            continue
        orient = int(r.get("orientation", 0)) % 4
        length = int(r.get("pin_length_mils", 0))
        rx, ry = int(r.get("x_mils", 0)), int(r.get("y_mils", 0))
        dx, dy = _DIRS[orient]
        # part_id is the instance's part: generic.get_sch_doc_pins returns
        # only the pins that instance shows, so U2 placed as parts 1 to 3
        # gives each pin once, under the part that carries it.
        out.setdefault(ref, []).append(PinEnd(
            ref, str(r.get("pin_number", "")), str(r.get("pin_name", "")),
            rx + dx * length, ry + dy * length, rx, ry, orient,
            max(1, int(r.get("part_id", 1) or 1))))
    return out


def pins_from_positions(positions: dict[str, Any]) -> dict[str, list[PinEnd]]:
    """Pins known only by their wire end, ``"REFDES.PIN": [x, y]`` in mils,
    as an editor that reports no pin direction gives them. Without the
    body side there is no body box, so the through-body check is skipped
    for these parts."""
    out: dict[str, list[PinEnd]] = {}
    for key, xy in positions.items():
        ref, _, pin = str(key).partition(".")
        if not pin or not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise TemplateError(f"pin_positions[{key!r}] must be REFDES.PIN: [x, y]")
        x, y = int(round(float(xy[0]))), int(round(float(xy[1])))
        out.setdefault(ref, []).append(PinEnd(ref, pin, "", x, y, x, y, 0))
    return out


def body_box(pins: list[PinEnd]) -> Optional[tuple[int, int, int, int]]:
    """The part body estimated from where its pins meet it."""
    if not pins or all((p.x, p.y) == (p.root_x, p.root_y) for p in pins):
        return None
    xs = [p.root_x for p in pins]
    ys = [p.root_y for p in pins]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    half = _SLIM_HALF if len(pins) <= 2 else _FLAT_HALF
    if x0 == x1:
        x0, x1 = x0 - half, x1 + half
    if y0 == y1:
        y0, y1 = y0 - half, y1 + half
    return x0, y0, x1, y1


def _pin_lookup(pins: dict[str, list[PinEnd]], anchor: str
                ) -> tuple[Optional[PinEnd], str]:
    ref, _, key = anchor.partition(".")
    if not key:
        return None, f"pin anchor {anchor!r} must read DESIGNATOR.PIN, e.g. U1.11 or U1.EN"
    plist = pins.get(ref)
    if plist is None:
        return None, f"{anchor}: no part {ref} in the template"
    by_number = [p for p in plist if p.number == key]
    if by_number:
        return by_number[0], ""
    by_name = [p for p in plist if p.name == key]
    if len(by_name) == 1:
        return by_name[0], ""
    if len(by_name) > 1:
        nums = ", ".join(p.number for p in by_name)
        return None, f"{anchor}: {len(by_name)} pins are named {key} ({nums}); use the pin number"
    return None, f"{anchor}: {ref} has no pin numbered or named {key}"


# --- building ---------------------------------------------------------------


def _point(raw: Any, pins: dict[str, list[PinEnd]], where: str,
           errors: list[dict]) -> Optional[tuple[int, int]]:
    if isinstance(raw, str):
        pin, problem = _pin_lookup(pins, raw.strip())
        if pin is None:
            errors.append({"check": "anchor", "where": where, "detail": problem})
            return None
        return pin.x, pin.y
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            return int(round(float(raw[0]))), int(round(float(raw[1])))
        except (TypeError, ValueError):
            pass
    if isinstance(raw, dict) and "x" in raw and "y" in raw:
        return int(round(float(raw["x"]))), int(round(float(raw["y"])))
    errors.append({"check": "anchor", "where": where,
                   "detail": f"{raw!r} is neither [x, y] nor a pin anchor like U1.11"})
    return None


def _orthogonal(points: list[tuple[int, int]], corner: str) -> list[tuple[int, int]]:
    out = [points[0]]
    for (x, y) in points[1:]:
        px, py = out[-1]
        if x != px and y != py:
            out.append((x, py) if corner != "vh" else (px, y))
        out.append((x, y))
    return out


def _split(segments: list[Segment], points: set[tuple[int, int]]) -> list[Segment]:
    out: list[Segment] = []
    for s in segments:
        cuts = sorted(p for p in points if s.interior(*p))
        if not cuts:
            out.append(s)
            continue
        key = (lambda p: p[0]) if s.horizontal else (lambda p: p[1])
        a, b = s.ends()
        chain = sorted([a, b, *cuts], key=key)
        if key(a) > key(b):
            chain.reverse()
        for p, q in zip(chain, chain[1:]):
            if p != q:
                out.append(Segment(p[0], p[1], q[0], q[1], s.net, s.wire))
    return out


class _Union:
    def __init__(self):
        self.parent: dict[Any, Any] = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def part_number(part: dict) -> int:
    """Which part of its symbol a template entry places (1 when not said)."""
    try:
        return max(1, int(part.get("part", 1) or 1))
    except (TypeError, ValueError):
        return 1


def body_name(ref: str, number: int) -> str:
    """How a part's body is named in checks and pictures: U2, U2/2, U2/3."""
    return ref if number == 1 else f"{ref}/{number}"


def build(template: dict, pins: dict[str, list[PinEnd]], *,
          grid: int = 100) -> Drawing:
    """Turn a template into segments, ports and labels, and check them.

    ``pins`` is every part's pins on the sheet, from place_symbol_pins (a
    preview) or pins_from_sheet (after placement, the truth).
    """
    if not isinstance(template, dict):
        raise TemplateError("the template must be an object with parts and wires")
    unknown = set(template) - {"parts", "wires", "ports", "labels", "nets"}
    if unknown:
        raise TemplateError(f"unknown template keys: {', '.join(sorted(unknown))}; "
                            "known: parts, wires, ports, labels, nets")
    d = Drawing(parts=list(template.get("parts") or []), pins=pins)
    errors, warnings = d.errors, d.warnings

    for part in d.parts:
        ref = str(part.get("designator", ""))
        number = part_number(part)
        mine = [p for p in pins.get(ref, []) if p.part == number]
        if not mine:
            errors.append({"check": "part", "where": body_name(ref, number),
                           "detail": f"no pins known for {ref}"
                                     + (f" part {number}" if number > 1 else "")})
        box = body_box(mine)
        if box:
            d.bodies[body_name(ref, number)] = box

    # Wires as straight segments.
    raw: list[Segment] = []
    for i, w in enumerate(template.get("wires") or []):
        where = f"wires[{i}]" + (f" ({w.get('net')})" if w.get("net") else "")
        path = w.get("path") if isinstance(w, dict) else None
        if not isinstance(path, list) or len(path) < 2:
            errors.append({"check": "wire", "where": where,
                           "detail": "path needs at least two points"})
            continue
        corner = str(w.get("corner", "hv")).lower()
        if corner not in ("hv", "vh"):
            errors.append({"check": "wire", "where": where,
                           "detail": "corner must be hv or vh"})
            continue
        pts = [_point(p, pins, where, errors) for p in path]
        if any(p is None for p in pts):
            continue
        pts = _orthogonal(pts, corner)
        for a, b in zip(pts, pts[1:]):
            if a != b:
                raw.append(Segment(a[0], a[1], b[0], b[1], str(w.get("net", "")), i))

    # Ports and labels.
    for kind, items in (("ports", template.get("ports") or []),
                        ("labels", template.get("labels") or [])):
        for i, item in enumerate(items):
            where = f"{kind}[{i}]"
            if not isinstance(item, dict) or not str(item.get("net", "")).strip():
                errors.append({"check": kind, "where": where, "detail": "net is required"})
                continue
            at = _point(item.get("at"), pins, where, errors)
            if at is None:
                continue
            entry = {"net": str(item["net"]).strip(), "x": at[0], "y": at[1]}
            if kind == "ports":
                style = str(item.get("style", "")).strip().lower() or (
                    "gnd_power" if "GND" in entry["net"].upper() else "bar")
                if style not in POWER_PORT_STYLES:
                    errors.append({"check": "ports", "where": where, "detail":
                                   f"unknown style {style!r}; use one of "
                                   + ", ".join(POWER_PORT_STYLES)})
                    continue
                orient = item.get("orientation")
                entry["style"] = style
                entry["orientation"] = int(orient) if orient is not None else (
                    3 if (style in GROUND_STYLES
                          or "GND" in str(entry.get("net", "")).upper()) else 1)
                d.ports.append(entry)
            else:
                entry["orientation"] = int(item.get("orientation", 0))
                d.labels.append(entry)

    # Every connection a vertex: split wires at pin ends, other wires' ends
    # and port / label points that land on them.
    all_pins = [p for plist in pins.values() for p in plist]
    vertices = {(p.x, p.y) for p in all_pins}
    vertices |= {e for s in raw for e in s.ends()}
    vertices |= {(o["x"], o["y"]) for o in d.ports + d.labels}
    d.segments = _split(raw, vertices)

    _check_grid(d, grid)
    _check_overlaps(d)
    _check_crossings(d)
    _check_bodies(d)
    _check_text(d)
    _connectivity(d, template.get("nets"))
    return d


# --- checks -----------------------------------------------------------------


def _check_grid(d: Drawing, grid: int) -> None:
    if grid <= 0:
        return
    seen: set[tuple[int, int]] = set()
    for s in d.segments:
        for p in s.ends():
            if (p[0] % grid or p[1] % grid) and p not in seen:
                seen.add(p)
    for part in d.parts:
        p = (int(part.get("x", 0)), int(part.get("y", 0)))
        if p[0] % grid or p[1] % grid:
            d.warnings.append({"check": "off_grid", "where": part.get("designator"),
                               "detail": f"placed at {list(p)}, off the {grid} mil grid"})
    if seen:
        d.warnings.append({"check": "off_grid", "where": "wires",
                           "detail": f"{len(seen)} wire vertices off the {grid} mil grid",
                           "points": sorted(list(p) for p in seen)[:20]})


def _check_overlaps(d: Drawing) -> None:
    segs = d.segments
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            if a.horizontal != b.horizontal:
                continue
            if a.horizontal and a.y1 == b.y1:
                lo = max(min(a.x1, a.x2), min(b.x1, b.x2))
                hi = min(max(a.x1, a.x2), max(b.x1, b.x2))
            elif not a.horizontal and a.x1 == b.x1:
                lo = max(min(a.y1, a.y2), min(b.y1, b.y2))
                hi = min(max(a.y1, a.y2), max(b.y1, b.y2))
            else:
                continue
            if hi > lo:
                d.errors.append({"check": "overlap", "where": f"wires[{a.wire}] and wires[{b.wire}]",
                                 "detail": "two wires run on top of each other "
                                           f"from {[a.x1, a.y1]} to {[a.x2, a.y2]}"})


def _check_crossings(d: Drawing) -> None:
    segs = d.segments
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            if a.horizontal == b.horizontal:
                continue
            h, v = (a, b) if a.horizontal else (b, a)
            x, y = v.x1, h.y1
            if h.interior(x, y) and v.interior(x, y):
                d.warnings.append({"check": "crossing", "where": f"{[x, y]}",
                                   "detail": f"wires[{h.wire}] crosses wires[{v.wire}] "
                                             "(not connected); route around it"})


def _check_bodies(d: Drawing) -> None:
    for s in d.segments:
        for ref, (x0, y0, x1, y1) in d.bodies.items():
            if s.horizontal:
                inside = y0 < s.y1 < y1 and min(s.x1, s.x2) < x1 and max(s.x1, s.x2) > x0
            else:
                inside = x0 < s.x1 < x1 and min(s.y1, s.y2) < y1 and max(s.y1, s.y2) > y0
            if inside:
                d.warnings.append({"check": "through_body", "where": ref,
                                   "detail": f"wires[{s.wire}] runs through {ref}'s body"})
    for i, a in enumerate(d.ports + d.labels):
        for b in (d.ports + d.labels)[i + 1:]:
            if (a["x"], a["y"]) == (b["x"], b["y"]):
                d.warnings.append({"check": "stacked", "where": [a["x"], a["y"]],
                                   "detail": f"{a['net']} and {b['net']} sit on the same point"})


#: Text as the preview draws it, in mils: per character and line height.
_CHAR_W, _TEXT_H = 50, 100


def text_boxes(d: Drawing) -> list[tuple[str, tuple[int, int, int, int]]]:
    """(what, box) for every designator, value, port and label text, and
    every power port's symbol, where the preview draws them."""
    by_ref = {body_name(str(p.get("designator", "")), part_number(p)): p
              for p in d.parts}
    out: list[tuple[str, tuple[int, int, int, int]]] = []
    for ref, (x0, y0, x1, y1) in d.bodies.items():
        part = by_ref.get(ref, {})
        name = str(part.get("designator", ref))
        out.append((f"{ref} designator",
                    (x1 + 20, y1, x1 + 20 + _CHAR_W * len(name), y1 + _TEXT_H)))
        if part.get("value"):
            val = str(part["value"])
            out.append((f"{ref} value",
                        (x1 + 20, y0 - _TEXT_H, x1 + 20 + _CHAR_W * len(val), y0)))
    for o in d.ports:
        dx, dy = _DIRS[o["orientation"] % 4]
        sx, sy = o["x"] + dx * 60, o["y"] + dy * 60
        out.append((f"port {o['net']} symbol",
                    (min(o["x"], sx) - 40, min(o["y"], sy) - 40,
                     max(o["x"], sx) + 40, max(o["y"], sy) + 40)))
        tx, ty = o["x"] + dx * 80, o["y"] + dy * 80
        half = _CHAR_W * len(o["net"]) // 2
        out.append((f"port {o['net']} text",
                    (tx - half, ty if dy >= 0 else ty - _TEXT_H,
                     tx + half, ty + _TEXT_H if dy >= 0 else ty)))
    for o in d.labels:
        out.append((f"label {o['net']}",
                    (o["x"], o["y"] + 15, o["x"] + _CHAR_W * len(o["net"]), o["y"] + 15 + _TEXT_H)))
    return out


def _check_text(d: Drawing) -> None:
    """Text that would sit on other text, on another part or on a power
    port's symbol: found only on the Altium screenshot before (cap values
    over ground symbols, port text over designators), and redrawn."""
    boxes = text_boxes(d)

    def hit(a, b) -> bool:
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    for i, (what, box) in enumerate(boxes):
        own = what.split(" ")[0]
        for other, obox in boxes[i + 1:]:
            if other.split(" ")[0] == own and not what.startswith(("port", "label")):
                continue
            if what.endswith("symbol") and other.endswith("symbol"):
                continue                       # stacked ports are their own check
            if hit(box, obox):
                d.warnings.append({"check": "text_overlap",
                                   "where": [box[0], box[1]],
                                   "detail": f"{what} overlaps {other} (approximate: "
                                             "Altium places texts from the symbol)"})
        if what.endswith("symbol"):
            continue
        for ref, body in d.bodies.items():
            if ref != own and hit(box, body):
                d.warnings.append({"check": "text_overlap",
                                   "where": [box[0], box[1]],
                                   "detail": f"{what} lies on {ref}'s body (approximate)"})


def _connectivity(d: Drawing, expected: Optional[dict]) -> None:
    u = _Union()
    all_pins = [p for plist in d.pins.values() for p in plist]
    pin_at: dict[tuple[int, int], list[PinEnd]] = {}
    for p in all_pins:
        pin_at.setdefault((p.x, p.y), []).append(p)
        u.find(("pin", p.designator, p.number))
    degree: dict[tuple[int, int], int] = {}
    for s in d.segments:
        a, b = s.ends()
        u.join(("pt", a), ("pt", b))
        for e in (a, b):
            degree[e] = degree.get(e, 0) + 1
    for pt, plist in pin_at.items():
        if ("pt", pt) in u.parent:
            for p in plist:
                u.join(("pin", p.designator, p.number), ("pt", pt))
        elif len(plist) > 1:
            for p in plist[1:]:
                u.join(("pin", plist[0].designator, plist[0].number),
                       ("pin", p.designator, p.number))
    # Altium's own junctions: three or more branches at a wire vertex.
    for pt, n in degree.items():
        if n + len(pin_at.get(pt, [])) >= 3:
            d.auto_junctions.append(pt)
    d.auto_junctions.sort()

    # Names: wire nets, ports and labels. Same-named ports / labels join.
    names: dict[Any, set[str]] = {}
    by_name: dict[str, list[Any]] = {}
    for s in d.segments:
        if s.net:
            names.setdefault(("pt", s.ends()[0]), set()).add(s.net)
    for o in d.ports + d.labels:
        pt = (o["x"], o["y"])
        node = ("pt", pt) if ("pt", pt) in u.parent else None
        if node is None and pt in pin_at:
            p = pin_at[pt][0]
            node = ("pin", p.designator, p.number)
        if node is None:
            d.errors.append({"check": "floating", "where": o["net"],
                             "detail": f"{o['net']} at {[o['x'], o['y']]} touches no wire or pin"})
            continue
        names.setdefault(node, set()).add(o["net"])
        by_name.setdefault(o["net"], []).append(node)
    for nodes in by_name.values():
        for n in nodes[1:]:
            u.join(nodes[0], n)
    group_names: dict[Any, set[str]] = {}
    for node, ns in names.items():
        group_names.setdefault(u.find(node), set()).update(ns)
    for root, ns in group_names.items():
        if len(ns) > 1:
            d.errors.append({"check": "short", "where": ", ".join(sorted(ns)),
                             "detail": "these nets are joined on the sheet"})

    # Dangling wire ends.
    port_pts = {(o["x"], o["y"]) for o in d.ports + d.labels}
    for pt, n in degree.items():
        if n == 1 and pt not in pin_at and pt not in port_pts:
            d.warnings.append({"check": "dangling", "where": list(pt),
                               "detail": "a wire ends here on nothing"})

    # Netlist.
    groups: dict[Any, list[str]] = {}
    for p in all_pins:
        groups.setdefault(u.find(("pin", p.designator, p.number)), []).append(
            f"{p.designator}.{p.number}")
    anon = 0
    for root, members in groups.items():
        ns = group_names.get(root)
        if not ns and len(members) == 1:
            d.unconnected_pins.append(members[0])
            continue
        if ns:
            name = sorted(ns)[0]
        else:
            anon += 1
            name = f"N{anon:03d}"
            d.unnamed.add(name)
        d.netlist.setdefault(name, []).extend(sorted(members))
    d.unconnected_pins.sort()

    if expected:
        _compare_netlist(d, expected)


def _compare_netlist(d: Drawing, expected: dict) -> None:
    pin_net = {m: n for n, ms in d.netlist.items() for m in ms}
    by_anchor: dict[str, str] = {}
    for ref, plist in d.pins.items():
        for p in plist:
            by_anchor[f"{ref}.{p.number}"] = f"{ref}.{p.number}"
            if p.name and sum(1 for q in plist if q.name == p.name) == 1:
                by_anchor[f"{ref}.{p.name}"] = f"{ref}.{p.number}"
    # A multi-part symbol's pins on parts this sheet does not draw (U2's
    # part 1 on another sheet) are expected to be missing here: the plan's
    # nets name them all. Only for those designators is a missing pin a
    # warning; anywhere else it is a mistake.
    counts: dict[str, int] = {}
    for part in d.parts:
        ref = str(part.get("designator", ""))
        counts[ref] = counts.get(ref, 0) + 1 + (part_number(part) > 1)
    split_symbols = {ref for ref, n in counts.items() if n > 1}
    for net, members in expected.items():
        resolved = []
        for m in members:
            key = by_anchor.get(str(m))
            if key is None:
                if str(m).partition(".")[0] in split_symbols:
                    d.warnings.append({"check": "netlist", "where": net,
                                       "detail": f"{m} is on none of the parts of "
                                                 "its symbol drawn here"})
                else:
                    d.errors.append({"check": "netlist", "where": net,
                                     "detail": f"{m} is not a pin of the template"})
                continue
            resolved.append(key)
        if not resolved:
            continue
        drawn = {pin_net.get(k) for k in resolved}
        if len(drawn) > 1 or None in drawn:
            split = {}
            for k in resolved:
                split.setdefault(pin_net.get(k) or "unconnected", []).append(k)
            d.errors.append({"check": "netlist", "where": net,
                             "detail": "these pins are not all joined", "drawn": split})
            continue
        got = drawn.pop()
        if got != net and got not in d.unnamed:
            d.errors.append({"check": "netlist", "where": net,
                             "detail": f"these pins are drawn as net {got}"})
            continue
        extra = sorted(set(d.netlist.get(got, [])) - set(resolved))
        if extra:
            d.errors.append({"check": "netlist", "where": net,
                             "detail": f"also joined to {', '.join(extra)}"})


# --- preview ----------------------------------------------------------------


def render_png(d: Drawing, path: str, *, title: str = "") -> str:
    """Draw the template as Altium would show it, roughly: bodies, pins,
    wires, Altium's junction dots, ports and labels, problems in red."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(11, 8))
    by_ref = {body_name(str(p.get("designator", "")), part_number(p)): p
              for p in d.parts}
    for ref, (x0, y0, x1, y1) in d.bodies.items():
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor="#fffde7",
                               edgecolor="#8d6e63", linewidth=1.0, zorder=2))
        part = by_ref.get(ref, {})
        ax.text(x1 + 20, y1, ref, fontsize=7, color="#263238", va="bottom", zorder=5)
        if part.get("value"):
            ax.text(x1 + 20, y0, str(part["value"]), fontsize=6, color="#546e7a",
                    va="top", zorder=5)
    for plist in d.pins.values():
        for p in plist:
            ax.plot([p.root_x, p.x], [p.root_y, p.y], color="#455a64", linewidth=0.8, zorder=3)
            if len(plist) > 2:
                ax.text(p.root_x, p.root_y, p.number, fontsize=4, color="#78909c", zorder=4)
    for s in d.segments:
        ax.plot([s.x1, s.x2], [s.y1, s.y2], color="#1565c0", linewidth=1.3, zorder=4)
    for (x, y) in d.auto_junctions:
        ax.plot([x], [y], marker="o", markersize=4, color="#1565c0", zorder=6)
    for o in d.ports:
        dx, dy = _DIRS[o["orientation"] % 4]
        ax.plot([o["x"], o["x"] + dx * 60], [o["y"], o["y"] + dy * 60],
                color="#c62828", linewidth=1.2, zorder=5)
        ax.text(o["x"] + dx * 80, o["y"] + dy * 80, o["net"], fontsize=6, color="#c62828",
                ha="center", va="bottom" if dy >= 0 else "top", zorder=6)
    for o in d.labels:
        ax.text(o["x"], o["y"] + 15, o["net"], fontsize=6, color="#ef6c00", zorder=6)
    for w in d.warnings + d.errors:
        loc = w.get("where")
        if isinstance(loc, list) and len(loc) == 2:
            ax.plot([loc[0]], [loc[1]], marker="x", markersize=8, color="#d50000", zorder=7)
    sub = f"{len(d.errors)} errors, {len(d.warnings)} warnings, {len(d.segments)} wire segments"
    ax.set_title((title + "\n" if title else "") + sub, fontsize=9)
    ax.set_aspect("equal")
    ax.margins(0.06)
    ax.grid(True, linewidth=0.3, color="#e0e0e0")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# --- the design plan --------------------------------------------------------


def merge_plan(template: dict, plan: Any, sheet: str = "") -> tuple[dict, dict, list[dict]]:
    """Take what the plan already knows into the template.

    The plan stays the netlist truth: each template part is looked up by
    designator and gets the plan's symbol, library and value where the
    template leaves them out, and the plan's nets over the template's parts
    become ``nets``, so the drawing is checked against the plan pin by pin.
    Returns the merged template, the parameters to stamp per designator
    (Value, Manufacturer, Manufacturer Part Number, Footprint) and notes:
    a template part the plan lacks is an error, a plan part on this sheet
    that the template does not draw is a warning.
    """
    from ._wiring import _bom_lookup, _part_parameters

    notes: list[dict] = []
    by_ref = {p.refdes: p for p in plan.parts}
    merged = dict(template)
    parts = []
    for raw in template.get("parts") or []:
        part = dict(raw)
        ref = str(part.get("designator", ""))
        pp = by_ref.get(ref)
        if pp is None:
            notes.append({"level": "error", "check": "plan", "where": ref,
                          "detail": f"{ref} is not a part of the plan"})
        else:
            part.setdefault("lib_reference", pp.lib_ref)
            if pp.lib_path:
                part.setdefault("library_path", pp.lib_path)
            if pp.value:
                part.setdefault("value", pp.value)
        parts.append(part)
    merged["parts"] = parts
    drawn = {str(p.get("designator", "")) for p in parts}
    on_sheet = [p.refdes for p in plan.parts if not sheet or p.sheet == sheet]
    missing = sorted(set(on_sheet) - drawn)
    if missing:
        notes.append({"level": "warning", "check": "plan", "where": ", ".join(missing),
                      "detail": "plan parts this template does not draw"})
    nets = dict(template.get("nets") or {})
    for net in plan.nets:
        pins = [f"{r.refdes}.{r.pin}" for r in net.pins if r.refdes in drawn]
        if pins:
            nets[net.name] = pins
    merged["nets"] = nets
    bom = _bom_lookup(plan)
    stamps = {ref: _part_parameters(by_ref[ref], bom) for ref in drawn if ref in by_ref}
    return merged, {k: v for k, v in stamps.items() if v}, notes
