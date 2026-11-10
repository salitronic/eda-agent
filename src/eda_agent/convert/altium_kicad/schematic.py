# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The imported schematic as a KiCad project would have it.

KiCad's Altium importer makes one library symbol per placed instance and
orientation, named ``LIB:<sheet>_<n>_[mirrored_]<part>_<LIB>.IntLib``, power
ports as symbols of a ``<project>-altium-import`` library, the part's value
in an ``ALTIUM_VALUE`` field, and keeps each sheet's Altium offset off the
1.27 mm connection grid. ``normalize`` turns that into one symbol per part
in project libraries named after the Altium libraries, power symbols in a
``<project>_Power`` library annotated ``#PWRnn``, Value, Footprint and
Datasheet filled in, and every sheet moved rigidly onto the grid.
``tidy_wiring`` then trims Altium's wire stubs back to their labels, takes
out an Altium-drawn title block and adds the PWR_FLAGs ERC asks for.
"""

from __future__ import annotations

import copy
import math
import os
import re
import uuid
from collections import Counter
from typing import Any, Optional

from .tree import Sym, find, first, num, prop, read, write

GRID = 1.27
_COORD_TAGS = {"at", "xy", "start", "end", "mid", "center"}
_IMPORT_LIB = "-altium-import"
_LIBRARY_SUFFIX = re.compile(r"\.(IntLib|SchLib|LibPkg|DbLib)$", re.I)
#: Altium parameters the importer copies that KiCad then misreads: a field
#: named FOOTPRINT is taken for the Footprint field and reported as a
#: footprint mismatch.
_DROP_FIELDS = ("FOOTPRINT",)


def sheets_in(project_dir: str) -> list[str]:
    return sorted(os.path.join(project_dir, f) for f in os.listdir(project_dir)
                  if f.lower().endswith(".kicad_sch"))


def canonical(lib_id: str, sheet_bases: list[str]) -> tuple[str, str, bool]:
    """``(library, part, mirrored)`` for an importer symbol name."""
    lib, _, name = lib_id.partition(":")
    # The root sheet's parts are prefixed "root_<n>_".
    for base in sorted(list(sheet_bases) + ["root"], key=len, reverse=True):
        stripped = re.sub(r"^" + re.escape(base) + r"_\d+_", "", name)
        if stripped != name:
            name = stripped
            break
    mirrored = name.startswith("mirrored_")
    if mirrored:
        name = name[len("mirrored_"):]
    suffix = "_" + lib
    plain = _LIBRARY_SUFFIX.sub("", name)
    if plain != name and plain.endswith(suffix):
        name = plain[: -len(suffix)]
    elif name.endswith(suffix):
        # A part Altium loaded without a source library: "LIB" is "*" and
        # the name ends "_*".
        name = name[: -len(suffix)]
    # A unit index the importer appends to a repeated part ("_2").
    name = re.sub(r"_" + re.escape(lib) + r"\.(IntLib|SchLib|LibPkg|DbLib)_\d+$", "", name, flags=re.I)
    return lib, name, mirrored


def library_name(lib: str, project: str) -> str:
    """A library name that is also a file name. Altium's "*" (a part with
    no source library) becomes the project's own library."""
    if lib.endswith(_IMPORT_LIB):
        return lib
    clean = re.sub(r'[<>:"/\|?*]', "", lib).strip(" .")
    return clean or project


def body(symbol_def: list) -> list:
    """A library symbol with what does not describe its drawing taken out:
    uuids, unit names and rectangle corner order."""
    d = copy.deepcopy(symbol_def)

    def walk(x):
        if not isinstance(x, list):
            return x
        x = [walk(c) for c in x if not (isinstance(c, list) and c and c[0] in ("uuid", "property"))]
        if x and x[0] == "rectangle":
            s, e = first(x, "start"), first(x, "end")
            if s is not None and e is not None:
                xs = sorted([float(s[1]), float(e[1])])
                ys = sorted([float(s[2]), float(e[2])])
                s[1], e[1], s[2], e[2] = (Sym(num(xs[0])), Sym(num(xs[1])),
                                          Sym(num(ys[0])), Sym(num(ys[1])))
        if x and x[0] == "symbol" and len(x) > 1 and isinstance(x[1], str):
            m = re.search(r"_(\d+)_(\d+)$", x[1])
            x[1] = f"UNIT_{m.group(1)}_{m.group(2)}" if m else "TOP"
        return x

    return walk(d)


