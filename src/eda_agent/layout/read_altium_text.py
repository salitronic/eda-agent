# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tracks and arcs from Altium's own text form of the live board.

Walking a board from a script costs about 0.26 ms an object in Altium
before a single property is read, and tracks and arcs are nearly all of a
board's objects. Altium writes the board it holds, unsaved edits included,
as text in well under a second (``pcb.save_board_text``, format ascii):
one record a line, ``|KEY=value|KEY=value...``. Its lengths are mils to
four places, which is the internal unit exactly, and every record names
its net, part and pour by index.

The text has no regions and no dimensions, and it names every mechanical
layer above 16 as Mechanical16, so this gives the tracks and arcs off
Mechanical16 and up, as the very entries the script's copper read sends
for them; the rest of the board is read through the script. Anything it
cannot account for (a layer it does not know, a link to nothing) raises
``TextUnusable``, and the caller reads those tracks through the script.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Altium's internal units in one mil.
_PER_MIL = 10000

_FIXED_LAYERS = {
    "TOP": "TopLayer", "BOTTOM": "BottomLayer",
    "TOPOVERLAY": "TopOverlay", "BOTTOMOVERLAY": "BottomOverlay",
    "TOPPASTE": "TopPaste", "BOTTOMPASTE": "BottomPaste",
    "TOPSOLDER": "TopSolder", "BOTTOMSOLDER": "BottomSolder",
    "KEEPOUT": "KeepOutLayer", "MULTILAYER": "MultiLayer",
    "DRILLGUIDE": "DrillGuide", "DRILLDRAWING": "DrillDrawing",
}
_NUMBERED_LAYERS = (("MID", "MidLayer"), ("PLANE", "InternalPlane"),
                    ("MECHANICAL", "Mechanical"))


class TextUnusable(ValueError):
    """The board text holds something this reader cannot turn into the
    script's entries exactly."""


def records(text: str) -> Iterable[dict[str, str]]:
    """Each ``|KEY=value|...`` line as a dict."""
    for line in text.splitlines():
        if line.startswith("|"):
            rec: dict[str, str] = {}
            for part in line[1:].split("|"):
                key, _, value = part.partition("=")
                rec[key] = value
            yield rec


def layer_name(token: str) -> str:
    """Altium's layer word in the text (TOP, MID2, MECHANICAL17) as the
    script names the layer (TopLayer, MidLayer2, Mechanical17)."""
    if token in _FIXED_LAYERS:
        return _FIXED_LAYERS[token]
    for prefix, name in _NUMBERED_LAYERS:
        if token.startswith(prefix) and token[len(prefix):].isdigit():
            return name + token[len(prefix):]
    raise TextUnusable(f"layer {token!r}")


def text_cannot_place(layer: str) -> bool:
    """A layer the text cannot name: Mechanical16 and up, all written
    Mechanical16."""
    return layer.startswith("Mechanical") and layer[10:].isdigit() and int(layer[10:]) >= 16


_LENGTH = re.compile(r"\s*(-?\d+(?:\.\d+)?)mil\s*")


def length(value: str) -> int:
    """A length as the text writes it ('61059.1696mil') in internal units.
    Refused unless it lands on a whole unit, so nothing is rounded."""
    m = _LENGTH.fullmatch(value)
    if not m:
        raise TextUnusable(f"length {value!r}")
    whole, _, frac = m.group(1).partition(".")
    if len(frac) > 4:
        raise TextUnusable(f"length {value!r} finer than the internal unit")
    sign = -1 if whole.startswith("-") else 1
    return sign * (abs(int(whole)) * _PER_MIL + int((frac + "0000")[:4]))


def _angle(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise TextUnusable(f"angle {value!r}") from None


def _number(value: float):
    """An angle as the script's JSON gives it: a whole number of degrees
    is an integer there."""
    return int(value) if value == int(value) else value


def _pour_name(value: str) -> str:
    """A pour's name, which the text writes as character codes ('71,78,68'
    is GND)."""
    if not value:
        return ""
    try:
        return "".join(chr(int(c)) for c in value.split(","))
    except ValueError:
        raise TextUnusable(f"pour name {value!r}") from None


def tracks_and_arcs(text: str, skip_layers: Iterable[str] = (),
                    mechanical: bool = True) -> list[dict]:
    """The board's tracks and arcs, as the script's copper read sends them,
    except those on ``skip_layers`` (script layer names) and, unless
    ``mechanical``, those on a layer it cannot place them on reliably
    (text_cannot_place)."""
    nets: dict[str, str] = {}
    pours: dict[str, tuple[str, str]] = {}
    designators: dict[str, str] = {}
    parts: set[str] = set()
    pending: list[dict] = []
    for rec in records(text):
        kind = rec.get("RECORD")
        if kind == "Net":
            nets[rec["ID"]] = rec.get("NAME", "")
        elif kind == "Component":
            parts.add(rec["ID"])
        elif kind == "Polygon":
            pours[rec["ID"]] = (_pour_name(rec.get("NAME", "")), rec.get("NET", ""))
        elif kind == "Text" and rec.get("DESIGNATOR") == "True" and "COMPONENT" in rec:
            designators[rec["COMPONENT"]] = rec.get("TEXT", "")
        elif kind in ("Track", "Arc"):
            pending.append(rec)
    # The text's encoding of a name outside ASCII is not pinned down: such a
    # board is read through the script.
    for name in [*nets.values(), *designators.values(), *(n for n, _ in pours.values())]:
        if not name.isascii():
            raise TextUnusable("a name outside ASCII")
    skip = set(skip_layers)
    out: list[dict] = []
    for rec in pending:
        layer = layer_name(rec.get("LAYER", ""))
        if layer in skip or (not mechanical and text_cannot_place(layer)):
            continue
        # A plane's own outline pieces: poured, of no pour by name.
        plane_piece = rec.get("POLYGON") == "-2"
        if plane_piece and not layer.startswith("InternalPlane"):
            raise TextUnusable(f"pour index -2 on {layer}")
        net = ""
        if "NET" in rec:
            if rec["NET"] not in nets:
                raise TextUnusable(f"net index {rec['NET']}")
            net = nets[rec["NET"]]
        comp = ""
        if "COMPONENT" in rec:
            if rec["COMPONENT"] not in parts:
                raise TextUnusable(f"part index {rec['COMPONENT']}")
            comp = designators.get(rec["COMPONENT"], "")
        pour = pour_net = ""
        poured = "POLYGON" in rec
        if poured and not plane_piece:
            if rec["POLYGON"] not in pours:
                raise TextUnusable(f"pour index {rec['POLYGON']}")
            pour, pour_net_id = pours[rec["POLYGON"]]
            pour_net = nets.get(pour_net_id, "")
        if rec["RECORD"] == "Track":
            v = [length(rec["X1"]), length(rec["Y1"]), length(rec["X2"]), length(rec["Y2"]),
                 length(rec["WIDTH"])]
            k = "track"
        else:
            v = [length(rec["LOCATION.X"]), length(rec["LOCATION.Y"]), length(rec["RADIUS"]),
                 _number(_angle(rec["STARTANGLE"])), _number(_angle(rec["ENDANGLE"])),
                 length(rec["WIDTH"])]
            k = "arc"
        out.append({"k": k, "v": v, "layer": layer, "net": net, "comp": comp,
                    "keepout": rec.get("KEEPOUT") == "TRUE", "in_polygon": poured,
                    "pour": pour, "pour_net": pour_net})
    return out
