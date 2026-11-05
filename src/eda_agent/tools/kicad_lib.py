# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library tools for KiCad: symbol (.kicad_sym) and footprint (.pretty)
libraries, read and edited as files.

A library is named by its nickname in the project's or the global
library table, or by a path. Symbol coordinates are KiCad's library
coordinates (mm, Y up); footprint coordinates are mm from the footprint
origin, Y down. Edits keep every other line of the file as it was. The
open symbol and footprint editors do not reload a file written here.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from ..bridge.kicad_bridge import KiCadApiError, KiCadNotReachableError, get_kicad_bridge
from ..core import kicad_libtables as libtables
from ..core.kicad_sch_doc import _at, _reindent, lib_pins
from ..core.kicad_sexpr_edit import (all_of, cut, find_children, first, forms,
                                     insert_before_close, new_uuid, num, parse, q,
                                     read_file, write_file)

RELOAD_NOTE = ("Written to the library file. An open symbol or footprint editor does not "
               "reload it; reopen the library there to see it.")

_FAILURES = (KiCadNotReachableError, KiCadApiError, ValueError, KeyError, TypeError,
             IndexError, OSError)

_PIN_TYPES = ("input", "output", "bidirectional", "tri_state", "passive", "free",
              "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
              "no_connect")


def _guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except _FAILURES as e:
        return {"ok": False, "reason": str(e) or type(e).__name__}


def _project_dir() -> str:
    br = get_kicad_bridge()
    try:
        return os.path.dirname(br.board_file_path())
    except KiCadNotReachableError:  # no board: the libraries resolve from the global table
        return ""


def _cli() -> Optional[str]:
    try:
        return get_kicad_bridge().kicad_cli_path()
    except KiCadNotReachableError:  # the tables still resolve, without the bundled paths
        return None


def _library(kind: str, library: str) -> str:
    """A library's file (symbols) or folder (footprints) from a nickname or path."""
    if not library:
        raise ValueError("name the library by nickname or path")
    if os.path.exists(library):
        return library
    path = libtables.resolve(kind, library, _project_dir(), _cli())
    if not path or not os.path.exists(path):
        raise ValueError(f"no {kind} library {library} in the library tables or on disk")
    return path


def _symbol_span(text: str, name: str):
    root = forms(text)[0]
    for s in find_children(text, root, "symbol"):
        if parse(text, s)[1] == name:
            return s
    return None


def _footprint_file(library: str, name: str) -> str:
    folder = _library("footprint", library)
    if folder.endswith(".kicad_mod"):
        return folder
    if os.path.sep in name or "/" in name or ".." in name:
        raise ValueError("a footprint name is a plain name")
    return os.path.join(folder, name + ".kicad_mod")