def _set_hidden(p: list, hidden: bool) -> None:
    h = first(p, "hide")
    if h is None:
        p.insert(3, [Sym("hide"), Sym("yes" if hidden else "no")])
    else:
        h[1] = Sym("yes" if hidden else "no")


def grid_offset(sheet: list) -> tuple[float, float]:
    """The shift that puts most of a sheet's wire points on the grid."""
    xs, ys = Counter(), Counter()

    def walk(x):
        if not isinstance(x, list) or (x and x[0] == "lib_symbols"):
            return
        if x and x[0] == "xy" and len(x) >= 3:
            try:
                xs[round(float(x[1]) % GRID, 4) % GRID] += 1
                ys[round(float(x[2]) % GRID, 4) % GRID] += 1
            except ValueError:
                pass
        for c in x:
            walk(c)

    walk(sheet)

    def shift(counter):
        if not counter:
            return 0.0
        off = counter.most_common(1)[0][0]
        if abs(off) < 1e-6 or abs(off - GRID) < 1e-6:
            return 0.0
        return round(GRID - off, 4)

    return shift(xs), shift(ys)


def move(sheet: list, dx: float, dy: float) -> None:
    """Every coordinate of a sheet by (dx, dy); library symbols keep theirs."""
    def walk(x):
        if not isinstance(x, list) or (x and x[0] == "lib_symbols"):
            return
        if x and x[0] in _COORD_TAGS and len(x) >= 3 and not isinstance(x[1], list):
            try:
                x[1] = Sym(num(float(x[1]) + dx))
                x[2] = Sym(num(float(x[2]) + dy))
            except ValueError:
                pass
        for c in x:
            walk(c)

    walk(sheet)


