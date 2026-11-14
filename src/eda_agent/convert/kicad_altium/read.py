# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read a KiCad project into plain data: its sheets and its board.

Everything stays in KiCad's own terms here (millimetres, Y down, KiCad
layer names); ``plan`` turns it into Altium's. Nothing is guessed: what a
sheet or board holds that the importer does not carry yet is counted in
``warnings`` so the report can say so.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Optional

from ...core.kicad_sch_doc import SchDoc, _at, _prop
from ...core.kicad_sexpr_edit import all_of, first
from ...libimport.kicad.sexpr import loads

#: Paper sizes in mm, landscape (width, height), as KiCad names them.
PAPER_MM = {
    "A5": (210.0, 148.0), "A4": (297.0, 210.0), "A3": (420.0, 297.0),
    "A2": (594.0, 420.0), "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
    "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8),
    "D": (863.6, 558.8), "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9), "USLegal": (355.6, 215.9),
    "USLedger": (431.8, 279.4),
}


def find_project(path: str) -> dict[str, str]:
    """The project's files from a .kicad_pro, its root .kicad_sch, its
    .kicad_pcb or its folder. Raises ValueError when there is no project."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        pros = sorted(f for f in os.listdir(path) if f.endswith(".kicad_pro"))
        if len(pros) != 1:
            raise ValueError(f"{path} holds {len(pros)} .kicad_pro files; "
                             "name the one to import")
        path = os.path.join(path, pros[0])
    folder, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    if ext not in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
        raise ValueError(f"{path} is not a KiCad project, schematic or board")
    out = {"name": stem, "folder": folder,
           "pro": os.path.join(folder, stem + ".kicad_pro"),
           "sch": os.path.join(folder, stem + ".kicad_sch"),
           "pcb": os.path.join(folder, stem + ".kicad_pcb")}
    for key in ("pro", "sch", "pcb"):
        if not os.path.isfile(out[key]):
            out[key] = ""
    if not out["sch"] and not out["pcb"]:
        raise ValueError(f"no {stem}.kicad_sch or {stem}.kicad_pcb in {folder}")
    return out


def _paper(tree: list) -> list[float]:
    node = first(tree, "paper") or ["paper", "A4"]
    name = str(node[1]) if len(node) > 1 else "A4"
    if name == "User" and len(node) > 3:
        size = [float(node[2]), float(node[3])]
    else:
        size = list(PAPER_MM.get(name, PAPER_MM["A4"]))
    if "portrait" in node[2:]:
        size = [size[1], size[0]]
    return size


def _xy(node: list) -> list[list[float]]:
    pts = first(node, "pts") or []
    return [[float(p[1]), float(p[2])] for p in all_of(pts, "xy")]


def _sheet(doc: SchDoc, inst: str, name: str) -> dict[str, Any]:
    """One schematic file as placed at instance path ``inst``."""
    tree = doc.tree
    out: dict[str, Any] = {
        "file": os.path.abspath(doc.path), "name": name, "path": inst,
        "paper_mm": _paper(tree), "parts": [], "power": [], "wires": [],
        "junctions": [], "no_connects": [], "labels": [], "sheets": [],
        "unsupported": {},
    }
    for _span, node in doc.symbols():
        info = doc.symbol_info(node, inst)
        if info.get("power"):
            pins = info.get("pins") or []
            at = pins[0]["position_mm"] if pins else info["position_mm"]
            out["power"].append({"net": info["value"], "lib_id": info["lib_id"],
                                 "at": at, "angle": info["angle_deg"]})
        else:
            out["parts"].append(info)
    for node in all_of(tree, "wire"):
        pts = _xy(node)
        out["wires"].extend([pts[i], pts[i + 1]] for i in range(len(pts) - 1))
    for tag, key in (("junction", "junctions"), ("no_connect", "no_connects")):
        for node in all_of(tree, tag):
            x, y, _a = _at(node)
            out[key].append([x, y])
    for tag, kind in (("label", "local"), ("global_label", "global"),
                      ("hierarchical_label", "hierarchical")):
        for node in all_of(tree, tag):
            x, y, a = _at(node)
            shape = first(node, "shape")
            out["labels"].append({"kind": kind, "text": str(node[1]), "at": [x, y],
                                  "angle": a, "shape": shape[1] if shape else ""})
    for node in all_of(tree, "sheet"):
        x, y, _a = _at(node)
        size = first(node, "size") or ["size", "0", "0"]
        pins = []
        for pin in all_of(node, "pin"):
            px, py, pa = _at(pin)
            pins.append({"name": str(pin[1]),
                         "shape": str(pin[2]) if len(pin) > 2 else "",
                         "at": [px, py], "angle": pa})
        out["sheets"].append({
            "name": _prop(node, "Sheetname") or _prop(node, "Sheet name") or "",
            "file": _prop(node, "Sheetfile") or _prop(node, "Sheet file") or "",
            "uuid": (first(node, "uuid") or ["", ""])[1],
            "at": [x, y], "size": [float(size[1]), float(size[2])], "pins": pins})
    for tag in ("bus", "bus_entry", "bus_alias", "text", "text_box", "image",
                "polyline", "rectangle", "circle", "arc", "netclass_flag"):
        n = len(all_of(tree, tag))
        if n:
            out["unsupported"][tag] = n
    return out


def read_schematics(root: str) -> tuple[list[dict], dict[str, str], list[str]]:
    """Every sheet of the hierarchy from the root, the library symbols they
    embed (lib_id -> s-expression text) and warnings."""
    sheets: list[dict] = []
    symbols: dict[str, str] = {}
    warnings: list[str] = []
    used: dict[str, str] = {}

    def visit(path: str, inst: str, name: str, parent: Optional[str], depth: int) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if depth > 20:
            warnings.append(f"{path}: hierarchy deeper than 20 sheets, not read")
            return
        if key in used:
            warnings.append(
                f"{os.path.basename(path)} is used by more than one sheet symbol; "
                "a repeated sheet is imported once, from its first use")
            return
        if not os.path.isfile(path):
            warnings.append(f"sheet file not found: {path}")
            return
        used[key] = inst
        doc = SchDoc(path)
        for lib_id in doc.lib_symbol_blocks():
            if lib_id not in symbols:
                text = doc.lib_symbol_text(lib_id)
                if text:
                    symbols[lib_id] = text
        sheet = _sheet(doc, inst, name)
        sheet["parent"] = parent
        sheets.append(sheet)
        for child in sheet["sheets"]:
            if child["file"]:
                visit(os.path.join(os.path.dirname(path), child["file"]),
                      inst + "/" + child["uuid"], child["name"] or child["file"],
                      sheet["file"], depth + 1)

    doc = SchDoc(root)
    visit(root, "/" + doc.uuid(), os.path.splitext(os.path.basename(root))[0], None, 0)
    return sheets, symbols, warnings


# --- board ------------------------------------------------------------------------


def _num(node: Optional[list], i: int = 1, default: float = 0.0) -> float:
    try:
        return float(node[i]) if node is not None and len(node) > i else default
    except (TypeError, ValueError):
        return default


def _point(node: list, tag: str) -> Optional[list[float]]:
    p = first(node, tag)
    return [float(p[1]), float(p[2])] if p and len(p) > 2 else None


def _net_of(node: list, names: dict[str, str]) -> str:
    """A net reference by number (KiCad 9 and older) or by name."""
    n = first(node, "net")
    if not n or len(n) < 2:
        return ""
    if len(n) > 2:
        return str(n[2])
    return names.get(str(n[1]), str(n[1]))


def pad_position(fp_at: tuple[float, float, float], local: tuple[float, float]
                 ) -> list[float]:
    """A footprint-local point on the board (Y down, the angle turns the
    footprint counter-clockwise as seen)."""
    x, y, a = fp_at
    t = math.radians(a)
    px, py = local
    return [round(x + px * math.cos(t) + py * math.sin(t), 6),
            round(y - px * math.sin(t) + py * math.cos(t), 6)]


def _flag(node: list, tag: str) -> bool:
    """A KiCad yes/no token: bare (older files) or ``(tag yes)``."""
    if tag in node[1:]:
        return True
    v = first(node, tag)
    return v is not None and (len(v) < 2 or v[1] == "yes")


def _label(fp: list, fp_at: tuple[float, float, float], which: str) -> Optional[dict[str, Any]]:
    """A footprint's Reference or Value text as KiCad draws it on the board:
    the board point (mm, Y down) of its anchor, the angle it reads at, its
    layer, height and stroke (mm), mirror, justification and whether it is
    hidden. KiCad stores the text angle as a board angle and the position
    in the footprint's own frame; footprint text is kept upright unless
    marked unlocked."""
    node = next((p for p in all_of(fp, "property") if len(p) > 2 and p[1] == which), None)
    if node is None:
        node = next((t for t in all_of(fp, "fp_text")
                     if len(t) > 2 and t[1] == which.lower()), None)
    if node is None:
        return None
    at = first(node, "at") or ["at", "0", "0"]
    nums = [v for v in at[1:] if v != "unlocked"]
    lx, ly = _num(["", *nums], 1), _num(["", *nums], 2)
    angle = _num(["", *nums], 3)
    upright = not ("unlocked" in at[1:] or _flag(node, "unlocked"))
    if upright:
        angle = (angle + 180.0) % 360.0 - 180.0
        if angle > 90.0:
            angle -= 180.0
        elif angle <= -90.0:
            angle += 180.0
    effects = first(node, "effects") or ["effects"]
    font = first(effects, "font") or ["font"]
    size = first(font, "size")
    justify = [str(v) for v in (first(effects, "justify") or [])[1:]]
    return {"text": str(node[2]),
            "at": pad_position(fp_at, (lx, ly)), "angle": round(angle % 360.0, 4),
            "layer": str((first(node, "layer") or ["layer", ""])[1]),
            "height": _num(size, 1, 1.0), "stroke": _num(first(font, "thickness"), 1, 0.15),
            "mirror": "mirror" in justify,
            "justify": [j for j in justify if j != "mirror"],
            "hidden": _flag(node, "hide") or _flag(effects, "hide")}


def _footprint(node: list, names: dict[str, str]) -> dict[str, Any]:
    x, y, a = _at(node)
    layer = (first(node, "layer") or ["layer", "F.Cu"])[1]
    pads = []
    for pad in all_of(node, "pad"):
        px, py, pa = _at(pad)
        pads.append({"number": str(pad[1]), "kind": str(pad[2]) if len(pad) > 2 else "",
                     "shape": str(pad[3]) if len(pad) > 3 else "",
                     "local": [px, py], "angle": pa,
                     "at": pad_position((x, y, a), (px, py)),
                     "net": _net_of(pad, names)})
    name, comment = _label(node, (x, y, a), "Reference"), _label(node, (x, y, a), "Value")
    return {"lib_id": str(node[1]) if len(node) > 1 else "",
            "reference": _prop(node, "Reference") or (name or {}).get("text", ""),
            "value": _prop(node, "Value") or (comment or {}).get("text", ""),
            "at": [x, y], "angle": a, "layer": layer, "pads": pads,
            "labels": {"name": name, "comment": comment},
            "attributes": [str(v) for v in (first(node, "attr") or [])[1:]],
            "node": node}


def _outline(tree: list) -> list[dict[str, Any]]:
    """Edge.Cuts drawing as primitives: line, arc (start/mid/end), circle,
    rect and poly, in board millimetres."""
    out = []
    for tag in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"):
        for node in all_of(tree, tag):
            if (first(node, "layer") or ["", ""])[1] != "Edge.Cuts":
                continue
            item: dict[str, Any] = {"kind": tag[3:]}
            for key in ("start", "mid", "end", "center"):
                p = _point(node, key)
                if p is not None:
                    item[key] = p
            if tag == "gr_poly":
                item["points"] = _xy(node)
            out.append(item)
    # A footprint can carry part of the outline (a notch, a connector's
    # cut-out): its Edge.Cuts drawing in board millimetres, placed as pads
    # are.
    for fp in all_of(tree, "footprint"):
        at = _at(fp)
        for tag in ("fp_line", "fp_arc", "fp_circle", "fp_rect", "fp_poly"):
            for node in all_of(fp, tag):
                if (first(node, "layer") or ["", ""])[1] != "Edge.Cuts":
                    continue
                pts = {k: _point(node, k) for k in ("start", "mid", "end", "center")}
                place = {k: pad_position(at, tuple(p)) for k, p in pts.items() if p}
                if tag == "fp_rect" and pts["start"] and pts["end"]:
                    (x1, y1), (x2, y2) = pts["start"], pts["end"]
                    out.append({"kind": "poly", "points": [
                        pad_position(at, c) for c in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))]})
                elif tag == "fp_poly":
                    out.append({"kind": "poly", "points": [
                        pad_position(at, tuple(p)) for p in _xy(node)]})
                else:
                    out.append({"kind": tag[3:], **place})
    return out


def _zone(node: list, names: dict[str, str]) -> dict[str, Any]:
    layers = []
    if first(node, "layer"):
        layers = [first(node, "layer")[1]]
    elif first(node, "layers"):
        layers = [str(v) for v in first(node, "layers")[1:]]
    keepout = first(node, "keepout")
    connect = first(node, "connect_pads") or []
    fill = first(node, "fill") or []
    return {
        "net": _net_of(node, names) or str((first(node, "net_name") or ["", ""])[1]),
        "name": str((first(node, "name") or ["", ""])[1]),
        "layers": layers,
        "outline": _xy(first(node, "polygon") or []),
        "keepout": ({str(k[0]): str(k[1]) for k in keepout[1:] if isinstance(k, list)}
                    if keepout else None),
        "priority": int(_num(first(node, "priority"), 1, 0)),
        "min_thickness": _num(first(node, "min_thickness"), 1, 0.25),
        "clearance": _num(first(connect, "clearance"), 1, 0.0) if connect else 0.0,
        "connect": str(connect[1]) if len(connect) > 1 and not isinstance(connect[1], list)
        else "thermal",
        "thermal_gap": _num(first(fill, "thermal_gap"), 1, 0.0) if fill else 0.0,
        "thermal_bridge_width": _num(first(fill, "thermal_bridge_width"), 1, 0.0)
        if fill else 0.0,
    }


def _stackup(tree: list) -> list[dict[str, Any]]:
    setup = first(tree, "setup") or []
    stack = first(setup, "stackup") or []
    out = []
    for layer in all_of(stack, "layer"):
        out.append({"name": str(layer[1]),
                    "type": str((first(layer, "type") or ["", ""])[1]),
                    "thickness_mm": _num(first(layer, "thickness"), 1, 0.0),
                    "material": str((first(layer, "material") or ["", ""])[1]),
                    "epsilon_r": _num(first(layer, "epsilon_r"), 1, 0.0),
                    "loss_tangent": _num(first(layer, "loss_tangent"), 1, 0.0)})
    return out


def read_board(path: str) -> dict[str, Any]:
    """The board: copper layers, stack-up, footprints with pads and nets,
    tracks, arcs, vias, zones and the Edge.Cuts outline."""
    with open(path, encoding="utf-8") as fh:
        tree = loads(fh.read())
    names = {str(n[1]): str(n[2]) for n in all_of(tree, "net") if len(n) > 2}
    layers = first(tree, "layers") or []
    copper = [str(l[1]) for l in layers[1:] if isinstance(l, list) and len(l) > 2
              and str(l[1]).endswith(".Cu")]
    general = first(tree, "general") or []
    board: dict[str, Any] = {
        "file": os.path.abspath(path),
        "thickness_mm": _num(first(general, "thickness"), 1, 1.6),
        "copper_layers": copper, "stackup": _stackup(tree),
        "nets": sorted({v for v in names.values() if v}),
        "footprints": [_footprint(n, names) for n in all_of(tree, "footprint")],
        "tracks": [], "arcs": [], "vias": [],
        "zones": [_zone(n, names) for n in all_of(tree, "zone")],
        "outline": _outline(tree), "unsupported": {},
    }
    for node in all_of(tree, "segment"):
        board["tracks"].append({"start": _point(node, "start"), "end": _point(node, "end"),
                                "width": _num(first(node, "width")),
                                "layer": (first(node, "layer") or ["", ""])[1],
                                "net": _net_of(node, names)})
    for node in all_of(tree, "arc"):
        board["arcs"].append({"start": _point(node, "start"), "mid": _point(node, "mid"),
                              "end": _point(node, "end"), "width": _num(first(node, "width")),
                              "layer": (first(node, "layer") or ["", ""])[1],
                              "net": _net_of(node, names)})
    for node in all_of(tree, "via"):
        kind = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else "through"
        board["vias"].append({"at": _point(node, "at"), "size": _num(first(node, "size")),
                              "drill": _num(first(node, "drill")),
                              "layers": [str(v) for v in (first(node, "layers") or [])[1:]],
                              "kind": kind, "net": _net_of(node, names)})
    for tag in ("gr_text", "gr_text_box", "dimension", "group", "image", "target",
                "generated"):
        n = len(all_of(tree, tag))
        if n:
            board["unsupported"][tag] = n
    # KiCad 10 writes no net table: every item names its net.
    used = {p["net"] for f in board["footprints"] for p in f["pads"]}
    used |= {t["net"] for key in ("tracks", "arcs", "vias", "zones") for t in board[key]}
    board["nets"] = sorted((set(board["nets"]) | used) - {""})
    silk = sum(1 for tag in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly")
               for node in all_of(tree, tag)
               if (first(node, "layer") or ["", ""])[1] != "Edge.Cuts")
    if silk:
        board["unsupported"]["board_graphics"] = silk
    return board


def read_net_classes(pro_path: str) -> dict[str, Any]:
    """Net classes and their members from the .kicad_pro, and the board's
    minimum rules."""
    if not pro_path or not os.path.isfile(pro_path):
        return {"classes": [], "assignments": {}, "patterns": [], "rules": {}}
    with open(pro_path, encoding="utf-8") as fh:
        pro = json.load(fh)
    settings = pro.get("net_settings") or {}
    classes = []
    for c in settings.get("classes") or []:
        classes.append({k: c.get(k) for k in (
            "name", "clearance", "track_width", "via_diameter", "via_drill",
            "diff_pair_width", "diff_pair_gap", "microvia_diameter", "microvia_drill")
            if k in c})
    rules = ((pro.get("board") or {}).get("design_settings") or {}).get("rules") or {}
    return {"classes": classes,
            "assignments": dict(settings.get("netclass_assignments") or {}),
            "patterns": list(settings.get("netclass_patterns") or []),
            "rules": rules}


def read_project(path: str) -> dict[str, Any]:
    """The whole project: sheets, embedded symbols, board and net classes."""
    files = find_project(path)
    out: dict[str, Any] = {"name": files["name"], "files": files, "sheets": [],
                           "symbols": {}, "board": None, "warnings": []}
    if files["sch"]:
        out["sheets"], out["symbols"], out["warnings"] = read_schematics(files["sch"])
    if files["pcb"]:
        out["board"] = read_board(files["pcb"])
    out["net_classes"] = read_net_classes(files["pro"])
    return out
