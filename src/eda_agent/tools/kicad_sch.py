# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic tools for KiCad, working on the ``.kicad_sch`` files.

KiCad 10.0's API answers no schematic request, so these read and write
the files. The open schematic editor does not reload a file written
here: every write reply says so. Coordinates are millimetres on the
sheet, Y down, on KiCad's 1.27 mm grid.

Write tools take ``items`` (a list of their own argument dicts; the
arguments beside it apply to all) and write the file once per call.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from ..bridge import kicad_api as api
from ..bridge.kicad_bridge import (KiCadApiError, KiCadNotReachableError,
                                   get_kicad_bridge)
from ..core import kicad_libtables as libtables
from ..core.kicad_sch_doc import (SchDoc, _at, _prop, _reindent, effects, instance_reference,
                                  junction_block, label_block, lib_pins, no_connect_block,
                                  project_of, sheet_paths, symbol_transform, text_block,
                                  wire_block)
from ..core.kicad_sexpr_edit import (all_of, children, find_children, first, forms, head,
                                     new_uuid, num, parse, q, read_file, write_file)

RELOAD_NOTE = ("Written to the file only. KiCad's open schematic editor does not reload "
               "it: use File > Revert or reopen the schematic to see it, and do not save "
               "the open schematic before that, which would overwrite this change.")

_FAILURES = (KiCadNotReachableError, KiCadApiError, ValueError, KeyError, TypeError,
             IndexError, OSError)


def _guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except _FAILURES as e:
        return {"ok": False, "reason": str(e) or type(e).__name__}


def _root_schematic() -> str:
    """The open project's root schematic."""
    br = get_kicad_bridge()
    try:
        path = br.sch_file_path()
        if os.path.isfile(path):
            return path
    except KiCadNotReachableError:  # no board open: ask the schematic editor
        pass
    for sch in api.open_schematics(br.ipc()):
        folder = sch["project_path"]
        if folder.lower().endswith(".kicad_pro"):
            folder = os.path.dirname(folder)
        cand = os.path.join(folder, (sch["project"] or "") + ".kicad_sch")
        if os.path.isfile(cand):
            return cand
    raise KiCadNotReachableError("no KiCad project is open: open its board or schematic, "
                                 "or name the schematic file in sheet")


def _sheet_path(sheet: str) -> str:
    if sheet and os.path.isabs(sheet):
        if not os.path.isfile(sheet):
            raise ValueError(f"no schematic at {sheet}")
        return sheet
    root = _root_schematic()
    if not sheet:
        return root
    path = os.path.join(os.path.dirname(root), sheet)
    if not path.endswith(".kicad_sch"):
        path += ".kicad_sch"
    if not os.path.isfile(path):
        raise ValueError(f"no schematic {sheet} in the project folder")
    return path


def _root_for(path: str) -> str:
    name, folder = project_of(path)
    return os.path.join(folder, name + ".kicad_sch")


def _instance_path(path: str) -> str:
    paths = sheet_paths(_root_for(path))
    key = os.path.normcase(os.path.abspath(path))
    if key not in paths:
        raise ValueError(f"{os.path.basename(path)} is not a sheet of the project")
    return paths[key]


def _merge(items: Optional[list], shared: dict[str, Any]) -> list[dict[str, Any]]:
    shared = {k: v for k, v in shared.items() if v is not None}
    if items is None:
        return [shared]
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list of argument dicts")
    if not all(isinstance(i, dict) for i in items):
        raise ValueError("each item must be a dict of arguments")
    return [{**shared, **i} for i in items]


def _reply(results: list[dict[str, Any]], doc: Optional[SchDoc], wrote: bool) -> dict[str, Any]:
    failed = [i for i, r in enumerate(results) if not r.get("ok")]
    out: dict[str, Any] = {"ok": not failed, "count": len(results),
                           "succeeded": len(results) - len(failed), "failed": len(failed),
                           "items": results}
    if failed:
        out["failed_items"] = failed
    if doc is not None:
        out["file"] = doc.path
    if wrote:
        out["note"] = RELOAD_NOTE
    return out


def _each(merged, doc: SchDoc, make: Callable[[dict[str, Any]], dict[str, Any]]) -> dict:
    """Run ``make`` per item against the document, save once, check the
    file reads back with every new uuid in it."""
    results = []
    for a in merged:
        try:
            results.append({"ok": True, **make(a)})
        except (ValueError, KeyError, TypeError) as e:
            results.append({"ok": False, "reason": str(e)})
    wrote = any(r["ok"] for r in results)
    if wrote:
        doc.save()
        back = SchDoc(doc.path).text
        for r in results:
            if r.get("ok") and r.get("uuid") and r["uuid"] not in back:
                r.update(ok=False, reason="not found in the file after writing")
    return _reply(results, doc, wrote)


def _xy(a: dict[str, Any], prefix: str = "") -> tuple[float, float]:
    x, y = a.get(prefix + "x_mm"), a.get(prefix + "y_mm")
    if x is None or y is None:
        raise ValueError(f"needs {prefix}x_mm and {prefix}y_mm")
    return float(x), float(y)


# -- library symbols ------------------------------------------------------------------
def _cli() -> Optional[str]:
    try:
        return get_kicad_bridge().kicad_cli_path()
    except KiCadNotReachableError:  # libraries resolve from the tables alone
        return None


