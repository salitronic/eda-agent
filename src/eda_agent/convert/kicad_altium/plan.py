# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Turn a read KiCad project into this server's own tool calls.

Each step is ``{"tool": name, "args": {...}}`` for a registered tool, run in
order by ``run``. The plan is pure: it is built and checked without Altium.

Frames: KiCad sheets and boards are millimetres with Y down; Altium is mils
with Y up. A sheet is flipped about its paper height rounded up to 50 mil,
so a part keeps its place on the page and on the grid; a board about the top of its outline, so the board's
lower-left corner sits at the origin.

A placed symbol keeps KiCad's angle (both turn counter-clockwise as seen).
KiCad mirrors after turning, about the vertical axis for ``mirror y`` and
the horizontal one for ``mirror x``; the second is the first after a half
turn, so it becomes Altium's angle plus 180 and Mirrored.
"""
from __future__ import annotations

import copy
import os
import re
from typing import Any, Optional

from ...libimport.easyeda.altium import build_altium_plan
from ...libimport.kicad.reader import read_kicad_footprint, read_kicad_symbol
from ...libimport.kicad.sexpr import dumps
from ...units import MILS_PER_MM

#: Grid every sheet coordinate is checked against: KiCad's own 1.27 mm is
#: 50 mil, and its half grid 25 mil.
SHEET_GRID = 25


def mil(mm: float) -> float:
    return mm * MILS_PER_MM


def imil(mm: float) -> int:
    return int(round(mil(mm)))


# --- names ------------------------------------------------------------------------


def _clean(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+\-]", "_", name).strip("_") or "PART"


def symbol_names(lib_ids: list[str]) -> dict[str, str]:
    """Altium component names for KiCad lib_ids: the symbol's own name, and
    the library's prefixed where two libraries share it."""
    short = {lid: _clean(lid.split(":", 1)[-1]) for lid in lib_ids}
    clash = {n for n in short.values() if list(short.values()).count(n) > 1}
    out = {}
    for lid in lib_ids:
        n = short[lid]
        out[lid] = _clean(lid.replace(":", "_")) if n in clash else n
    return out


def footprint_names(lib_ids: list[str]) -> dict[str, str]:
    return symbol_names(lib_ids)


# --- libraries --------------------------------------------------------------------


_LOCAL_POINTS = ("at", "start", "mid", "end", "center", "xy")


def _flip_y(node: Any) -> None:
    """Negate every Y of a footprint's local geometry, in place."""
    if not isinstance(node, list) or not node:
        return
    if node[0] in _LOCAL_POINTS and len(node) > 2:
        try:
            node[2] = repr(-float(node[2]))
        except (TypeError, ValueError):
            pass
    for child in node[1:]:
        _flip_y(child)


def _swap_sides(node: Any) -> None:
    """F.* layers to B.* and back, in place."""
    if not isinstance(node, list):
        return
    for i, v in enumerate(node):
        if isinstance(v, str) and node and node[0] in ("layer", "layers"):
            if v.startswith("F."):
                node[i] = "B." + v[2:]
            elif v.startswith("B."):
                node[i] = "F." + v[2:]
        elif isinstance(v, list):
            _swap_sides(v)


def library_footprint(fp: dict[str, Any]) -> str:
    """A placed footprint back in its library frame, as .kicad_mod text.

    A board keeps each pad's angle with the footprint's added, and a part
    on the back drawn already flipped (Y negated, F and B swapped). Both
    are undone, so the footprint built from it is the library's.
    """
    node = copy.deepcopy(fp["node"])
    angle = float(fp.get("angle") or 0.0)
    back = str(fp.get("layer", "F.Cu")).startswith("B.")
    keep = []
    for child in node:
        if isinstance(child, list) and child and child[0] in (
                "at", "property", "uuid", "path", "sheetname", "sheetfile"):
            continue
        keep.append(child)
    node = keep
    for child in node:
        if isinstance(child, list) and child and child[0] == "pad":
            at = next((c for c in child if isinstance(c, list) and c and c[0] == "at"), None)
            if at is not None:
                pad_angle = float(at[3]) if len(at) > 3 else 0.0
                local = (pad_angle - angle) % 360.0
                if back:
                    local = (-local) % 360.0
                at[3:] = [repr(round(local, 6))]
            for c in list(child):
                if isinstance(c, list) and c and c[0] in ("net", "uuid", "pinfunction",
                                                          "pintype"):
                    child.remove(c)
    if back:
        _flip_y(node)
        _swap_sides(node)
    return dumps(node)


