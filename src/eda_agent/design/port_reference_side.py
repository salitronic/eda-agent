# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which side of its port a port cross-reference belongs on.

With Port Cross References on, Altium prints each port's "Sheet[zone]"
reference as a ``CrossRef`` parameter past the port end it takes as free.
That end is decided when the reference is made; a port moved without its
wire keeps it, so a block moved on the sheet can leave references lying on
their wires, and compiling again does not move them. Location and
Justification of the parameter are writable, so two writes per port put a
reference back once the wired end is known.

Altium's ``ConnectedEnd`` says which end is wired (1 the location end, 2
the far end) and cannot be set by a script. When it reads 0 or 3 the
wires and pin ends at each end decide. Everything here works on the rows
``obj_query`` returns, so it runs without Altium.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

#: Distance Altium leaves between a port end and its reference, mils.
OFFSET = 20.0
#: A reference within this of its place is left alone (hand moves on a
#: 10 mil grid land a few mil off).
CLOSE_ENOUGH = 10.0
#: How far past a port end a reference is still taken as that port's.
REACH = 4 * OFFSET
#: CrossRef justifications: text running to the right of, or to the left
#: of, the anchor (Altium's TTextJustification values).
TEXT_RIGHTWARD = 3
TEXT_LEFTWARD = 5
#: Port styles drawn along X (none, left, right, left-and-right).
ALONG_X = {0, 1, 2, 3}


def _num(row: dict, key: str) -> Optional[float]:
    try:
        return float(row.get(key))
    except (TypeError, ValueError):
        return None


@dataclass
class Port:
    name: str
    start: float
    stop: float
    y: float
    style: int
    connected_end: Optional[int]

    @classmethod
    def from_row(cls, row: dict) -> "Port | str":
        """A port, or why the row cannot be one."""
        x, y, width = _num(row, "Location.X"), _num(row, "Location.Y"), _num(row, "Width")
        if x is None or y is None or not width:
            return "no location or width"
        end = _num(row, "ConnectedEnd")
        return cls(str(row.get("Name", "")), x, x + width, y, int(_num(row, "Style") or 0),
                   None if end is None else int(end))


def segments_of(wires: Iterable[dict]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Wire segments from each wire's ``Vertices`` text ("x,y;x,y;...")."""
    found = []
    for wire in wires:
        corners = []
        for chunk in str(wire.get("Vertices") or "").split(";"):
            try:
                x, y = (float(v) for v in chunk.split(","))
            except ValueError:
                continue
            corners.append((x, y))
        found += list(zip(corners, corners[1:]))
    return found


def reaches(point: tuple[float, float], segments, pin_ends, slack: float = 0.5) -> bool:
    """Whether a pin end or any wire segment meets the point."""
    px, py = point
    if any(abs(px - x) <= slack and abs(py - y) <= slack for x, y in pin_ends):
        return True
    for (ax, ay), (bx, by) in segments:
        if not (min(ax, bx) - slack <= px <= max(ax, bx) + slack
                and min(ay, by) - slack <= py <= max(ay, by) + slack):
            continue
        span = max(abs(bx - ax), abs(by - ay))
        if span == 0 or abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / span <= slack:
            return True
    return False


def wired_side(port: Port, segments, pin_ends) -> tuple[Optional[str], str]:
    """("start" | "stop" | None, how it was decided or why not)."""
    if port.connected_end == 1:
        return "start", "ConnectedEnd"
    if port.connected_end == 2:
        return "stop", "ConnectedEnd"
    at_start = reaches((port.start, port.y), segments, pin_ends)
    at_stop = reaches((port.stop, port.y), segments, pin_ends)
    if at_start and not at_stop:
        return "start", "wires"
    if at_stop and not at_start:
        return "stop", "wires"
    both = at_start or port.connected_end == 3
    return None, "both ends wired" if both else "neither end wired"


def _claim(references: list[dict], ports: list[Port]) -> dict[int, int]:
    """Port index to reference index: each reference goes to the port on its
    row whose end it lies nearest, and a port keeps the nearest claimant."""
    best: dict[int, tuple[float, int]] = {}
    for r, ref in enumerate(references):
        rx, ry = _num(ref, "Location.X"), _num(ref, "Location.Y")
        options = []
        for p, port in enumerate(ports):
            if abs(ry - port.y) > 1 or not port.start - REACH <= rx <= port.stop + REACH:
                continue
            options.append((min(abs(rx - (port.start - OFFSET)),
                                abs(rx - (port.stop + OFFSET))), p))
        if not options:
            continue
        gap, p = min(options)
        if p not in best or gap < best[p][0]:
            best[p] = (gap, r)
    return {p: r for p, (_, r) in best.items()}


def place_references(ports: list[dict], wires: list[dict], references: list[dict],
                     pins: Iterable[dict] = ()) -> dict[str, Any]:
    """Where each port's reference should sit.

    Args:
        ports: ePort rows (Name, Location.X, Location.Y, Width, Style,
            ConnectedEnd).
        wires: eWire rows with Vertices.
        references: eParameter rows named CrossRef (Text, Location.X,
            Location.Y, Justification).
        pins: ePin rows with ConnectionX, ConnectionY; a port can sit on a
            pin end with no wire.

    Returns:
        ``to_move`` (with the new x and justification), ``already_right``,
        ``skipped`` (port and why) and ``orphan_references``.
    """
    segments = segments_of(wires)
    pin_ends = [(x, y) for x, y in ((_num(p, "ConnectionX"), _num(p, "ConnectionY"))
                                    for p in pins) if x is not None and y is not None]
    usable = [r for r in references
              if _num(r, "Location.X") is not None and _num(r, "Location.Y") is not None]
    skipped: list[dict] = []
    flat: list[Port] = []
    for row in ports:
        port = Port.from_row(row)
        if isinstance(port, str):
            skipped.append({"port": str(row.get("Name", "")), "why": port})
        elif port.style not in ALONG_X:
            skipped.append({"port": port.name, "why": "a vertical port"})
        else:
            flat.append(port)
    owner = _claim(usable, flat)
    to_move: list[dict] = []
    already_right: list[dict] = []
    for p, port in enumerate(flat):
        if p not in owner:
            skipped.append({"port": port.name, "why": "no CrossRef beside it"})
            continue
        ref = usable[owner[p]]
        side, how = wired_side(port, segments, pin_ends)
        if side is None:
            skipped.append({"port": port.name, "why": how,
                            "connected_end": port.connected_end})
            continue
        new_x = port.stop + OFFSET if side == "start" else port.start - OFFSET
        new_just = TEXT_RIGHTWARD if side == "start" else TEXT_LEFTWARD
        entry = {"port": port.name, "wired": "location end" if side == "start" else "far end",
                 "decided_by": how, "text": ref.get("Text", ""),
                 "x": _num(ref, "Location.X"), "y": _num(ref, "Location.Y"),
                 "justification": int(_num(ref, "Justification") or 0),
                 "new_x": round(new_x, 4), "new_justification": new_just}
        right = (abs(entry["x"] - new_x) <= CLOSE_ENOUGH
                 and entry["justification"] == new_just)
        (already_right if right else to_move).append(entry)
    return {"to_move": to_move, "already_right": already_right, "skipped": skipped,
            "orphan_references": len(usable) - len(owner)}