def _find_in_library(path: str, name: str) -> Optional[str]:
    text = read_file(path)
    root = forms(text)[0]
    for s in find_children(text, root, "symbol"):
        node = parse(text, s)
        if node[1] == name:
            block = text[s[0]:s[1]]
            parent = first(node, "extends")
            if parent:
                base = _find_in_library(path, parent[1])
                if base is None:
                    raise ValueError(f"{name} extends {parent[1]}, which is not in {path}")
                block = _flatten(base, parent[1], node, text[s[0]:s[1]], name)
            return block
    return None


def _flatten(base: str, base_name: str, derived: list, derived_text: str, name: str) -> str:
    """A derived symbol as a full one: the parent's body with the derived
    symbol's name and properties."""
    text = base.replace(f'(symbol "{base_name}_', f'(symbol "{name}_')
    text = text.replace(f'(symbol "{base_name}"', f'(symbol "{name}"', 1)
    span = forms(text)[0]
    own = {p[1] for p in all_of(derived, "property")}
    for s in reversed(find_children(text, span, "property")):
        if parse(text, s)[1] in own:
            text = text[:s[0]] + text[s[1]:]
    dspan = forms(derived_text)[0]
    props = [derived_text[s[0]:s[1]] for s in find_children(derived_text, dspan, "property")]
    span = forms(text)[0]
    first_child = children(text, span)[0]
    insert = "\n".join(props) + "\n"
    return text[:first_child[0]] + insert + text[first_child[0]:]


def _library_block(lib_id: str, project_dir: str) -> str:
    if ":" not in lib_id:
        raise ValueError("lib_id must be Library:Symbol, e.g. Device:R")
    nick, name = lib_id.split(":", 1)
    path = libtables.resolve("symbol", nick, project_dir, _cli())
    if not path or not os.path.isfile(path):
        raise ValueError(f"no symbol library {nick} in the library tables")
    block = _find_in_library(path, name)
    if block is None:
        raise ValueError(f"no symbol {name} in {nick}")
    return block.replace(f'(symbol "{name}"', f'(symbol {q(lib_id)}', 1)


def _ensure_lib_symbol(doc: SchDoc, lib_id: str) -> list:
    blocks = doc.lib_symbol_blocks()
    if lib_id not in blocks:
        doc.add_lib_symbol(_library_block(lib_id, os.path.dirname(doc.path)))
        blocks = doc.lib_symbol_blocks()
    return blocks[lib_id]


def _used_references(root_path: str) -> set[str]:
    refs = set()
    for path in sheet_paths(root_path):
        d = SchDoc(path)
        for _, node in d.symbols():
            for inst in _all_instance_refs(node):
                refs.add(inst)
            r = _prop(node, "Reference")
            if r:
                refs.add(r)
    return refs


def _all_instance_refs(node: list) -> list[str]:
    out = []
    inst = first(node, "instances")
    for project in all_of(inst or [], "project"):
        for p in all_of(project, "path"):
            ref = first(p, "reference")
            if ref:
                out.append(ref[1])
    return out


def _next_reference(prefix: str, used: set[str]) -> str:
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    ref = f"{prefix}{n}"
    used.add(ref)
    return ref


def _symbol_block(lib: list, lib_text: str, lib_id: str, ref: str, x: float, y: float,
                  angle: float, mirror: str, unit: int, fields: dict[str, Any],
                  flags: dict[str, bool], project: str, inst_path: str) -> tuple[str, str]:
    uid = new_uuid()
    fn = symbol_transform(x, y, angle, mirror)
    lines = [f"(symbol", f"\t(lib_id {q(lib_id)})", f"\t(at {num(x)} {num(y)} {num(angle)})"]
    if mirror in ("x", "y"):
        lines.append(f"\t(mirror {mirror})")
    lines += [f"\t(unit {int(unit)})", "\t(body_style 1)",
              f"\t(exclude_from_sim {'yes' if flags.get('exclude_from_sim') else 'no'})",
              f"\t(in_bom {'yes' if flags.get('in_bom', True) else 'no'})",
              f"\t(on_board {'yes' if flags.get('on_board', True) else 'no'})",
              f"\t(in_pos_files {'yes' if flags.get('on_board', True) else 'no'})",
              f"\t(dnp {'yes' if flags.get('dnp') else 'no'})", f"\t(uuid {q(uid)})"]
    span = forms(lib_text)[0]
    seen = set()
    for s in find_children(lib_text, span, "property"):
        node = parse(lib_text, s)
        name = node[1]
        seen.add(name)
        if name.startswith("ki_"):  # library-only search fields
            continue
        value = fields.get(name, node[2] if len(node) > 2 else "")
        if name == "Reference":
            value = ref
        px, py, pa = _at(node)
        sx, sy = fn(px, py)
        eff_spans = find_children(lib_text, s, "effects")
        eff = lib_text[eff_spans[0][0]:eff_spans[0][1]] if eff_spans else effects()
        eff = re.sub(r"\s+", " ", eff)
        hide = "\n\t\t(hide yes)" if first(node, "hide") and first(node, "hide")[1:2] == ["yes"] \
            else ""
        lines.append(f"\t(property {q(name)} {q(value)}\n\t\t(at {num(sx)} {num(sy)} {num(pa)})"
                     f"{hide}\n\t\t{eff}\n\t)")
    for name, value in fields.items():
        if name not in seen and value is not None:
            lines.append(f"\t(property {q(name)} {q(value)}\n\t\t(at {num(x)} {num(y)} 0)"
                         f"\n\t\t(hide yes)\n\t\t{effects()}\n\t)")
    for p in lib_pins(lib):
        if p["unit"] in (0, int(unit)) and p["body_style"] in (0, 1):
            lines.append(f"\t(pin {q(p['number'])}\n\t\t(uuid {q(new_uuid())})\n\t)")
    lines.append(f"\t(instances\n\t\t(project {q(project)}\n\t\t\t(path {q(inst_path)}\n"
                 f"\t\t\t\t(reference {q(ref)})\n\t\t\t\t(unit {int(unit)})\n\t\t\t)\n\t\t)\n\t)")
    lines.append(")")
    return "\n".join(lines), uid