def normalize(project_dir: str, project_name: str,
              footprints: dict[str, str]) -> dict[str, Any]:
    """Project libraries, power symbols, fields and the grid, in place.

    ``footprints`` maps a designator to the board footprint's library id
    (``LIB:NAME``), so a symbol names the footprint its part has.
    """
    paths = sheets_in(project_dir)
    bases = [os.path.splitext(os.path.basename(p))[0] for p in paths]
    power_lib = f"{project_name}_Power"
    libraries: dict[str, dict[str, list]] = {}
    bodies: dict[str, list] = {}
    power_names: dict[str, str] = {}
    report: dict[str, Any] = {"sheets": [], "power_symbols": 0,
                              "symbols_kept_apart": [], "fields_dropped": 0}
    power_count = 0

    def new_id(old: str, value: str = "") -> tuple[str, str]:
        lib, part, _ = canonical(old, bases)
        lib = library_name(lib, project_name)
        if lib.endswith(_IMPORT_LIB):
            # One power symbol per port style; it is named after its net
            # unless another style already took that name.
            key = old.partition(":")[2]
            if key not in power_names:
                want = value or part
                taken = set(power_names.values())
                power_names[key] = want if want not in taken else part
            return power_lib, power_names[key]
        return lib, part

    for path in paths:
        sheet = read(path)
        table = first(sheet, "lib_symbols")
        instances = find(sheet, "symbol")
        value_of = {}
        for s in instances:
            lid = first(s, "lib_id")
            v = prop(s, "Value")
            if lid is not None and v is not None:
                value_of.setdefault(lid[1], v[2])
        renamed: dict[str, str] = {}
        defs: dict[str, list] = {}
        for d in find(table, "symbol") if table is not None else []:
            old = d[1]
            lib, part = new_id(old, value_of.get(old, ""))
            nid = f"{lib}:{part}"
            shape = body(d)
            if nid in bodies and bodies[nid] != shape:
                # Two drawings under one name: kept apart, never merged.
                k = 2
                while f"{nid}_{k}" in bodies and bodies[f"{nid}_{k}"] != shape:
                    k += 1
                part = f"{part}_{k}"
                nid = f"{lib}:{part}"
                report["symbols_kept_apart"].append(nid)
            bodies.setdefault(nid, shape)
            renamed[old] = nid
            if nid in defs:
                continue
            new = copy.deepcopy(d)
            base = old.partition(":")[2]
            new[1] = nid
            for unit in find(new, "symbol"):
                if isinstance(unit[1], str) and unit[1].startswith(base):
                    unit[1] = part + unit[1][len(base):]
            defs[nid] = new

        for s in instances:
            lid = first(s, "lib_id")
            if lid is None or lid[1] not in renamed:
                continue
            lid[1] = renamed[lid[1]]
            lib = lid[1].partition(":")[0]
            ref = prop(s, "Reference")
            if lib == power_lib:
                power_count += 1
                designator = f"#PWR{power_count:02d}"
                if ref is not None:
                    ref[2] = designator
                for inst in find(s, "instances"):
                    for proj in find(inst, "project"):
                        for p in find(proj, "path"):
                            r = first(p, "reference")
                            if r is not None:
                                r[1] = designator
                continue
            designator = ref[2] if ref is not None else ""
            fp = prop(s, "Footprint")
            if fp is not None and designator in footprints:
                fp[2] = footprints[designator]
            altium_value = prop(s, "ALTIUM_VALUE")
            value = prop(s, "Value")
            if altium_value is not None and value is not None:
                text = altium_value[2]
                if text.startswith("${") or not text.strip():
                    part_number = prop(s, "PARTNUMBER")
                    text = part_number[2] if part_number is not None else lid[1].partition(":")[2]
                value[2] = text
                # Value takes where and how the Altium value was shown.
                for tag in ("at", "effects", "show_name", "do_not_autoplace"):
                    a, b = first(altium_value, tag), first(value, tag)
                    if a is not None and b is not None:
                        b[:] = copy.deepcopy(a)
                hidden = first(altium_value, "hide")
                _set_hidden(value, bool(hidden) and hidden[1] == "yes")
                s.remove(altium_value)
            for name in _DROP_FIELDS:
                f = prop(s, name)
                if f is not None:
                    s.remove(f)
                    report["fields_dropped"] += 1
            help_url, datasheet = prop(s, "HELPURL"), prop(s, "Datasheet")
            if help_url is not None and datasheet is not None and not datasheet[2]:
                datasheet[2] = help_url[2]

        for nid, d in defs.items():
            lib, part = nid.split(":", 1)
            users = [s for s in instances if first(s, "lib_id") is not None
                     and first(s, "lib_id")[1] == nid]
            if users and lib != power_lib:
                for name in ("Footprint", "Datasheet", "Description"):
                    pi, pd = prop(users[0], name), prop(d, name)
                    if pi is not None and pd is not None:
                        pd[2] = pi[2]
                pv = prop(d, "Value")
                if pv is not None:
                    pv[2] = part
            for name in ("ALTIUM_VALUE",) + _DROP_FIELDS:
                f = prop(d, name)
                if f is not None:
                    d.remove(f)
            libraries.setdefault(lib, {}).setdefault(part, d)
        if table is not None:
            table[1:] = list(defs.values())
        dx, dy = grid_offset(sheet)
        move(sheet, dx, dy)
        write(path, sheet)
        report["sheets"].append({"sheet": os.path.basename(path),
                                 "symbols": len(defs), "moved_mm": [dx, dy]})

    for lib, parts in libraries.items():
        out = [Sym("kicad_symbol_lib"), [Sym("version"), Sym("20251024")],
               [Sym("generator"), "eda_agent"], [Sym("generator_version"), "10.0"]]
        for part, d in sorted(parts.items()):
            e = copy.deepcopy(d)
            e[1] = part
            out.append(e)
        write(os.path.join(project_dir, f"{lib}.kicad_sym"), out)
    rows = "".join(f'  (lib (name "{lib}")(type "KiCad")(uri "${{KIPRJMOD}}/{lib}.kicad_sym")(options "")(descr ""))\n'
                   for lib in sorted(libraries))
    with open(os.path.join(project_dir, "sym-lib-table"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"(sym_lib_table\n  (version 7)\n{rows})\n")
    report["power_symbols"] = power_count
    report["libraries"] = {lib: len(parts) for lib, parts in sorted(libraries.items())}
    report["power_library"] = power_lib
    return report


# --- wiring ---------------------------------------------------------------

def _transform(px: float, py: float, angle: float, mirror: Optional[str]) -> tuple[float, float]:
    """A library pin point placed on the sheet: library y-up to sheet
    y-down, rotated, then mirrored."""
    x, y = px, -py
    a = math.radians(angle)
    c, s = round(math.cos(a)), round(math.sin(a))
    x, y = x * c + y * s, -x * s + y * c
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    return x, y


def connection_points(sheet: list) -> set[tuple[float, float]]:
    """Every point a wire end can connect to except another wire: pin ends,
    junctions, labels, no-connects and sheet pins."""
    defs = {d[1]: d for d in find(first(sheet, "lib_symbols") or [], "symbol")}
    points = set()
    for s in find(sheet, "symbol"):
        lid = first(s, "lib_id")
        d = defs.get(lid[1]) if lid is not None else None
        at = first(s, "at")
        if d is None or at is None:
            continue
        X, Y = float(at[1]), float(at[2])
        angle = float(at[3]) if len(at) > 3 else 0.0
        m = first(s, "mirror")
        mirror = str(m[1]) if m else None
        unit_tag = first(s, "unit")
        unit = int(unit_tag[1]) if unit_tag else 1
        for u in find(d, "symbol"):
            mm = re.search(r"_(\d+)_(\d+)$", u[1])
            if mm and int(mm.group(1)) not in (0, unit):
                continue
            for p in find(u, "pin"):
                pa = first(p, "at")
                dx, dy = _transform(float(pa[1]), float(pa[2]), angle, mirror)
                points.add((round(X + dx, 3), round(Y + dy, 3)))
    for kind in ("junction", "label", "global_label", "hierarchical_label", "no_connect"):
        for e in find(sheet, kind):
            a = first(e, "at")
            points.add((round(float(a[1]), 3), round(float(a[2]), 3)))
    for sh in find(sheet, "sheet"):
        for p in find(sh, "pin"):
            a = first(p, "at")
            points.add((round(float(a[1]), 3), round(float(a[2]), 3)))
    return points


def _pt(e: list) -> tuple[float, float]:
    return (round(float(e[1]), 3), round(float(e[2]), 3))


def _on_segment(p, a, b, tol: float = 1e-3) -> bool:
    (px, py), (ax, ay), (bx, by) = p, a, b
    if abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) > tol:
        return False
    return (min(ax, bx) - tol <= px <= max(ax, bx) + tol
            and min(ay, by) - tol <= py <= max(ay, by) + tol)