def model_file(ref: str, project_dir: str) -> str:
    """A footprint's 3D model reference as a STEP file on this machine:
    KiCad's installed models, or the project's own (``${KIPRJMOD}``). A
    WRL reference takes its STEP sibling, the one Altium can load."""
    from ...libimport.providers.kicad_local import resolve_model_3d

    text = str(ref or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("${KIPRJMOD}") and project_dir:
        local = os.path.normpath(os.path.join(project_dir, text[len("${KIPRJMOD}"):].lstrip("/")))
        for path in (local, os.path.splitext(local)[0] + ".step",
                     os.path.splitext(local)[0] + ".stp"):
            if os.path.isfile(path) and path.lower().endswith((".step", ".stp")):
                return path
        return ""
    found = resolve_model_3d(text)
    return str(found) if found else ""


def symbol_library_text(text: str) -> str:
    return f"(kicad_symbol_lib (version 20241209) (generator eda_agent) {text})"


def library_plan(project: dict[str, Any], schlib: str, pcblib: str) -> dict[str, Any]:
    """Steps that build every symbol and footprint the project uses."""
    warnings: list[str] = []
    parts = [p for s in project["sheets"] for p in s["parts"]]
    used_symbols = sorted({p["lib_id"] for p in parts if p["lib_id"] in project["symbols"]})
    missing = sorted({p["lib_id"] for p in parts} - set(project["symbols"]))
    for lid in missing:
        warnings.append(f"symbol {lid} is not embedded in the schematic; its parts are skipped")
    sym_names = symbol_names(used_symbols)

    board = project.get("board") or {}
    by_ref = {f["reference"]: f for f in board.get("footprints") or [] if f["reference"]}
    first_fp: dict[str, dict] = {}
    for f in board.get("footprints") or []:
        first_fp.setdefault(f["lib_id"], f)
    fp_names = footprint_names(sorted(first_fp))

    # The footprints each symbol's parts use: the board's, else the
    # symbol's Footprint field when no board has the part.
    fps_of: dict[str, list[str]] = {}
    fp_of_ref: dict[str, str] = {}
    for p in parts:
        if p["lib_id"] not in sym_names or not p.get("on_board", True):
            continue
        f = by_ref.get(p["reference"])
        if f is not None:
            fp_of_ref[p["reference"]] = fp_names[f["lib_id"]]
            lst = fps_of.setdefault(p["lib_id"], [])
            if f["lib_id"] not in lst:
                lst.append(f["lib_id"])

    steps: list[dict[str, Any]] = []
    built_fps: dict[str, str] = {}
    unresolved: list[str] = []
    for fid, f in first_fp.items():
        try:
            comp = read_kicad_footprint(library_footprint(f))
        except (ValueError, TypeError, IndexError) as exc:
            warnings.append(f"footprint {fid}: {exc}")
            continue
        comp.symbol = None
        ref = getattr(comp.footprint, "model_3d_ref", "") if comp.footprint else ""
        model = model_file(ref, project.get("files", {}).get("folder", ""))
        if model:
            comp.footprint.model_3d_path = model
        elif ref:
            unresolved.append(fid)
        plan = build_altium_plan(comp, schlib, pcblib, footprint_name=fp_names[fid],
                                 exact=True)
        steps.extend(plan["steps"])
        warnings.extend(f"footprint {fid}: {w}" for w in plan["warnings"]
                        if "3D model" not in w)
        built_fps[fid] = fp_names[fid]
    if unresolved:
        warnings.append(f"{len(unresolved)} footprint(s) name a 3D model with no STEP file "
                        f"here, so they have no 3D body: {', '.join(unresolved[:5])}")

    links: list[dict[str, Any]] = []
    for lid in used_symbols:
        try:
            comp = read_kicad_symbol(symbol_library_text(project["symbols"][lid]), lid)
        except (ValueError, TypeError, IndexError) as exc:
            warnings.append(f"symbol {lid}: {exc}")
            continue
        comp.footprint = None
        # grid 1: KiCad's drawing as it is, not snapped to Altium's pin grid.
        plan = build_altium_plan(comp, schlib, pcblib, symbol_name=sym_names[lid], grid=1,
                                 exact=True)
        steps.extend(plan["steps"])
        warnings.extend(f"symbol {lid}: {w}" for w in plan["warnings"])
        for fid in fps_of.get(lid, []):
            if fid in built_fps:
                links.append({"tool": "lib_link_footprint", "args": {
                    "component_name": sym_names[lid], "footprint_name": built_fps[fid],
                    "footprint_library": pcblib}})
    # Every link after every symbol, in a row: one call that reads the
    # footprints' pads together instead of a trip to the PcbLib per symbol.
    if links:
        steps.append({"tool": "app_set_active_document", "args": {"file_path": schlib}})
        steps.extend(links)
    return {"steps": steps, "symbols": sym_names, "footprints": built_fps,
            "footprint_of": fp_of_ref, "warnings": warnings}


# --- schematic --------------------------------------------------------------------


def sheet_height(paper_mm: list[float]) -> int:
    """The height a sheet is flipped about, in mils: the paper's, rounded up
    to 50 mil so KiCad's 50 mil grid lands on Altium's (A4 is 8267.7 mil)."""
    return -(-imil(paper_mm[1]) // 50) * 50


def _sheet_point(p: list[float], height_mil: int) -> list[int]:
    return [imil(p[0]), height_mil - imil(p[1])]


def placement(angle: float, mirror: str) -> tuple[int, bool]:
    """Altium rotation and Mirrored for a KiCad symbol's angle and mirror."""
    a = int(round(angle)) % 360
    if mirror == "x":
        return (a + 180) % 360, True
    return a, mirror == "y"


_GROUND = re.compile(r"GND|VSS|EARTH|CHASSIS", re.I)


def _port_style(lib_id: str, net: str) -> str:
    name = lib_id.split(":", 1)[-1].upper()
    if "EARTH" in name or "CHASSIS" in name:
        return "gnd_earth"
    if _GROUND.search(name) or _GROUND.search(net):
        return "gnd_power"
    return "bar"


def _split_wires(wires: list[list[list[int]]], points: set[tuple[int, int]]
                 ) -> list[list[list[int]]]:
    """Each wire cut at every point that lies inside it (a junction, or
    another wire's end), so the template sees every connection as a vertex."""
    out = []
    for (a, b) in wires:
        cuts = []
        for (x, y) in points:
            if (x, y) in (tuple(a), tuple(b)):
                continue
            if a[0] == b[0] == x and min(a[1], b[1]) < y < max(a[1], b[1]):
                cuts.append((abs(y - a[1]), [x, y]))
            elif a[1] == b[1] == y and min(a[0], b[0]) < x < max(a[0], b[0]):
                cuts.append((abs(x - a[0]), [x, y]))
        pts = [a] + [p for _d, p in sorted(cuts)] + [b]
        out.extend([pts[i], pts[i + 1]] for i in range(len(pts) - 1) if pts[i] != pts[i + 1])
    return out


def _ortho(seg: list[list[int]]) -> bool:
    return seg[0][0] == seg[1][0] or seg[0][1] == seg[1][1]


def _orientation(angle: float) -> int:
    return int(round(angle / 90.0)) % 4


_IO = {"input": "input", "output": "output", "bidirectional": "bidirectional",
       "tri_state": "bidirectional", "passive": "unspecified"}


def sheet_plan(sheet: dict[str, Any], names: dict[str, str], schlib: str,
               footprint_of: dict[str, str], doc_path: str,
               child_docs: dict[str, str]) -> dict[str, Any]:
    """The template for one sheet and the steps around it: the sheet size,
    the drawing, hierarchical ports, sheet symbols with their entries, No
    ERC markers and the parts' other fields."""
    h = sheet_height(sheet["paper_mm"])
    warnings: list[str] = []
    parts = []
    pinless = []
    stamps = []
    for p in sheet["parts"]:
        if p["lib_id"] not in names:
            continue
        rot, mirrored = placement(p["angle_deg"], p.get("mirror", ""))
        x, y = _sheet_point(p["position_mm"], h)
        if not p.get("pins"):
            # A logo or a mounting hole: nothing to wire, so it is placed
            # beside the drawing rather than through the template.
            pinless.append({"designator": p["reference"], "lib_reference": names[p["lib_id"]],
                            "library_path": schlib, "x": x, "y": y, "rotation": rot,
                            **({"footprint": footprint_of[p["reference"]]}
                               if p["reference"] in footprint_of else {})})
            continue
        entry: dict[str, Any] = {"designator": p["reference"], "lib_reference": names[p["lib_id"]],
                                 "library_path": schlib, "x": x, "y": y, "rotation": rot,
                                 "value": p.get("value", "")}
        if mirrored:
            entry["mirror"] = True
        if int(p.get("unit") or 1) > 1:
            entry["part"] = int(p["unit"])
        if p["reference"] in footprint_of:
            entry["footprint"] = footprint_of[p["reference"]]
        parts.append(entry)
        # "~" is KiCad's empty field.
        fields = {k: v for k, v in (p.get("properties") or {}).items()
                  if v and v.strip() != "~" and not k.startswith("ki_")
                  and k not in ("Reference", "Value", "Footprint", "Description")}
        # One stamp per designator: a part drawn in several units has its
        # fields once.
        if fields and all(s["designator"] != p["reference"] for s in stamps):
            stamps.append({"designator": p["reference"], **fields})

    wires = [[_sheet_point(a, h), _sheet_point(b, h)] for a, b in sheet["wires"]]
    bent = [w for w in wires if not _ortho(w)]
    if bent:
        warnings.append(f"{len(bent)} diagonal wire(s) drawn with a corner")
    ends = {tuple(p) for w in wires for p in w}
    ends |= {tuple(_sheet_point(j, h)) for j in sheet["junctions"]}
    wires = _split_wires(wires, ends)

    ports = []
    flags = 0
    for pw in sheet["power"]:
        if pw["lib_id"].split(":", 1)[-1].upper() == "PWR_FLAG" or pw["net"] == "PWR_FLAG":
            # KiCad's ERC marker for a driven net, not a net of its own.
            flags += 1
            continue
        at = _sheet_point(pw["at"], h)
        style = _port_style(pw["lib_id"], pw["net"])
        base = 3 if style.startswith("gnd") else 1
        ports.append({"net": pw["net"], "at": at, "style": style,
                      "orientation": (base + _orientation(pw["angle"])) % 4})
    labels = []
    extra: list[dict[str, Any]] = []
    for lb in sheet["labels"]:
        at = _sheet_point(lb["at"], h)
        if lb["kind"] == "local":
            labels.append({"net": lb["text"], "at": at, "orientation": _orientation(lb["angle"])})
        elif lb["kind"] == "global":
            # A global label is a net every sheet shares, which in Altium
            # is a power port.
            ports.append({"net": lb["text"], "at": at, "style": "arrow",
                          "orientation": _orientation(lb["angle"])})
        else:
            width = max(300, 60 * (len(lb["text"]) + 2))
            o = _orientation(lb["angle"])
            x, y = at
            if o == 2:
                x -= width
            elif o == 3:
                y -= width
            extra.append({"tool": "sch_place_port", "args": {
                "name": lb["text"], "x": x, "y": y,
                "style": "vertical" if o in (1, 3) else "horizontal",
                "io_type": _IO.get(lb.get("shape", ""), "unspecified"), "width": width}})

    for child in sheet["sheets"]:
        x1, y1 = _sheet_point(child["at"], h)
        x2 = x1 + imil(child["size"][0])
        y2 = y1 - imil(child["size"][1])
        doc = child_docs.get(child["file"], "")
        if not doc:
            warnings.append(f"sheet symbol {child['name']}: its file was not imported")
            continue
        name = child["name"] or doc
        extra.append({"tool": "sch_place_sheet_symbol", "args": {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2, "sheet_file_name": doc,
            "sheet_name": name}})
        for pin in child["pins"]:
            px, py = _sheet_point(pin["at"], h)
            o = _orientation(pin["angle"])
            side = {2: "left", 0: "right", 1: "top", 3: "bottom"}[o]
            dist = (y1 - py) if side in ("left", "right") else (px - x1)
            extra.append({"tool": "sch_place_sheet_entry", "args": {
                "sheet_name": name, "entry_name": pin["name"],
                "io_type": _IO.get(pin.get("shape", ""), "unspecified"),
                "side": side, "distance_from_top": dist}})
    if flags:
        warnings.append(f"{flags} PWR_FLAG marker(s) left out: Altium's ERC has no such marker")
    if pinless:
        extra.append({"tool": "sch_place_components", "args": {
            "placements": pinless, "document_path": doc_path, "allow_manual_layout": True}})
    for nc in sheet["no_connects"]:
        x, y = _sheet_point(nc, h)
        extra.append({"tool": "sch_place_no_erc", "args": {"x": x, "y": y}})
    if stamps:
        extra.append({"tool": "sch_set_components_parameters",
                      "args": {"stamps": stamps, "sheet_path": doc_path}})
    for tag, n in sheet.get("unsupported", {}).items():
        warnings.append(f"{n} {tag} item(s) not imported")

    template = {"parts": parts, "wires": [{"path": w} for w in wires],
                "ports": ports, "labels": labels}
    w_mil, h_mil = -(-imil(sheet["paper_mm"][0]) // 50) * 50, h
    steps = [
        {"tool": "app_set_active_document", "args": {"file_path": doc_path}},
        {"tool": "sch_set_sheet_size", "args": {"style": "Custom", "custom_width": w_mil,
                                                "custom_height": h_mil}},
        {"tool": "sch_draw_template", "args": {"template": template, "document_path": doc_path,
                                               "grid": SHEET_GRID}},
    ] + extra
    return {"template": template, "steps": steps, "warnings": warnings}


def preview_pins(sheet: dict[str, Any]) -> dict[str, list[Any]]:
    """The sheet's pins where KiCad put them, in the template's frame, to
    check a template before Altium is involved."""
    from ...design.sch_template import PinEnd

    h = sheet_height(sheet["paper_mm"])
    out: dict[str, list[Any]] = {}
    for p in sheet["parts"]:
        for pin in p.get("pins") or []:
            x, y = _sheet_point(pin["position_mm"], h)
            out.setdefault(p["reference"], []).append(PinEnd(
                p["reference"], str(pin["number"]), str(pin["name"]), x, y, x, y, 0,
                max(1, int(p.get("unit") or 1))))
    return out


def doc_names(sheets: list[dict[str, Any]]) -> dict[str, str]:
    """An Altium .SchDoc name for each sheet file, unique within the project."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for s in sheets:
        stem = _clean(os.path.splitext(os.path.basename(s["file"]))[0])
        name, n = stem, 2
        while name.lower() in taken:
            name, n = f"{stem}_{n}", n + 1
        taken.add(name.lower())
        out[s["file"]] = name + ".SchDoc"
    return out


def schematic_plan(project: dict[str, Any], folder: str, prj_path: str,
                   lib: dict[str, Any], schlib: str) -> dict[str, Any]:
    """Sheets created in the project, then each drawn."""
    names = doc_names(project["sheets"])
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    for s in project["sheets"]:
        path = os.path.join(folder, names[s["file"]])
        steps.append({"tool": "app_create_document", "args": {
            "kind": "SCH", "file_path": path, "project_path": prj_path}})
    templates = {}
    for s in project["sheets"]:
        path = os.path.join(folder, names[s["file"]])
        child_docs = {}
        for c in s["sheets"]:
            full = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(s["file"]),
                                                                 c["file"])))
            for f, n in names.items():
                if os.path.normcase(f) == full:
                    child_docs[c["file"]] = n
        sp = sheet_plan(s, lib["symbols"], schlib, lib["footprint_of"], path, child_docs)
        steps.extend(sp["steps"])
        warnings.extend(f"{names[s['file']]}: {w}" for w in sp["warnings"])
        templates[names[s["file"]]] = sp["template"]
    steps.append({"tool": "app_save_all", "args": {}})
    return {"steps": steps, "warnings": warnings, "documents": names, "templates": templates}


# --- netlist check ----------------------------------------------------------------


def pin_groups(nets: dict[str, list[str]]) -> set[frozenset[str]]:
    """Nets as sets of REF.PIN, names set aside: two tools name the same net
    differently, and what must match is which pins it joins."""
    return {frozenset(m) for m in nets.values() if len(m) > 1}


def compare_nets(kicad: dict[str, list[str]], altium: dict[str, list[str]]) -> dict[str, Any]:
    a, b = pin_groups(kicad), pin_groups(altium)
    only_k = sorted(sorted(g) for g in a - b)
    only_a = sorted(sorted(g) for g in b - a)
    return {"same": not only_k and not only_a, "kicad_nets": len(a), "altium_nets": len(b),
            "only_in_kicad": only_k[:20], "only_in_altium": only_a[:20],
            "differences": len(only_k) + len(only_a)}