def _locate_symbol(doc: SchDoc, a: dict[str, Any], inst_path: str):
    """The (span, node) of the symbol an item names by uuid, or by
    reference (and which_unit, for one unit of a multi-unit part)."""
    for span, node in doc.symbols():
        uid = (first(node, "uuid") or ["", ""])[1]
        if a.get("uuid") and uid == a["uuid"]:
            return span, node
        if a.get("reference") and not a.get("uuid"):
            ref = instance_reference(node, inst_path) or _prop(node, "Reference")
            unit = int((first(node, "unit") or ["", "1"])[1])
            if ref == a["reference"] and int(a.get("which_unit") or unit) == unit:
                return span, node
    raise ValueError(f"no symbol {a.get('uuid') or a.get('reference')} on this sheet")


def register_kicad_sch_tools(mcp) -> None:

    @mcp.tool()
    async def kicad_sch_get_items(sheet: str = "", kinds: Optional[list[str]] = None
                                  ) -> dict[str, Any]:
        """Read a schematic sheet from its file (default the root). kinds:
        symbols, wires, buses, labels, junctions, no_connects, texts,
        sheets, page; all by default. Symbols carry their fields, flags,
        position, angle, mirror, unit and each pin's number, name, type and
        sheet position (mm) for wiring. Lists the project's sheet files."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            want = set(kinds or ["symbols", "wires", "buses", "labels", "junctions",
                                 "no_connects", "texts", "sheets", "page"])
            inst = _instance_path(path)
            out: dict[str, Any] = {"ok": True, "file": path, "instance_path": inst,
                                   "sheet_files": [os.path.basename(p) for p in
                                                   sheet_paths(_root_for(path))]}
            if "symbols" in want:
                out["symbols"] = [doc.symbol_info(n, inst) for _, n in doc.symbols()]
            for tag, key in (("wire", "wires"), ("bus", "buses")):
                if key in want:
                    rows = []
                    for s in doc.top(tag):
                        n = parse(doc.text, s)
                        pts = [[float(p[1]), float(p[2])] for p in (first(n, "pts") or [])[1:]]
                        rows.append({"uuid": (first(n, "uuid") or ["", ""])[1], "points_mm": pts})
                    out[key] = rows
            if "labels" in want:
                rows = []
                for tag, kind in (("label", "local"), ("global_label", "global"),
                                  ("hierarchical_label", "hierarchical")):
                    for s in doc.top(tag):
                        n = parse(doc.text, s)
                        x, y, a = _at(n)
                        shape = first(n, "shape")
                        rows.append({"kind": kind, "text": n[1], "position_mm": [x, y],
                                     "angle_deg": a, "uuid": (first(n, "uuid") or ["", ""])[1],
                                     **({"shape": shape[1]} if shape else {})})
                out["labels"] = rows
            for tag, key in (("junction", "junctions"), ("no_connect", "no_connects")):
                if key in want:
                    out[key] = [{"uuid": (first(parse(doc.text, s), "uuid") or ["", ""])[1],
                                 "position_mm": list(_at(parse(doc.text, s))[:2])}
                                for s in doc.top(tag)]
            if "texts" in want:
                out["texts"] = [{"text": parse(doc.text, s)[1],
                                 "position_mm": list(_at(parse(doc.text, s))[:2]),
                                 "uuid": (first(parse(doc.text, s), "uuid") or ["", ""])[1]}
                                for s in doc.top("text")]
            if "sheets" in want:
                rows = []
                for s in doc.top("sheet"):
                    n = parse(doc.text, s)
                    x, y, _ = _at(n)
                    size = first(n, "size") or ["", "0", "0"]
                    rows.append({"uuid": (first(n, "uuid") or ["", ""])[1],
                                 "name": _prop(n, "Sheetname") or _prop(n, "Sheet name"),
                                 "file": _prop(n, "Sheetfile") or _prop(n, "Sheet file"),
                                 "position_mm": [x, y], "size_mm": [float(size[1]), float(size[2])],
                                 "pins": [{"name": p[1], "shape": p[2] if len(p) > 2 else "",
                                           "position_mm": list(_at(p)[:2])}
                                          for p in all_of(n, "pin")]})
                out["sheets"] = rows
            if "page" in want:
                tb = first(doc.tree, "title_block") or []
                out["page"] = {"paper": (first(doc.tree, "paper") or ["", ""])[1],
                               "title_block": {c[0]: c[-1] for c in tb[1:] if isinstance(c, list)}}
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_symbols(items: list[dict[str, Any]], lib_id: str = "",
                                      sheet: str = "") -> dict[str, Any]:
        """Place library symbols on a sheet (default the root). Each item:
        lib_id ("Device:R", from the symbol tables), x_mm, y_mm, and
        reference (default: next free for the prefix), value, angle_deg
        (0/90/180/270), mirror (x, y), unit, footprint, properties {name:
        value}, dnp, in_bom, on_board. Returns uuids, references and pin
        positions."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            project, _ = project_of(path)
            inst = _instance_path(path)
            used = _used_references(_root_for(path))
            merged = _merge(items, {"lib_id": lib_id or None, "angle_deg": 0, "mirror": "",
                                    "unit": 1})

            def make(a):
                if not a.get("lib_id"):
                    raise ValueError("lib_id is required, e.g. Device:R")
                x, y = _xy(a)
                if float(a.get("angle_deg", 0)) % 90:
                    raise ValueError("angle_deg must be 0, 90, 180 or 270")
                lib = _ensure_lib_symbol(doc, a["lib_id"])
                lib_text = doc.lib_symbol_text(a["lib_id"])
                prefix = (_prop(lib, "Reference") or "U").rstrip("?")
                ref = a.get("reference") or _next_reference(prefix, used)
                if a.get("reference"):
                    used.add(ref)
                fields = dict(a.get("properties") or {})
                if a.get("value") is not None:
                    fields["Value"] = a["value"]
                if a.get("footprint") is not None:
                    fields["Footprint"] = a["footprint"]
                block, uid = _symbol_block(lib, lib_text, a["lib_id"], ref, x, y,
                                           float(a.get("angle_deg", 0)), a.get("mirror", ""),
                                           int(a.get("unit", 1)), fields,
                                           {k: a.get(k) for k in ("dnp", "in_bom", "on_board")},
                                           project, inst)
                doc.append(block)
                placed = doc.symbol_info(parse(doc.text, doc.find_uuid(uid)), inst)
                return {"uuid": uid, "reference": ref, "value": placed["value"],
                        "pins": placed.get("pins", [])}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_power(items: list[dict[str, Any]],
                                    sheet: str = "") -> dict[str, Any]:
        """Place power ports. Each item: name (the net: GND, +3V3), x_mm,
        y_mm (its pin), angle_deg, lib_id (default "power:<name>").
        References are numbered #PWR."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            project, _ = project_of(path)
            inst = _instance_path(path)
            used = _used_references(_root_for(path))
            merged = _merge(items, {})

            def make(a):
                if not a.get("name"):
                    raise ValueError("a power port needs name, e.g. GND")
                lid = a.get("lib_id") or "power:" + a["name"]
                x, y = _xy(a)
                lib = _ensure_lib_symbol(doc, lid)
                if first(lib, "power") is None:
                    raise ValueError(f"{lid} is not a power symbol")
                ref = _next_reference("#PWR", used)
                block, uid = _symbol_block(lib, doc.lib_symbol_text(lid), lid, ref, x, y,
                                           float(a.get("angle_deg", 0)), "", 1,
                                           {"Value": a["name"]},
                                           {"in_bom": False, "on_board": False}, project, inst)
                doc.append(block)
                return {"uuid": uid, "reference": ref, "net": a["name"]}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_wires(items: list[dict[str, Any]], junctions: bool = True,
                                    sheet: str = "") -> dict[str, Any]:
        """Draw wires. Each item: points [[x_mm, y_mm], ...] (a polyline),
        bus (true for a bus). With junctions, one is added where a wire end
        lands mid-wire."""
        def body():
            doc = SchDoc(_sheet_path(sheet))
            merged = _merge(items, {})
            existing = []
            for s in doc.top("wire"):
                pts = (first(parse(doc.text, s), "pts") or [])[1:]
                if len(pts) == 2:
                    existing.append(((float(pts[0][1]), float(pts[0][2])),
                                     (float(pts[1][1]), float(pts[1][2]))))
            have = {tuple(_at(parse(doc.text, s))[:2]) for s in doc.top("junction")}

            def make(a):
                pts = a.get("points") or []
                if len(pts) < 2:
                    raise ValueError("a wire needs at least two points")
                pts = [(float(p[0]), float(p[1])) for p in pts]
                uids, added = [], []
                for k in range(len(pts) - 1):
                    block = wire_block(pts[k], pts[k + 1], bool(a.get("bus")))
                    uids.append(re.search(r'\(uuid "([^"]+)"\)', block).group(1))
                    doc.append(block)
                if junctions and not a.get("bus"):
                    for end in (pts[0], pts[-1]):
                        if end not in have and any(_mid_of(end, w) for w in existing):
                            doc.append(junction_block(*end))
                            have.add(end)
                            added.append(list(end))
                    existing.extend((pts[k], pts[k + 1]) for k in range(len(pts) - 1))
                return {"uuid": uids[0], "uuids": uids, "junctions_added": added}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_labels(items: list[dict[str, Any]], kind: str = "local",
                                     sheet: str = "") -> dict[str, Any]:
        """Place net labels. Each item: text (the net), x_mm, y_mm (on a
        wire end or pin), angle_deg, kind (local, global, hierarchical),
        shape (input, output, bidirectional, tri_state, passive), size_mm."""
        def body():
            doc = SchDoc(_sheet_path(sheet))
            merged = _merge(items, {"angle_deg": 0, "kind": kind, "shape": "bidirectional",
                                    "size_mm": 1.27})

            def make(a):
                if not a.get("text"):
                    raise ValueError("a label needs text")
                if a["kind"] not in ("local", "global", "hierarchical"):
                    raise ValueError("kind must be local, global or hierarchical")
                if a["shape"] not in ("input", "output", "bidirectional", "tri_state", "passive"):
                    raise ValueError("shape must be input, output, bidirectional, tri_state "
                                     "or passive")
                x, y = _xy(a)
                block = label_block(a["kind"], a["text"], x, y, float(a["angle_deg"]),
                                    a["shape"], float(a["size_mm"]))
                doc.append(block)
                return {"uuid": re.search(r'\(uuid "([^"]+)"\)', block).group(1),
                        "text": a["text"], "kind": a["kind"]}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_markers(items: list[dict[str, Any]], kind: str = "junction",
                                      sheet: str = "") -> dict[str, Any]:
        """Place junctions, no-connect flags or bus entries. Each item: x_mm,
        y_mm, kind (junction, no_connect, bus_entry); a bus entry takes
        size_mm [dx, dy] (default [2.54, 2.54])."""
        def body():
            doc = SchDoc(_sheet_path(sheet))
            merged = _merge(items, {"kind": kind})

            def make(a):
                x, y = _xy(a)
                if a["kind"] == "junction":
                    block = junction_block(x, y)
                elif a["kind"] == "no_connect":
                    block = no_connect_block(x, y)
                elif a["kind"] == "bus_entry":
                    dx, dy = a.get("size_mm") or [2.54, 2.54]
                    block = (f"(bus_entry\n\t(at {num(x)} {num(y)})\n\t(size {num(dx)} {num(dy)})"
                             f"\n\t(stroke\n\t\t(width 0)\n\t\t(type default)\n\t)\n"
                             f"\t(uuid {q(new_uuid())})\n)")
                else:
                    raise ValueError("kind must be junction, no_connect or bus_entry")
                doc.append(block)
                return {"uuid": re.search(r'\(uuid "([^"]+)"\)', block).group(1),
                        "kind": a["kind"]}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_graphics(items: list[dict[str, Any]],
                                       sheet: str = "") -> dict[str, Any]:
        """Place notes and drawing. Each item: shape text (text, x_mm,
        y_mm, size_mm, angle_deg), text_box (text, points: two corners),
        line (points), rectangle (points: two corners) or circle (x_mm,
        y_mm, radius in size_mm)."""
        def body():
            doc = SchDoc(_sheet_path(sheet))
            merged = _merge(items, {"shape": "text", "size_mm": 1.27, "angle_deg": 0})
            stroke = "\t(stroke\n\t\t(width 0)\n\t\t(type default)\n\t)\n\t(fill\n\t\t(type none)\n\t)"

            def make(a):
                kind = a["shape"]
                uid = new_uuid()
                pts = [(float(p[0]), float(p[1])) for p in a.get("points") or []]
                if kind == "text":
                    if not a.get("text"):
                        raise ValueError("text is empty")
                    x, y = _xy(a)
                    block = text_block(a["text"], x, y, float(a["angle_deg"]), float(a["size_mm"]))
                    uid = re.search(r'\(uuid "([^"]+)"\)', block).group(1)
                elif kind == "text_box":
                    if len(pts) != 2 or not a.get("text"):
                        raise ValueError("a text box needs text and two corner points")
                    (x1, y1), (x2, y2) = pts
                    block = (f"(text_box {q(a['text'])}\n\t(exclude_from_sim no)\n"
                             f"\t(at {num(min(x1, x2))} {num(min(y1, y2))} 0)\n"
                             f"\t(size {num(abs(x2 - x1))} {num(abs(y2 - y1))})\n"
                             f"\t(margins 0.9525 0.9525 0.9525 0.9525)\n{stroke}\n"
                             f"\t{effects(float(a['size_mm']), 'left top')}\n\t(uuid {q(uid)})\n)")
                elif kind == "line":
                    if len(pts) < 2:
                        raise ValueError("a line needs at least two points")
                    xy = " ".join(f"(xy {num(x)} {num(y)})" for x, y in pts)
                    block = (f"(polyline\n\t(pts\n\t\t{xy}\n\t)\n\t(stroke\n\t\t(width 0)\n"
                             f"\t\t(type default)\n\t)\n\t(uuid {q(uid)})\n)")
                elif kind == "rectangle":
                    if len(pts) != 2:
                        raise ValueError("a rectangle needs two corner points")
                    block = (f"(rectangle\n\t(start {num(pts[0][0])} {num(pts[0][1])})\n"
                             f"\t(end {num(pts[1][0])} {num(pts[1][1])})\n{stroke}\n"
                             f"\t(uuid {q(uid)})\n)")
                elif kind == "circle":
                    x, y = _xy(a)
                    block = (f"(circle\n\t(center {num(x)} {num(y)})\n"
                             f"\t(radius {num(a['size_mm'])})\n{stroke}\n\t(uuid {q(uid)})\n)")
                else:
                    raise ValueError("shape must be text, text_box, line, rectangle or circle")
                doc.append(block)
                return {"uuid": uid, "shape": kind}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_place_sheet(name: str, file: str, x_mm: float, y_mm: float,
                                    width_mm: float = 25.4, height_mm: float = 25.4,
                                    pins: Optional[list[dict[str, Any]]] = None,
                                    sheet: str = "") -> dict[str, Any]:
        """Add a hierarchical sheet: name, file (a .kicad_sch in the project
        folder, made empty when missing), position and size (mm), and pins
        [{name, shape (input, output, bidirectional, tri_state, passive),
        side (left, right), offset_mm (from the top)}]. Pins on the sheet
        symbol pair with hierarchical labels of the same name inside."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            project, folder = project_of(path)
            if not file.endswith(".kicad_sch") or os.path.dirname(file):
                raise ValueError("file must be a plain name ending .kicad_sch")
            child = os.path.join(folder, file)
            created = False
            if not os.path.exists(child):
                write_file(child, f"(kicad_sch\n\t(version 20250114)\n\t(generator \"eeschema\")\n"
                                  f"\t(generator_version \"10.0\")\n\t(uuid {q(new_uuid())})\n"
                                  f"\t(paper \"A4\")\n\t(lib_symbols\n\t)\n"
                                  f"\t(embedded_fonts no)\n)\n")
                created = True
            uid = new_uuid()
            x, y, w, h = float(x_mm), float(y_mm), float(width_mm), float(height_mm)
            pages = [int(p) for p in re.findall(r'\(page "(\d+)"\)',
                                                SchDoc(_root_for(path)).text)] or [1]
            lines = [f"(sheet", f"\t(at {num(x)} {num(y)})", f"\t(size {num(w)} {num(h)})",
                     "\t(exclude_from_sim no)", "\t(in_bom yes)", "\t(on_board yes)",
                     "\t(dnp no)", "\t(fields_autoplaced yes)",
                     "\t(stroke\n\t\t(width 0.1524)\n\t\t(type solid)\n\t)",
                     "\t(fill\n\t\t(color 0 0 0 0.0000)\n\t)", f"\t(uuid {q(uid)})",
                     f"\t(property \"Sheetname\" {q(name)}\n\t\t(at {num(x)} {num(y - 0.7)} 0)\n"
                     f"\t\t{effects(1.27, 'left bottom')}\n\t)",
                     f"\t(property \"Sheetfile\" {q(file)}\n\t\t(at {num(x)} {num(y + h + 0.6)} 0)\n"
                     f"\t\t{effects(1.27, 'left top')}\n\t)"]
            for p in pins or []:
                side = p.get("side", "left")
                px = x if side == "left" else x + w
                py = y + float(p.get("offset_mm", 2.54))
                lines.append(f"\t(pin {q(p['name'])} {p.get('shape', 'bidirectional')}\n"
                             f"\t\t(at {num(px)} {num(py)} {180 if side == 'left' else 0})\n"
                             f"\t\t(uuid {q(new_uuid())})\n"
                             f"\t\t{effects(1.27, 'left' if side == 'left' else 'right')}\n\t)")
            inst = _instance_path(path)
            lines.append(f"\t(instances\n\t\t(project {q(project)}\n\t\t\t(path {q(inst)}\n"
                         f"\t\t\t\t(page {q(str(max(pages) + 1))})\n\t\t\t)\n\t\t)\n\t)")
            lines.append(")")
            doc.append("\n".join(lines))
            doc.save()
            ok = uid in SchDoc(path).text
            return _reply([{"ok": ok, "uuid": uid, "name": name, "file": child,
                            "file_created": created,
                            **({} if ok else {"reason": "not in the file after writing"})}],
                          doc, True)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_modify_symbols(items: list[dict[str, Any]],
                                       sheet: str = "") -> dict[str, Any]:
        """Change placed symbols, each named by reference (which_unit picks
        one unit of a multi-unit part) or uuid, with the changes beside it:
        new_reference, value, footprint, datasheet,
        properties {name: value, or null to remove}, x_mm, y_mm (moves
        fields with it), angle_deg, mirror (x, y, or ""), unit, dnp, in_bom,
        on_board, exclude_from_sim. Wires are not moved."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            inst = _instance_path(path)
            merged = _merge(items, {})

            def make(a):
                span, node = _locate_symbol(doc, a, inst)
                text = doc.text[span[0]:span[1]]
                text = _modify_symbol_text(text, node, a, inst, doc)
                doc.replace(span, text)
                uid = (first(node, "uuid") or ["", ""])[1]
                info = doc.symbol_info(parse(doc.text, doc.find_uuid(uid)), inst)
                want = {"reference": a.get("new_reference"), "value": a.get("value"),
                        "angle_deg": a.get("angle_deg"), "mirror": a.get("mirror"),
                        "unit": a.get("unit"), "dnp": a.get("dnp")}
                off = [k for k, v in want.items() if v is not None and info[k] != v and
                       not (isinstance(v, (int, float)) and float(info[k]) == float(v))]
                for i, key in enumerate(("x_mm", "y_mm")):
                    if a.get(key) is not None and abs(info["position_mm"][i] - float(a[key])) > 1e-6:
                        off.append(key)
                if off:
                    raise ValueError(f"the change did not take for {off}")
                return {"uuid": uid, "reference": info["reference"], "value": info["value"],
                        "position_mm": info["position_mm"], "angle_deg": info["angle_deg"],
                        "mirror": info["mirror"], "unit": info["unit"], "dnp": info["dnp"]}
            return _each(merged, doc, make)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_delete_items(uuids: Optional[list[str]] = None,
                                     references: Optional[list[str]] = None,
                                     sheet: str = "") -> dict[str, Any]:
        """Delete schematic items by uuid (any item: symbol, wire, label,
        junction, sheet...) or symbols by reference, from one sheet file."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            inst = _instance_path(path)
            results = []
            for uid in uuids or []:
                span = doc.find_uuid(uid)
                if span is None:
                    results.append({"uuid": uid, "ok": False, "reason": "not on this sheet"})
                    continue
                kind = head(doc.text, span)
                doc.remove(span)
                results.append({"uuid": uid, "ok": True, "kind": kind})
            for ref in references or []:
                found = [s for s, n in doc.symbols()
                         if (instance_reference(n, inst) or _prop(n, "Reference")) == ref]
                if not found:
                    results.append({"reference": ref, "ok": False, "reason": "not on this sheet"})
                    continue
                for s in reversed(found):
                    doc.remove(s)
                results.append({"reference": ref, "ok": True, "units_removed": len(found)})
            if not results:
                return {"ok": False, "reason": "give uuids or references"}
            wrote = any(r["ok"] for r in results)
            if wrote:
                doc.save()
                back = SchDoc(path)
                for r in results:
                    if r["ok"] and r.get("uuid") and back.find_uuid(r["uuid"]) is not None:
                        r.update(ok=False, reason="still in the file")
            return _reply(results, doc, wrote)
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_annotate(reset: bool = False, order: str = "x",
                                 dry_run: bool = False, sheet: str = "") -> dict[str, Any]:
        """Number symbols across every sheet of the project (sheet: any of
        its files, default the open one). References ending "?" get the
        lowest free number for their prefix, ordered by position (order x:
        rows; y: columns). reset renumbers all but power ports. Each
        unannotated unit gets its own number."""
        def body():
            if order not in ("x", "y"):
                raise ValueError("order must be x or y")
            root = _root_for(_sheet_path(sheet))
            paths = sheet_paths(root)
            docs = {p: SchDoc(p) for p in paths}
            entries = []
            for p, doc in docs.items():
                inst = paths[p]
                for span, node in doc.symbols():
                    ref = instance_reference(node, inst) or _prop(node, "Reference") or ""
                    if ref.startswith("#"):
                        continue
                    x, y, _ = _at(node)
                    entries.append({"file": p, "uuid": (first(node, "uuid") or ["", ""])[1],
                                    "ref": ref, "x": x, "y": y, "inst": inst})
            todo = [e for e in entries if reset or e["ref"].endswith("?")]
            keep = {e["ref"] for e in entries if e not in todo}
            key = (lambda e: (e["file"], e["y"], e["x"])) if order == "x" else \
                (lambda e: (e["file"], e["x"], e["y"]))
            changes = []
            for e in sorted(todo, key=key):
                prefix = re.sub(r"[0-9?]+$", "", e["ref"]) or "U"
                new = _next_reference(prefix, keep)
                if new != e["ref"]:
                    changes.append({**e, "new": new})
            if dry_run or not changes:
                return {"ok": True, "changed": 0 if dry_run else len(changes),
                        "renames": [{"from": c["ref"], "to": c["new"], "file":
                                     os.path.basename(c["file"])} for c in changes]}
            for c in changes:
                doc = docs[c["file"]]
                span = doc.find_uuid(c["uuid"])
                node = parse(doc.text, span)
                doc.replace(span, _modify_symbol_text(doc.text[span[0]:span[1]], node,
                                                      {"new_reference": c["new"]}, c["inst"], doc))
            for p in {c["file"] for c in changes}:
                docs[p].save()
            return {"ok": True, "changed": len(changes),
                    "renames": [{"from": c["ref"], "to": c["new"],
                                 "file": os.path.basename(c["file"])} for c in changes],
                    "note": RELOAD_NOTE}
        return _guard(body)

    @mcp.tool()
    async def kicad_sch_set_page(paper: str = "", title: Optional[str] = None,
                                 date: Optional[str] = None, revision: Optional[str] = None,
                                 company: Optional[str] = None,
                                 comments: Optional[dict[str, str]] = None,
                                 sheet: str = "") -> dict[str, Any]:
        """Set a sheet's paper size (A4, A3, A2, A1, A0, A, B, C, D, E,
        USLetter, USLegal) and title block: title, date, revision, company,
        comments {"1": "..."} for comments 1 to 9. Fields not given are
        kept."""
        def body():
            path = _sheet_path(sheet)
            doc = SchDoc(path)
            if paper:
                sizes = {"A5", "A4", "A3", "A2", "A1", "A0", "A", "B", "C", "D", "E",
                         "USLetter", "USLegal", "USLedger"}
                if paper not in sizes:
                    raise ValueError(f"paper must be one of {sorted(sizes)}")
                spans = doc.top("paper")
                if spans:
                    doc.replace(spans[0], f"(paper {q(paper)})")
                else:
                    doc.append(f"(paper {q(paper)})")
            tb = first(doc.tree, "title_block") or ["title_block"]
            fields = {c[0] if c[0] != "comment" else f"comment{c[1]}": c[-1]
                      for c in tb[1:] if isinstance(c, list)}
            for k, v in (("title", title), ("date", date), ("rev", revision),
                         ("company", company)):
                if v is not None:
                    fields[k] = v
            for k, v in (comments or {}).items():
                fields["comment" + str(k).replace("comment", "")] = v
            lines = ["(title_block"]
            for k in ("title", "date", "rev", "company"):
                if fields.get(k):
                    lines.append(f"\t({k} {q(fields[k])})")
            for n in range(1, 10):
                if fields.get(f"comment{n}"):
                    lines.append(f"\t(comment {n} {q(fields[f'comment{n}'])})")
            lines.append(")")
            spans = doc.top("title_block")
            if spans:
                doc.replace(spans[0], "\n".join(lines))
            else:
                paper_span = doc.top("paper")[0]
                end = doc.text.find("\n", paper_span[1]) + 1
                body_text = "".join("\t" + l + "\n" for l in lines)
                doc.changed(doc.text[:end] + body_text + doc.text[end:])
            doc.save()
            back = SchDoc(path)
            tb = first(back.tree, "title_block") or []
            return {"ok": True, "file": path, "paper": (first(back.tree, "paper") or ["", ""])[1],
                    "title_block": {c[0] if c[0] != "comment" else f"comment{c[1]}": c[-1]
                                    for c in tb[1:] if isinstance(c, list)},
                    "note": RELOAD_NOTE}
        return _guard(body)