def trim_stubs(sheet: list) -> dict[str, Any]:
    """Altium draws a wire on past its net label. Each wire end that meets
    nothing is pulled back to the nearest label on that wire."""
    points = connection_points(sheet)
    wires = find(sheet, "wire")
    labels = find(sheet, "label") + find(sheet, "global_label") + find(sheet, "hierarchical_label")
    segments = [tuple(_pt(x) for x in find(first(w, "pts"), "xy")) for w in wires]
    trimmed, free = 0, []

    def connected(p, own):
        if p in points:
            return True
        return any(k != own and (p == a or p == b or _on_segment(p, a, b))
                   for k, (a, b) in enumerate(segments))

    for wi, w in enumerate(wires):
        pts = find(first(w, "pts"), "xy")
        if len(pts) != 2:
            continue
        for i in (0, 1):
            p, other = _pt(pts[i]), _pt(pts[1 - i])
            if connected(p, wi) or not connected(other, wi):
                continue
            best = None
            for lb in labels:
                lp = _pt(first(lb, "at"))
                if lp != p and _on_segment(lp, p, other):
                    d = math.dist(lp, p)
                    if best is None or d < best[0]:
                        best = (d, lp)
            if best:
                pts[i][1], pts[i][2] = Sym(num(best[1][0])), Sym(num(best[1][1]))
                segments[wi] = (_pt(pts[0]), _pt(pts[1]))
                trimmed += 1
            else:
                free.append(list(p))
    return {"trimmed": trimmed, "free_ends_without_a_label": free}


