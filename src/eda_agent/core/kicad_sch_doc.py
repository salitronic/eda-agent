# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A ``.kicad_sch`` file as items to read and spans to edit.

KiCad 10.0's API does not answer schematic requests, so schematic tools
read and write the file. ``SchDoc`` reads the items (symbols with their
pin positions on the sheet, wires, labels, junctions, sheets) and makes
span edits that leave every other line of the file as KiCad wrote it.
"""

from __future__ import annotations

import math
import os
from typing import Any, Optional

from .kicad_sexpr_edit import (Span, all_of, children, cut, find_children, first, forms,
                               head, insert_before_close, new_uuid, num, parse, q,
                               read_file, write_file)

GRID_MM = 1.27


def _at(node: list) -> tuple[float, float, float]:
    at = first(node, "at") or ["at", "0", "0"]
    vals = [float(v) for v in at[1:4]] + [0.0] * (3 - len(at[1:4]))
    return vals[0], vals[1], vals[2]


def _prop(node: list, name: str) -> Optional[str]:
    for p in all_of(node, "property"):
        if len(p) > 2 and p[1] == name:
            return p[2]
    return None


def _yes(node: list, tag: str, default: bool = True) -> bool:
    v = first(node, tag)
    if v is None:
        return default
    return len(v) < 2 or v[1] == "yes"


def symbol_transform(x: float, y: float, angle: float, mirror: str):
    """Library point (Y up) to sheet point (Y down) for a placed symbol."""
    theta = math.radians(angle)
    c, s = round(math.cos(theta)), round(math.sin(theta))

    def fn(px: float, py: float) -> tuple[float, float]:
        dx, dy = px, -py
        # Turn counter-clockwise on the sheet, then mirror.
        rx = dx * c + dy * s
        ry = -dx * s + dy * c
        if mirror == "x":
            ry = -ry
        elif mirror == "y":
            rx = -rx
        return round(x + rx, 4), round(y + ry, 4)
    return fn


def lib_pins(block: list) -> list[dict[str, Any]]:
    """Pins of a library symbol: number, name, type, unit, body style, the
    connection point (library coordinates) and angle."""
    out = []
    name = block[1] if len(block) > 1 else ""
    for sub in all_of(block, "symbol") or []:
        parts = str(sub[1]).rsplit("_", 2)
        unit = int(parts[-2]) if len(parts) == 3 and parts[-2].isdigit() else 0
        style = int(parts[-1]) if len(parts) == 3 and parts[-1].isdigit() else 0
        for pin in all_of(sub, "pin"):
            px, py, pa = _at(pin)
            nm = first(pin, "name")
            nb = first(pin, "number")
            out.append({"number": nb[1] if nb else "", "name": nm[1] if nm else "",
                        "type": pin[1] if len(pin) > 1 else "", "unit": unit,
                        "body_style": style, "at": (px, py), "angle": pa,
                        "length": float((first(pin, "length") or ["", "0"])[1])})
    for pin in all_of(block, "pin"):
        px, py, pa = _at(pin)
        nm, nb = first(pin, "name"), first(pin, "number")
        out.append({"number": nb[1] if nb else "", "name": nm[1] if nm else "",
                    "type": pin[1], "unit": 0, "body_style": 0, "at": (px, py), "angle": pa,
                    "length": float((first(pin, "length") or ["", "0"])[1])})
    del name
    return out


class SchDoc:
    """One schematic file."""

    def __init__(self, path: str):
        self.path = path
        self.text = read_file(path)
        self._parsed: Optional[list] = None

    # -- structure --------------------------------------------------------------
    @property
    def root(self) -> Span:
        return forms(self.text)[0]

    def top(self, tag: Optional[str] = None) -> list[Span]:
        spans = children(self.text, self.root)
        return [s for s in spans if tag is None or head(self.text, s) == tag]

    @property
    def tree(self) -> list:
        if self._parsed is None:
            self._parsed = parse(self.text, self.root)
        return self._parsed

    def uuid(self) -> str:
        u = first(self.tree, "uuid")
        return u[1] if u else ""

    def lib_symbol_blocks(self) -> dict[str, list]:
        libs = first(self.tree, "lib_symbols") or []
        return {b[1]: b for b in all_of(libs, "symbol")}

    def lib_symbol_text(self, lib_id: str) -> Optional[str]:
        spans = self.top("lib_symbols")
        if not spans:
            return None
        for s in find_children(self.text, spans[0], "symbol"):
            if parse(self.text, s)[1] == lib_id:
                return self.text[s[0]:s[1]]
        return None

    # -- edits ------------------------------------------------------------------
    def changed(self, text: str) -> None:
        self.text = text
        self._parsed = None

    def append(self, block: str) -> None:
        """Add a top-level item before the trailing sheet_instances and
        embedded_fonts, where KiCad writes items."""
        tail = [s for s in self.top() if head(self.text, s) in ("sheet_instances",
                                                                  "embedded_fonts")]
        if tail:
            at = self.text.rfind("\n", 0, tail[0][0]) + 1
            body = "".join("\t" + line + "\n" for line in block.rstrip("\n").split("\n"))
            self.changed(self.text[:at] + body + self.text[at:])
        else:
            self.changed(insert_before_close(self.text, self.root, block))

    def add_lib_symbol(self, block: str) -> None:
        spans = self.top("lib_symbols")
        if not spans:
            self.append("(lib_symbols\n)")
            spans = self.top("lib_symbols")
        self.changed(insert_before_close(self.text, spans[0], _reindent(block, 0), "\t\t"))

    def remove(self, span: Span) -> None:
        self.changed(cut(self.text, span))

    def replace(self, span: Span, block: str) -> None:
        start = self.text.rfind("\n", 0, span[0]) + 1
        indent = self.text[start:span[0]]
        body = _reindent(block, 0).replace("\n", "\n" + indent)
        self.changed(self.text[:span[0]] + body + self.text[span[1]:])

    def save(self) -> None:
        write_file(self.path, self.text)

    def find_uuid(self, uid: str) -> Optional[Span]:
        for s in self.top():
            node = parse(self.text, s)
            u = first(node, "uuid")
            if u and u[1] == uid:
                return s
        return None

    # -- reading ------------------------------------------------------------------
    def symbols(self) -> list[tuple[Span, list]]:
        return [(s, parse(self.text, s)) for s in self.top("symbol")]

    def symbol_info(self, node: list, path: str = "") -> dict[str, Any]:
        lib_id = (first(node, "lib_id") or ["", ""])[1]
        x, y, a = _at(node)
        mirror_node = first(node, "mirror")
        mirror = mirror_node[1] if mirror_node and len(mirror_node) > 1 else ""
        unit = int((first(node, "unit") or ["", "1"])[1])
        reference = _prop(node, "Reference") or ""
        inst = instance_reference(node, path)
        if inst:
            reference = inst
        props = {p[1]: p[2] for p in all_of(node, "property") if len(p) > 2}
        lib = self.lib_symbol_blocks().get(lib_id)
        info: dict[str, Any] = {
            "uuid": (first(node, "uuid") or ["", ""])[1], "reference": reference,
            "value": props.get("Value", ""), "lib_id": lib_id, "footprint": props.get("Footprint", ""),
            "position_mm": [x, y], "angle_deg": a, "mirror": mirror, "unit": unit,
            "in_bom": _yes(node, "in_bom"), "on_board": _yes(node, "on_board"),
            "dnp": _yes(node, "dnp", False), "properties": props,
            "power": bool(lib is not None and first(lib, "power") is not None),
        }
        if lib is not None:
            fn = symbol_transform(x, y, a, mirror)
            info["pins"] = [{"number": p["number"], "name": p["name"], "type": p["type"],
                             "position_mm": list(fn(*p["at"]))}
                            for p in lib_pins(lib) if p["unit"] in (0, unit)
                            and p["body_style"] in (0, 1)]
        return info


def instance_reference(node: list, path: str = "") -> str:
    inst = first(node, "instances")
    if not inst:
        return ""
    for project in all_of(inst, "project"):
        for p in all_of(project, "path"):
            if not path or p[1] == path:
                ref = first(p, "reference")
                if ref:
                    return ref[1]
    return ""


def _reindent(block: str, level: int) -> str:
    """The block with its continuation lines moved so its closing line sits
    at ``level`` tabs (the first line keeps its place in the text)."""
    lines = block.split("\n")
    strip = min((len(l) - len(l.lstrip("\t")) for l in lines[1:] if l.strip()), default=0)
    out = []
    for i, l in enumerate(lines):
        body = l[strip:] if l.startswith("\t" * strip) else l.lstrip("\t")
        out.append(("\t" * level if i else "") + body if body.strip() else "")
    return "\n".join(out)


# -- blocks KiCad's way --------------------------------------------------------------
def effects(size: float = 1.27, justify: str = "", hide: bool = False) -> str:
    parts = [f"(effects (font (size {num(size)} {num(size)}))"]
    if justify:
        parts.append(f" (justify {justify})")
    if hide:
        parts.append(" (hide yes)")
    return "".join(parts) + ")"


def wire_block(a, b, bus: bool = False) -> str:
    tag = "bus" if bus else "wire"
    return (f"({tag}\n\t(pts\n\t\t(xy {num(a[0])} {num(a[1])}) (xy {num(b[0])} {num(b[1])})\n\t)\n"
            f"\t(stroke\n\t\t(width 0)\n\t\t(type default)\n\t)\n\t(uuid {q(new_uuid())})\n)")


def label_block(kind: str, text: str, x: float, y: float, angle: float = 0.0,
                shape: str = "bidirectional", size: float = 1.27) -> str:
    justify = "left bottom" if angle in (0, 90) else "right bottom"
    if kind == "local":
        return (f"(label {q(text)}\n\t(at {num(x)} {num(y)} {num(angle)})\n"
                f"\t{effects(size, justify)}\n\t(uuid {q(new_uuid())})\n)")
    tag = {"global": "global_label", "hierarchical": "hierarchical_label"}[kind]
    justify = "left" if angle in (0, 90) else "right"
    extra = ""
    if kind == "global":
        extra = (f"\n\t(property \"Intersheetrefs\" \"${{INTERSHEET_REFS}}\"\n"
                 f"\t\t(at {num(x)} {num(y)} 0)\n\t\t{effects(size, justify, hide=True)}\n\t)")
    return (f"({tag} {q(text)}\n\t(shape {shape})\n\t(at {num(x)} {num(y)} {num(angle)})\n"
            f"\t{effects(size, justify)}\n\t(uuid {q(new_uuid())}){extra}\n)")


def junction_block(x: float, y: float) -> str:
    return (f"(junction\n\t(at {num(x)} {num(y)})\n\t(diameter 0)\n\t(color 0 0 0 0)\n"
            f"\t(uuid {q(new_uuid())})\n)")


def no_connect_block(x: float, y: float) -> str:
    return f"(no_connect\n\t(at {num(x)} {num(y)})\n\t(uuid {q(new_uuid())})\n)"


def text_block(text: str, x: float, y: float, angle: float = 0.0, size: float = 1.27) -> str:
    return (f"(text {q(text)}\n\t(exclude_from_sim no)\n\t(at {num(x)} {num(y)} {num(angle)})\n"
            f"\t{effects(size, 'left bottom')}\n\t(uuid {q(new_uuid())})\n)")


def project_of(sch_path: str) -> tuple[str, str]:
    """(project name, project folder) for a schematic: the .kicad_pro in
    its folder."""
    folder = os.path.dirname(os.path.abspath(sch_path))
    pros = [f for f in os.listdir(folder) if f.endswith(".kicad_pro")]
    if not pros:
        raise ValueError(f"no .kicad_pro beside {sch_path}")
    return os.path.splitext(pros[0])[0], folder


def sheet_paths(root_path: str) -> dict[str, str]:
    """Each schematic file of the project -> its instance path, walking
    the sheets from the root (first use of a file wins)."""
    out: dict[str, str] = {}

    def visit(path: str, inst: str, depth: int) -> None:
        if depth > 20 or not os.path.isfile(path):
            return
        key = os.path.normcase(os.path.abspath(path))
        if key in out:
            return
        doc = SchDoc(path)
        out[key] = inst
        for s in doc.top("sheet"):
            node = parse(doc.text, s)
            file = _prop(node, "Sheetfile") or _prop(node, "Sheet file")
            uid = (first(node, "uuid") or ["", ""])[1]
            if file:
                visit(os.path.join(os.path.dirname(path), file), inst + "/" + uid, depth + 1)

    root = SchDoc(root_path)
    visit(root_path, "/" + root.uuid(), 0)
    return out