def _mid_of(p, wire) -> bool:
    (ax, ay), (bx, by) = wire
    if p in (wire[0], wire[1]):
        return False
    if abs((bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)) > 1e-6:
        return False
    return min(ax, bx) - 1e-6 <= p[0] <= max(ax, bx) + 1e-6 and \
        min(ay, by) - 1e-6 <= p[1] <= max(ay, by) + 1e-6


def _set_flag(text: str, tag: str, value: bool) -> str:
    new = f"({tag} {'yes' if value else 'no'})"
    if re.search(rf"\n\t*\({tag} (yes|no)\)", text):
        return re.sub(rf"\({tag} (yes|no)\)", new, text, count=1)
    return text.replace("\t(uuid ", f"\t{new}\n\t(uuid ", 1)


def _modify_symbol_text(text: str, node: list, a: dict[str, Any], inst: str,
                        doc: SchDoc) -> str:
    """One symbol's text with the item's changes applied."""
    x0, y0, a0 = _at(node)
    text = _reindent(text, 0)
    if a.get("new_reference"):
        new = str(a["new_reference"])
        text = re.sub(r'(\(property "Reference" )"[^"]*"', lambda m: m.group(1) + q(new), text,
                      count=1)
        text = re.sub(r'(\(path ' + re.escape(q(inst)) + r'\s*\(reference )"[^"]*"',
                      lambda m: m.group(1) + q(new), text, count=1)
    props = dict(a.get("properties") or {})
    for key, name in (("value", "Value"), ("footprint", "Footprint"), ("datasheet", "Datasheet")):
        if a.get(key) is not None:
            props[name] = a[key]
    for name, value in props.items():
        span = forms(text)[0]
        found = None
        for s in find_children(text, span, "property"):
            if parse(text, s)[1] == name:
                found = s
        if value is None:
            if found is None:
                continue
            if name in ("Reference", "Value", "Footprint", "Datasheet"):
                raise ValueError(f"{name} cannot be removed")
            start = text.rfind("\n", 0, found[0]) + 1
            end = found[1] + (1 if text[found[1]:found[1] + 1] == "\n" else 0)
            text = text[:start] + text[end:]
        elif found is not None:
            old = parse(text, found)
            seg = text[found[0]:found[1]]
            seg = seg.replace(f"(property {q(name)} {q(old[2])}", f"(property {q(name)} {q(value)}", 1)
            text = text[:found[0]] + seg + text[found[1]:]
        else:
            block = (f"(property {q(name)} {q(value)}\n\t\t(at {num(x0)} {num(y0)} 0)\n"
                     f"\t\t(hide yes)\n\t\t{effects()}\n\t)")
            last = find_children(text, forms(text)[0], "property")[-1]
            text = text[:last[1]] + "\n\t" + block + text[last[1]:]
    for flag in ("dnp", "in_bom", "on_board", "exclude_from_sim"):
        if a.get(flag) is not None:
            text = _set_flag(text, flag, bool(a[flag]))
    if a.get("unit") is not None:
        text = re.sub(r"\(unit \d+\)", f"(unit {int(a['unit'])})", text)
    moved = a.get("x_mm") is not None or a.get("y_mm") is not None
    turned = a.get("angle_deg") is not None or a.get("mirror") is not None
    if moved or turned:
        x = float(a["x_mm"]) if a.get("x_mm") is not None else x0
        y = float(a["y_mm"]) if a.get("y_mm") is not None else y0
        ang = float(a["angle_deg"]) if a.get("angle_deg") is not None else a0
        if ang % 90:
            raise ValueError("angle_deg must be 0, 90, 180 or 270")
        dx, dy = x - x0, y - y0
        lines = text.split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"^(\t*)\(at (-?[\d.]+) (-?[\d.]+)( -?[\d.]+)?\)$", line)
            if not m:
                continue
            if len(m.group(1)) == 1:
                lines[i] = f"{m.group(1)}(at {num(x)} {num(y)} {num(ang)})"
            else:
                lines[i] = (f"{m.group(1)}(at {num(float(m.group(2)) + dx)} "
                            f"{num(float(m.group(3)) + dy)}{m.group(4) or ''})")
        text = "\n".join(lines)
        if a.get("mirror") is not None:
            text = re.sub(r"\n\t\(mirror [xy]\)", "", text)
            if a["mirror"] in ("x", "y"):
                text = re.sub(r"(\n\t\(at [^\n]*\))", rf"\1\n\t(mirror {a['mirror']})", text,
                              count=1)
            elif a["mirror"]:
                raise ValueError("mirror must be x, y or empty")
    del doc
    return text