#: Paper sizes in mm, as KiCad names them.
_PAPER = {"A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420),
          "A1": (841, 594), "A0": (1189, 841), "A": (279.4, 215.9), "B": (431.8, 279.4),
          "C": (558.8, 431.8), "D": (863.6, 558.8), "E": (1117.6, 863.6),
          "USLetter": (279.4, 215.9), "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4)}


def _coords(node) -> list[tuple[float, float]]:
    out = []

    def walk(e):
        if not isinstance(e, list):
            return
        if e and e[0] in _COORD_TAGS and len(e) >= 3 and not isinstance(e[1], list):
            try:
                out.append((float(e[1]), float(e[2])))
            except ValueError:
                pass
        for c in e:
            walk(c)

    walk(node)
    return out


def remove_title_block(sheet: list, width: float = 130.0, height: float = 50.0) -> list[str]:
    """Graphics lying wholly in the sheet's bottom-right corner, where an
    Altium template draws its title block: KiCad draws its own there. Only
    text, lines, rectangles and text boxes; what was taken out is named."""
    paper = first(sheet, "paper")
    size = _PAPER.get(str(paper[1])) if paper else None
    if paper is not None and size is None and len(paper) >= 3:
        try:
            size = (float(paper[1]), float(paper[2]))
        except ValueError:
            size = None
    if size is None:
        return []
    x0, y0 = size[0] - width, size[1] - height
    gone = []
    for e in list(sheet):
        if isinstance(e, list) and e and e[0] in ("text", "polyline", "rectangle", "text_box"):
            pts = _coords(e)
            if pts and all(x >= x0 and y >= y0 for x, y in pts):
                sheet.remove(e)
                gone.append(e[1] if e[0] in ("text", "text_box") and isinstance(e[1], str) else str(e[0]))
    return gone


def power_flag_definition(kicad_share: str) -> Optional[list]:
    """KiCad's own PWR_FLAG symbol, from its power library."""
    path = os.path.join(kicad_share, "symbols", "power.kicad_sym")
    if not os.path.isfile(path):
        return None
    for s in find(read(path), "symbol"):
        if s[1] == "PWR_FLAG":
            return s
    return None


def add_power_flag(sheet: list, at: tuple[float, float], reference: str,
                   lib_id: str, project: str) -> None:
    """A PWR_FLAG with its pin on ``at``."""
    path = None
    for s in find(sheet, "symbol"):
        inst = first(s, "instances")
        proj = first(inst, "project") if inst else None
        p = first(proj, "path") if proj else None
        if p is not None:
            path = p[1]
            break
    x, y = at

    def field(name, value, dy):
        return [Sym("property"), name, value, [Sym("at"), Sym(num(x)), Sym(num(y + dy)), Sym("0")],
                [Sym("hide"), Sym("yes")],
                [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]]]]

    symbol = [Sym("symbol"), [Sym("lib_id"), lib_id],
              [Sym("at"), Sym(num(x)), Sym(num(y)), Sym("0")], [Sym("unit"), Sym("1")],
              [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("yes")],
              [Sym("on_board"), Sym("yes")], [Sym("dnp"), Sym("no")],
              [Sym("uuid"), str(uuid.uuid4())],
              field("Reference", reference, -1.905), field("Value", "PWR_FLAG", -3.81),
              field("Footprint", "", 0), field("Datasheet", "~", 0),
              [Sym("pin"), "1", [Sym("uuid"), str(uuid.uuid4())]]]
    if path is not None:
        symbol.append([Sym("instances"), [Sym("project"), project,
                       [Sym("path"), path, [Sym("reference"), reference], [Sym("unit"), Sym("1")]]]])
    sheet.append(symbol)