def _summary(results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    failed = [i for i, r in enumerate(results) if not r.get("ok")]
    out = {"ok": not failed, "count": len(results), "succeeded": len(results) - len(failed),
           "failed": len(failed), "items": results}
    if failed:
        out["failed_items"] = failed
    out.update(extra)
    return out


def _props(node: list) -> dict[str, str]:
    return {p[1]: p[2] for p in all_of(node, "property") if len(p) > 2}


def _symbol_info(node: list) -> dict[str, Any]:
    pins = lib_pins(node)
    units = sorted({p["unit"] for p in pins if p["unit"]}) or [1]
    return {"name": node[1], "properties": _props(node),
            "extends": (first(node, "extends") or ["", ""])[1],
            "power": first(node, "power") is not None, "units": units,
            "pins": [{"number": p["number"], "name": p["name"], "type": p["type"],
                      "unit": p["unit"], "position_mm": list(p["at"]), "angle_deg": p["angle"],
                      "length_mm": p["length"]} for p in pins]}


def _footprint_info(node: list) -> dict[str, Any]:
    pads = []
    for p in all_of(node, "pad"):
        x, y, a = _at(p)
        size = first(p, "size") or ["", "0", "0"]
        drill = first(p, "drill")
        layers = first(p, "layers") or ["layers"]
        pads.append({"number": p[1], "type": p[2], "shape": p[3], "position_mm": [x, y],
                     "angle_deg": a, "size_mm": [float(size[1]), float(size[2])],
                     "drill_mm": [float(v) for v in drill[1:] if _is_num(v)] if drill else None,
                     "layers": layers[1:]})
    graphics: dict[str, int] = {}
    for tag in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_text"):
        for g in all_of(node, tag):
            layer = (first(g, "layer") or ["", "?"])[1]
            graphics[f"{tag[3:]}@{layer}"] = graphics.get(f"{tag[3:]}@{layer}", 0) + 1
    attr = first(node, "attr")
    return {"name": node[1], "description": (first(node, "descr") or ["", ""])[1],
            "tags": (first(node, "tags") or ["", ""])[1], "properties": _props(node),
            "attributes": attr[1:] if attr else [], "pads": pads, "graphics": graphics,
            "models": [m[1] for m in all_of(node, "model")]}


def _is_num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _edit_symbol(library: str, name: str, fn: Callable[[str], str]) -> tuple[str, str]:
    path = _library("symbol", library)
    text = read_file(path)
    span = _symbol_span(text, name)
    if span is None:
        raise ValueError(f"no symbol {name} in {os.path.basename(path)}")
    block = _reindent(text[span[0]:span[1]], 0)
    new = fn(block)
    start = text.rfind("\n", 0, span[0]) + 1
    indent = text[start:span[0]]
    text = text[:span[0]] + new.replace("\n", "\n" + indent) + text[span[1]:]
    write_file(path, text)
    return path, new


def _edit_footprint(library: str, name: str, fn: Callable[[str], str]) -> str:
    path = _footprint_file(library, name)
    if not os.path.isfile(path):
        raise ValueError(f"no footprint {name} at {path}")
    text = read_file(path)
    write_file(path, fn(text))
    return path


def _pin_block(p: dict[str, Any]) -> str:
    ptype = p.get("type", "passive")
    if ptype not in _PIN_TYPES:
        raise ValueError(f"pin type must be one of {list(_PIN_TYPES)}")
    if not p.get("number"):
        raise ValueError("a pin needs a number")
    hidden = "\n\t(hide yes)" if p.get("hidden") else ""
    return (f"(pin {ptype} {p.get('shape', 'line')}\n"
            f"\t(at {num(p.get('x_mm', 0))} {num(p.get('y_mm', 0))} {num(p.get('angle_deg', 0))})"
            f"\n\t(length {num(p.get('length_mm', 2.54))}){hidden}\n"
            f"\t(name {q(p.get('name', '~'))}\n\t\t(effects (font (size 1.27 1.27)))\n\t)\n"
            f"\t(number {q(p['number'])}\n\t\t(effects (font (size 1.27 1.27)))\n\t)\n)")


def _unit_span(block: str, name: str, unit: int):
    """The sub-symbol holding a unit's drawing and pins (made when missing)."""
    root = forms(block)[0]
    want = f"{name}_{int(unit)}_1"
    for s in find_children(block, root, "symbol"):
        if parse(block, s)[1] == want:
            return block, s
    block = insert_before_close(block, root, f"(symbol {q(want)}\n)")
    return _unit_span(block, name, unit)


def _graphic_block(g: dict[str, Any]) -> str:
    stroke = f"(stroke (width {num(g.get('width_mm', 0.254))}) (type default))"
    fill = f"(fill (type {g.get('fill', 'none')}))"
    shape = g.get("shape")
    pts = [(float(p[0]), float(p[1])) for p in g.get("points") or []]
    if shape == "rectangle":
        if len(pts) != 2:
            raise ValueError("a rectangle needs two corner points")
        return (f"(rectangle (start {num(pts[0][0])} {num(pts[0][1])}) "
                f"(end {num(pts[1][0])} {num(pts[1][1])}) {stroke} {fill})")
    if shape == "polyline":
        if len(pts) < 2:
            raise ValueError("a polyline needs two or more points")
        xy = " ".join(f"(xy {num(x)} {num(y)})" for x, y in pts)
        return f"(polyline (pts {xy}) {stroke} {fill})"
    if shape == "circle":
        c = g.get("center_mm") or [0, 0]
        return (f"(circle (center {num(c[0])} {num(c[1])}) (radius {num(g.get('radius_mm', 1))})"
                f" {stroke} {fill})")
    if shape == "arc":
        if len(pts) != 3:
            raise ValueError("an arc needs three points: start, mid, end")
        return (f"(arc (start {num(pts[0][0])} {num(pts[0][1])}) (mid {num(pts[1][0])} "
                f"{num(pts[1][1])}) (end {num(pts[2][0])} {num(pts[2][1])}) {stroke} {fill})")
    if shape == "text":
        at = g.get("position_mm") or [0, 0]
        return (f"(text {q(g.get('text', ''))} (at {num(at[0])} {num(at[1])} "
                f"{num(g.get('angle_deg', 0))}) (effects (font (size "
                f"{num(g.get('size_mm', 1.27))} {num(g.get('size_mm', 1.27))}))))")
    raise ValueError("shape must be rectangle, polyline, circle, arc or text")


def _fp_item_block(it: dict[str, Any]) -> str:
    kind = it.get("kind")
    layer = it.get("layer", "F.SilkS")
    stroke = f"(stroke (width {num(it.get('width_mm', 0.12))}) (type solid))"
    uid = f"(uuid {q(new_uuid())})"
    pts = [(float(p[0]), float(p[1])) for p in it.get("points") or []]
    if kind == "pad":
        if not it.get("number") and it.get("type", "smd") != "np_thru_hole":
            raise ValueError("a pad needs a number")
        ptype = {"smd": "smd", "pth": "thru_hole", "thru_hole": "thru_hole",
                 "npth": "np_thru_hole", "np_thru_hole": "np_thru_hole",
                 "connect": "connect"}[it.get("type", "smd")]
        shape = it.get("shape", "roundrect")
        size = it.get("size_mm") or [1, 1]
        at = it.get("position_mm") or [0, 0]
        layers = it.get("layers") or (["F.Cu", "F.Paste", "F.Mask"] if ptype == "smd"
                                      else ["*.Cu", "*.Mask"])
        parts = [f"(pad {q(it.get('number', ''))} {ptype} {shape}",
                 f"\t(at {num(at[0])} {num(at[1])}{' ' + num(it['angle_deg']) if it.get('angle_deg') else ''})",
                 f"\t(size {num(size[0])} {num(size[1])})"]
        if ptype in ("thru_hole", "np_thru_hole"):
            parts.append(f"\t(drill {num(it.get('drill_mm', 0.8))})")
        parts.append("\t(layers " + " ".join(q(l) for l in layers) + ")")
        if shape == "roundrect":
            parts.append(f"\t(roundrect_rratio {num(it.get('roundrect_ratio', 0.25))})")
        parts += [f"\t{uid}", ")"]
        return "\n".join(parts)
    if kind == "line":
        if len(pts) != 2:
            raise ValueError("a line needs two points")
        return (f"(fp_line (start {num(pts[0][0])} {num(pts[0][1])}) (end {num(pts[1][0])} "
                f"{num(pts[1][1])}) {stroke} (layer {q(layer)}) {uid})")
    if kind == "rect":
        if len(pts) != 2:
            raise ValueError("a rect needs two corner points")
        fill = "yes" if it.get("filled") else "no"
        return (f"(fp_rect (start {num(pts[0][0])} {num(pts[0][1])}) (end {num(pts[1][0])} "
                f"{num(pts[1][1])}) {stroke} (fill {fill}) (layer {q(layer)}) {uid})")
    if kind == "circle":
        c = it.get("center_mm") or [0, 0]
        r = float(it.get("radius_mm", 1))
        return (f"(fp_circle (center {num(c[0])} {num(c[1])}) (end {num(c[0] + r)} {num(c[1])}) "
                f"{stroke} (fill {'yes' if it.get('filled') else 'no'}) (layer {q(layer)}) {uid})")
    if kind == "arc":
        if len(pts) != 3:
            raise ValueError("an arc needs three points: start, mid, end")
        return (f"(fp_arc (start {num(pts[0][0])} {num(pts[0][1])}) (mid {num(pts[1][0])} "
                f"{num(pts[1][1])}) (end {num(pts[2][0])} {num(pts[2][1])}) {stroke} "
                f"(layer {q(layer)}) {uid})")
    if kind == "polygon":
        if len(pts) < 3:
            raise ValueError("a polygon needs three or more points")
        xy = " ".join(f"(xy {num(x)} {num(y)})" for x, y in pts)
        return (f"(fp_poly (pts {xy}) {stroke} (fill {'no' if it.get('filled') is False else 'yes'})"
                f" (layer {q(layer)}) {uid})")
    if kind == "text":
        at = it.get("position_mm") or [0, 0]
        s = num(it.get("size_mm", 1))
        return (f"(fp_text user {q(it.get('text', ''))} (at {num(at[0])} {num(at[1])}) "
                f"(layer {q(layer)}) {uid} (effects (font (size {s} {s}) (thickness "
                f"{num(it.get('thickness_mm', 0.15))}))))")
    raise ValueError("kind must be pad, line, rect, circle, arc, polygon or text")


def register_kicad_lib_tools(mcp) -> None:

    @mcp.tool()
    async def kicad_lib_list_libraries(kind: str = "symbol") -> dict[str, Any]:
        """The symbol or footprint libraries (kind) the open project sees:
        nickname, path, scope (project or global), disabled. Project rows
        come first; KiCad takes a nickname from the project table first."""
        def body():
            if kind not in libtables.KINDS:
                raise ValueError("kind must be symbol or footprint")
            rows = libtables.libraries(kind, _project_dir(), _cli())
            return {"ok": True, "count": len(rows), "libraries": rows}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_set_table(action: str, nickname: str, kind: str = "symbol",
                                  path: str = "", scope: str = "project",
                                  description: str = "") -> dict[str, Any]:
        """Add (action "add", with path) or remove (action "remove") a
        library in the project's or the global (scope) symbol or footprint
        table. A path inside the project folder is stored as ${KIPRJMOD}.
        KiCad reads the tables when a library is next opened."""
        def body():
            if kind not in libtables.KINDS or scope not in ("project", "global"):
                raise ValueError("kind is symbol or footprint; scope is project or global")
            project = _project_dir()
            if action == "add":
                if not path or not os.path.exists(path):
                    raise ValueError(f"no library at {path}")
                uri = os.path.abspath(path).replace("\\", "/")
                if project and os.path.normcase(uri).startswith(
                        os.path.normcase(project.replace("\\", "/")) + "/"):
                    uri = "${KIPRJMOD}" + uri[len(project):]
                table = libtables.add_row(kind, scope, nickname, uri, project, description)
            elif action == "remove":
                table = libtables.remove_row(kind, scope, nickname, project)
            else:
                raise ValueError("action must be add or remove")
            rows = libtables.libraries(kind, project, _cli())
            present = any(r["nickname"] == nickname and r["scope"] == scope for r in rows)
            ok = present if action == "add" else not present
            return {"ok": ok, "table": table, "action": action, "nickname": nickname,
                    **({} if ok else {"reason": "the table does not show the change"})}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_list_items(library: str, kind: str = "symbol",
                                   filter: str = "") -> dict[str, Any]:
        """The symbols in a .kicad_sym library, or the footprints in a
        .pretty library (kind), by nickname or path: name, description,
        keywords, footprint (symbols), pad count, and pin count. filter keeps
        names containing it (case-insensitive)."""
        def body():
            path = _library(kind, library)
            rows = []
            if kind == "symbol":
                text = read_file(path)
                for s in find_children(text, forms(text)[0], "symbol"):
                    node = parse(text, s)
                    if filter and filter.lower() not in node[1].lower():
                        continue
                    props = _props(node)
                    rows.append({"name": node[1], "description": props.get("Description", ""),
                                 "keywords": props.get("ki_keywords", ""),
                                 "footprint": props.get("Footprint", ""),
                                 "extends": (first(node, "extends") or ["", ""])[1],
                                 "pin_count": len(lib_pins(node))})
            elif kind == "footprint":
                for f in sorted(os.listdir(path)):
                    if not f.endswith(".kicad_mod"):
                        continue
                    name = f[:-10]
                    if filter and filter.lower() not in name.lower():
                        continue
                    node = parse(read_file(os.path.join(path, f)),
                                 forms(read_file(os.path.join(path, f)))[0])
                    rows.append({"name": name, "description": (first(node, "descr") or ["", ""])[1],
                                 "tags": (first(node, "tags") or ["", ""])[1],
                                 "pad_count": len(all_of(node, "pad"))})
            else:
                raise ValueError("kind must be symbol or footprint")
            return {"ok": True, "library": path, "count": len(rows), "items": rows}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_get_symbol(library: str, name: str) -> dict[str, Any]:
        """One library symbol: properties, units, whether it is a power
        symbol, the symbol it extends, and every pin's number, name, type,
        unit, position (library mm, Y up), angle and length."""
        def body():
            path = _library("symbol", library)
            text = read_file(path)
            span = _symbol_span(text, name)
            if span is None:
                raise ValueError(f"no symbol {name} in {os.path.basename(path)}")
            return {"ok": True, "library": path, **_symbol_info(parse(text, span))}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_get_footprint(library: str, name: str) -> dict[str, Any]:
        """One library footprint: description, tags, properties, attributes,
        every pad's number, type, shape, position, angle, size, drill and
        layers (mm from the footprint origin), graphic counts per layer, and
        its 3D model paths."""
        def body():
            path = _footprint_file(library, name)
            text = read_file(path)
            return {"ok": True, "file": path, **_footprint_info(parse(text, forms(text)[0]))}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_search(query: str, kind: str = "symbol",
                               limit: int = 50) -> dict[str, Any]:
        """Search every library the project sees for symbols or footprints
        (kind) whose name, description or keywords hold every word of
        query. Returns "Library:Name" ids ready for placing."""
        def body():
            if kind not in libtables.KINDS:
                raise ValueError("kind must be symbol or footprint")
            words = [w.lower() for w in query.split() if w]
            if not words:
                raise ValueError("query is empty")
            found, searched, unread = [], 0, []
            for row in libtables.libraries(kind, _project_dir(), _cli()):
                if row["disabled"] or len(found) >= limit:
                    continue
                path = row["path"]
                try:
                    if kind == "symbol":
                        text = read_file(path)
                        searched += 1
                        for m in re.finditer(r'\n\t\(symbol "([^"]+)"', text):
                            end = text.find("\n\t)", m.end())
                            chunk = text[m.start():end if end > 0 else m.end() + 4000]
                            hay = (m.group(1) + " " + " ".join(re.findall(
                                r'\(property "(?:Description|ki_keywords)" "([^"]*)"', chunk))).lower()
                            if all(w in hay for w in words):
                                found.append({"id": f"{row['nickname']}:{m.group(1)}"})
                                if len(found) >= limit:
                                    break
                    else:
                        searched += 1
                        for f in os.listdir(path):
                            if not f.endswith(".kicad_mod"):
                                continue
                            hay = f[:-10].lower()
                            if all(w in hay for w in words):
                                found.append({"id": f"{row['nickname']}:{f[:-10]}"})
                                if len(found) >= limit:
                                    break
                except OSError as e:
                    unread.append(f"{row['nickname']}: {e}")
            out = {"ok": True, "searched_libraries": searched, "count": len(found), "items": found}
            if unread:
                out["unread"] = unread
            if kind == "footprint":
                out["matched_on"] = "footprint names"
            return out
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_copy(name: str, library: str, to_library: str = "",
                             new_name: str = "", kind: str = "symbol") -> dict[str, Any]:
        """Copy a symbol or footprint (kind) within a library or into another
        (to_library, an existing .kicad_sym or .pretty, by nickname or
        path), as new_name (default: the same name)."""
        def body():
            target_name = new_name or name
            if kind == "symbol":
                src = _library("symbol", library)
                dst = _library("symbol", to_library) if to_library else src
                text = read_file(src)
                span = _symbol_span(text, name)
                if span is None:
                    raise ValueError(f"no symbol {name} in {os.path.basename(src)}")
                block = _reindent(text[span[0]:span[1]], 0)
                if target_name != name:
                    block = block.replace(f'(symbol {q(name)}', f'(symbol {q(target_name)}', 1)
                    block = block.replace(f'(symbol "{name}_', f'(symbol "{target_name}_')
                dtext = read_file(dst)
                if _symbol_span(dtext, target_name) is not None:
                    raise ValueError(f"{target_name} already exists in {os.path.basename(dst)}")
                write_file(dst, insert_before_close(dtext, forms(dtext)[0], block))
                ok = _symbol_span(read_file(dst), target_name) is not None
                return {"ok": ok, "library": dst, "name": target_name, "note": RELOAD_NOTE}
            if kind == "footprint":
                src = _footprint_file(library, name)
                if not os.path.isfile(src):
                    raise ValueError(f"no footprint {name}")
                dst = _footprint_file(to_library or library, target_name)
                if os.path.exists(dst):
                    raise ValueError(f"{target_name} already exists at {dst}")
                text = read_file(src)
                if target_name != name:
                    text = text.replace(f"(footprint {q(name)}", f"(footprint {q(target_name)}", 1)
                write_file(dst, text)
                return {"ok": os.path.isfile(dst), "file": dst, "name": target_name,
                        "note": RELOAD_NOTE}
            raise ValueError("kind must be symbol or footprint")
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_delete(names: list[str], library: str,
                               kind: str = "symbol") -> dict[str, Any]:
        """Delete symbols or footprints (kind) from a library. A symbol that
        others extend is refused."""
        def body():
            results = []
            if kind == "symbol":
                path = _library("symbol", library)
                text = read_file(path)
                for n in names:
                    span = _symbol_span(text, n)
                    if span is None:
                        results.append({"name": n, "ok": False, "reason": "not in the library"})
                        continue
                    if re.search(r'\(extends ' + re.escape(q(n)) + r'\)', text):
                        results.append({"name": n, "ok": False, "reason": "other symbols extend it"})
                        continue
                    text = cut(text, span)
                    results.append({"name": n, "ok": True})
                if any(r["ok"] for r in results):
                    write_file(path, text)
                    back = read_file(path)
                    for r in results:
                        if r["ok"] and _symbol_span(back, r["name"]) is not None:
                            r.update(ok=False, reason="still in the file")
            elif kind == "footprint":
                for n in names:
                    f = _footprint_file(library, n)
                    if not os.path.isfile(f):
                        results.append({"name": n, "ok": False, "reason": "not in the library"})
                        continue
                    os.remove(f)
                    results.append({"name": n, "ok": not os.path.exists(f)})
            else:
                raise ValueError("kind must be symbol or footprint")
            return _summary(results, note=RELOAD_NOTE)
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_rename(name: str, new_name: str, library: str,
                               kind: str = "symbol") -> dict[str, Any]:
        """Rename a symbol or footprint (kind) in its library. Symbols that
        extend it follow. Placed copies in schematics and boards keep the
        old name until updated from the library."""
        def body():
            if kind == "symbol":
                path = _library("symbol", library)
                text = read_file(path)
                if _symbol_span(text, name) is None:
                    raise ValueError(f"no symbol {name}")
                if _symbol_span(text, new_name) is not None:
                    raise ValueError(f"{new_name} already exists")
                text = text.replace(f"(symbol {q(name)}", f"(symbol {q(new_name)}", 1)
                text = text.replace(f'(symbol "{name}_', f'(symbol "{new_name}_')
                text = text.replace(f"(extends {q(name)})", f"(extends {q(new_name)})")
                write_file(path, text)
                ok = _symbol_span(read_file(path), new_name) is not None
                return {"ok": ok, "library": path, "name": new_name, "note": RELOAD_NOTE}
            if kind == "footprint":
                src = _footprint_file(library, name)
                dst = _footprint_file(library, new_name)
                if not os.path.isfile(src) or os.path.exists(dst):
                    raise ValueError(f"no footprint {name}, or {new_name} already exists")
                text = read_file(src).replace(f"(footprint {q(name)}", f"(footprint {q(new_name)}", 1)
                write_file(dst, text)
                os.remove(src)
                return {"ok": os.path.isfile(dst) and not os.path.exists(src), "file": dst,
                        "note": RELOAD_NOTE}
            raise ValueError("kind must be symbol or footprint")
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_set_symbol_properties(items: list[dict[str, Any]],
                                              library: str = "") -> dict[str, Any]:
        """Set properties of library symbols. Each item: library, name,
        properties {name: value, or null to remove}: Value, Footprint,
        Datasheet, Description, ki_keywords, ki_fp_filters, or any field
        (MPN, Manufacturer: added hidden)."""
        def body():
            merged = [{**({"library": library} if library else {}), **i} for i in items]
            results = []
            for a in merged:
                try:
                    if not a.get("library") or not a.get("name") or not a.get("properties"):
                        raise ValueError("each item needs library, name and properties")

                    def change(block, props=a["properties"]):
                        return _set_properties(block, props)
                    path, _ = _edit_symbol(a["library"], a["name"], change)
                    text = read_file(path)
                    now = _props(parse(text, _symbol_span(text, a["name"])))
                    off = [k for k, v in a["properties"].items()
                           if (v is None and k in now) or (v is not None and now.get(k) != str(v))]
                    results.append({"name": a["name"], "ok": not off, "properties": now,
                                    **({"reason": f"not stored: {off}"} if off else {})})
                except (ValueError, OSError) as e:
                    results.append({"name": a.get("name"), "ok": False, "reason": str(e)})
            return _summary(results, note=RELOAD_NOTE)
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_add_pins(library: str, name: str, pins: list[dict[str, Any]],
                                 unit: int = 1) -> dict[str, Any]:
        """Add pins to a library symbol's unit. Each pin: number, name, type
        (input, output, bidirectional, passive, power_in, power_out,
        open_collector, no_connect...), x_mm, y_mm (its connection end,
        library coordinates, Y up), angle_deg (0 for a pin on the body's
        left, 180 on its right), length_mm, shape, hidden. A number the unit
        already has is refused."""
        def body():
            def change(block):
                used = {p["number"] for p in lib_pins(parse(block, forms(block)[0]))
                        if p["unit"] in (0, unit)}
                for p in pins:
                    if str(p.get("number")) in used:
                        raise ValueError(f"pin {p.get('number')} already exists")
                    used.add(str(p.get("number")))
                block, span = _unit_span(block, name, unit)
                for p in pins:
                    block = insert_before_close(block, span, _pin_block(p), "\t\t")
                    block, span = _unit_span(block, name, unit)
                return block
            path, _ = _edit_symbol(library, name, change)
            text = read_file(path)
            info = _symbol_info(parse(text, _symbol_span(text, name)))
            have = {(p["number"], p["unit"]) for p in info["pins"]}
            missing = [p["number"] for p in pins if (str(p["number"]), unit) not in have]
            return {"ok": not missing, "library": path, "pin_count": len(info["pins"]),
                    **({"reason": f"pins not found after writing: {missing}"} if missing else {}),
                    "note": RELOAD_NOTE}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_add_symbol_graphics(library: str, name: str,
                                            items: list[dict[str, Any]],
                                            unit: int = 1) -> dict[str, Any]:
        """Draw on a library symbol's unit (library mm, Y up). Each item:
        shape rectangle (points: two corners), polyline (points), circle
        (center_mm, radius_mm), arc (points: start, mid, end) or text (text,
        position_mm, size_mm); with width_mm and fill (none, outline,
        background)."""
        def body():
            blocks = [_graphic_block(g) for g in items]

            def change(block):
                block, span = _unit_span(block, name, unit)
                for b in blocks:
                    block = insert_before_close(block, span, b, "\t\t")
                    block, span = _unit_span(block, name, unit)
                return block
            path, _ = _edit_symbol(library, name, change)
            return {"ok": True, "library": path, "added": len(blocks), "note": RELOAD_NOTE}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_set_footprint_properties(library: str, name: str,
                                                 description: Optional[str] = None,
                                                 tags: Optional[str] = None,
                                                 mounting: Optional[str] = None,
                                                 add_models: Optional[list[dict[str, Any]]] = None,
                                                 remove_models: Optional[list[str]] = None
                                                 ) -> dict[str, Any]:
        """Set a library footprint's description, tags, mounting (smd,
        through_hole, unspecified), and 3D models: add_models [{path,
        offset_mm, rotation_deg, scale}] and remove_models (paths, or "*"
        for all)."""
        def body():
            def change(text):
                root = forms(text)[0]
                for tag, value in (("descr", description), ("tags", tags)):
                    if value is None:
                        continue
                    spans = find_children(text, root, tag)
                    if spans:
                        text = text[:spans[0][0]] + f"({tag} {q(value)})" + text[spans[0][1]:]
                    else:
                        text = insert_before_close(text, root, f"({tag} {q(value)})")
                    root = forms(text)[0]
                if mounting is not None:
                    attr = {"smd": "smd", "through_hole": "through_hole", "unspecified": ""}
                    if mounting not in attr:
                        raise ValueError("mounting must be smd, through_hole or unspecified")
                    spans = find_children(text, root, "attr")
                    keep = [a for a in (parse(text, spans[0])[1:] if spans else [])
                            if a not in ("smd", "through_hole")]
                    new = " ".join(([attr[mounting]] if attr[mounting] else []) + keep)
                    block = f"(attr {new})" if new else ""
                    if spans:
                        text = text[:spans[0][0]] + block + text[spans[0][1]:]
                    elif block:
                        text = insert_before_close(text, root, block)
                    root = forms(text)[0]
                for m in remove_models or []:
                    for s in reversed(find_children(text, root, "model")):
                        if m == "*" or parse(text, s)[1] == m:
                            text = cut(text, s)
                    root = forms(text)[0]
                for m in add_models or []:
                    off = m.get("offset_mm") or [0, 0, 0]
                    rot = m.get("rotation_deg") or [0, 0, 0]
                    sc = m.get("scale") or [1, 1, 1]
                    text = insert_before_close(
                        text, root,
                        f"(model {q(m['path'])}\n\t(offset (xyz {' '.join(num(v) for v in off)}))\n"
                        f"\t(scale (xyz {' '.join(num(v) for v in sc)}))\n"
                        f"\t(rotate (xyz {' '.join(num(v) for v in rot)}))\n)")
                    root = forms(text)[0]
                return text
            path = _edit_footprint(library, name, change)
            text = read_file(path)
            return {"ok": True, "file": path, **{k: v for k, v in _footprint_info(
                parse(text, forms(text)[0])).items() if k in ("description", "tags",
                                                               "attributes", "models")},
                    "note": RELOAD_NOTE}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_add_footprint_items(library: str, name: str,
                                            items: list[dict[str, Any]]) -> dict[str, Any]:
        """Add pads and graphics to a library footprint (mm from its origin,
        Y down). Each item: kind pad (number, type smd/pth/npth, shape
        roundrect/rect/circle/oval, position_mm, size_mm, drill_mm,
        angle_deg, layers), line or arc (points), rect (points: corners,
        filled), circle (center_mm, radius_mm), polygon (points), text (text,
        position_mm, size_mm); graphics take layer (default F.SilkS) and
        width_mm."""
        def body():
            blocks = [_fp_item_block(it) for it in items]

            def change(text):
                for b in blocks:
                    text = insert_before_close(text, forms(text)[0], b)
                return text
            path = _edit_footprint(library, name, change)
            text = read_file(path)
            info = _footprint_info(parse(text, forms(text)[0]))
            return {"ok": True, "file": path, "added": len(blocks), "pad_count": len(info["pads"]),
                    "graphics": info["graphics"], "note": RELOAD_NOTE}
        return _guard(body)

    @mcp.tool()
    async def kicad_lib_check(library: str, kind: str = "symbol") -> dict[str, Any]:
        """Check a symbol or footprint library (kind) for common faults.
        Symbols: no pins, duplicate pin numbers in a unit, pins off the 1.27
        mm grid, empty Reference or Value. Footprints: no pads, duplicate
        pad numbers of different shapes, no courtyard, no F.Fab or silk
        outline, no Reference text. Lists each finding with its item."""
        def body():
            findings = []
            if kind == "symbol":
                path = _library("symbol", library)
                text = read_file(path)
                spans = find_children(text, forms(text)[0], "symbol")
                for s in spans:
                    node = parse(text, s)
                    name = node[1]
                    if first(node, "extends"):
                        continue
                    pins = lib_pins(node)
                    props = _props(node)
                    if not pins and first(node, "power") is None:
                        findings.append({"item": name, "finding": "no pins"})
                    seen = {}
                    for p in pins:
                        k = (p["unit"], p["body_style"], p["number"])
                        if k in seen and p["number"]:
                            findings.append({"item": name, "finding": f"pin {p['number']} repeated "
                                                                      f"in unit {p['unit']}"})
                        seen[k] = True
                        if any(abs(v / 1.27 - round(v / 1.27)) > 1e-3 for v in p["at"]):
                            findings.append({"item": name, "finding": f"pin {p['number']} off the "
                                                                      f"1.27 mm grid at {list(p['at'])}"})
                    for f in ("Reference", "Value"):
                        if not props.get(f):
                            findings.append({"item": name, "finding": f"{f} is empty"})
                checked = len(spans)
            elif kind == "footprint":
                folder = _library("footprint", library)
                files = [f for f in os.listdir(folder) if f.endswith(".kicad_mod")]
                for f in files:
                    text = read_file(os.path.join(folder, f))
                    node = parse(text, forms(text)[0])
                    info = _footprint_info(node)
                    name = info["name"]
                    if not info["pads"]:
                        findings.append({"item": name, "finding": "no pads"})
                    shapes: dict[str, set] = {}
                    for p in info["pads"]:
                        if p["number"]:
                            shapes.setdefault(p["number"], set()).add(
                                (p["shape"], tuple(p["size_mm"])))
                    for n, kinds in shapes.items():
                        if len(kinds) > 1:
                            findings.append({"item": name, "finding": f"pad {n} has "
                                                                      f"{len(kinds)} shapes"})
                    layers = " ".join(info["graphics"])
                    for layer, what in (("CrtYd", "no courtyard"), ("Fab", "no F.Fab drawing"),
                                        ("SilkS", "no silkscreen")):
                        if layer not in layers:
                            findings.append({"item": name, "finding": what})
                    if "Reference" not in info["properties"]:
                        findings.append({"item": name, "finding": "no Reference text"})
                checked = len(files)
            else:
                raise ValueError("kind must be symbol or footprint")
            return {"ok": True, "checked": checked, "finding_count": len(findings),
                    "findings": findings}
        return _guard(body)


def _set_properties(block: str, props: dict[str, Any]) -> str:
    for name, value in props.items():
        root = forms(block)[0]
        found = None
        for s in find_children(block, root, "property"):
            if parse(block, s)[1] == name:
                found = s
        if value is None:
            if found is not None:
                if name in ("Reference", "Value"):
                    raise ValueError(f"{name} cannot be removed")
                block = cut(block, found)
            continue
        if found is not None:
            old = parse(block, found)
            seg = block[found[0]:found[1]].replace(
                f"(property {q(name)} {q(old[2])}", f"(property {q(name)} {q(value)}", 1)
            block = block[:found[0]] + seg + block[found[1]:]
        else:
            props_spans = find_children(block, root, "property")
            new = (f"(property {q(name)} {q(value)}\n\t(at 0 0 0)\n\t(hide yes)\n"
                   f"\t(effects (font (size 1.27 1.27)))\n)")
            if props_spans:
                last = props_spans[-1]
                body = "\n\t" + new.replace("\n", "\n\t")
                block = block[:last[1]] + body + block[last[1]:]
            else:
                block = insert_before_close(block, root, new)
    return block


__all__ = ["register_kicad_lib_tools"]
