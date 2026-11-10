# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A flat Altium project as one KiCad hierarchy.

An Altium project without a top sheet comes in as several KiCad 10
top-level sheets, the first one written into ``<project>.kicad_sch``.
kicad-cli reads only that file, so its netlist, ERC and the board's net
names saw one sheet in three. And Altium's net labels on such a project
join across sheets (its net scope is global), where a KiCad label joins
only on its own sheet.

``build_root`` gives the project one root sheet holding a sheet symbol for
each original sheet. A sheet symbol keeps its sheet's top-level uuid, so a
placed symbol's instance path only gains the root's uuid in front.
``globalize_labels`` makes every label whose name is used on more than one
sheet a global label. The pin-to-net check against Altium's own netlist
then says whether the result connects as Altium did.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from .tree import Sym, find, first, num, read, write

_SYMBOL_W, _SYMBOL_H, _PITCH = 40.64, 20.32, 30.48


def top_level_sheets(pro_path: str) -> list[dict[str, Any]]:
    with open(pro_path, encoding="utf-8") as fh:
        pro = json.load(fh)
    return list(pro.get("schematic", {}).get("top_level_sheets") or [])


def _sheet_symbol(name: str, filename: str, sheet_uuid: str, x: float, y: float,
                  project: str, root_uuid: str, page: int) -> list:
    def field(key, value, dy):
        return [Sym("property"), key, value,
                [Sym("at"), Sym(num(x)), Sym(num(y + dy)), Sym("0")],
                [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]],
                 [Sym("justify"), Sym("left"), Sym("bottom")]]]
    return [Sym("sheet"), [Sym("at"), Sym(num(x)), Sym(num(y))],
            [Sym("size"), Sym(num(_SYMBOL_W)), Sym(num(_SYMBOL_H))],
            [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("yes")],
            [Sym("on_board"), Sym("yes")], [Sym("dnp"), Sym("no")],
            [Sym("fields_autoplaced"), Sym("yes")],
            [Sym("stroke"), [Sym("width"), Sym("0.1524")], [Sym("type"), Sym("solid")]],
            [Sym("fill"), [Sym("color"), Sym("0"), Sym("0"), Sym("0"), Sym("0.0000")]],
            [Sym("uuid"), sheet_uuid],
            field("Sheetname", name, -0.7116), field("Sheetfile", filename, _SYMBOL_H + 0.5),
            [Sym("instances"), [Sym("project"), project,
                                [Sym("path"), f"/{root_uuid}", [Sym("page"), str(page)]]]]]


def _reroot_paths(node, old: str, new: str) -> int:
    """Instance paths beginning ``/<old>`` start with ``/<new>/<old>``."""
    changed = 0
    if isinstance(node, list):
        if node and node[0] == "path" and len(node) > 1 and isinstance(node[1], str):
            if node[1] == f"/{old}" or node[1].startswith(f"/{old}/"):
                node[1] = f"/{new}" + node[1]
                changed += 1
        for child in node:
            changed += _reroot_paths(child, old, new)
    return changed


def build_root(project_dir: str, project: str) -> dict[str, Any]:
    """One root sheet over the top-level sheets; nothing when there is one."""
    pro_path = os.path.join(project_dir, project + ".kicad_pro")
    tops = top_level_sheets(pro_path)
    if len(tops) < 2:
        return {"built": False, "top_level_sheets": len(tops)}
    root_file = project + ".kicad_sch"
    root_uuid = str(uuid.uuid4())
    sheets = []
    for i, top in enumerate(tops):
        filename = top["filename"]
        if filename == root_file:
            # The first sheet was written as the project's root file; it
            # moves to a file of its own name.
            filename = f"{top.get('name') or 'Sheet1'}.kicad_sch"
            if os.path.exists(os.path.join(project_dir, filename)):
                filename = f"{project}_{i + 1}.kicad_sch"
            from ...atomicfile import replace_with_retry
            replace_with_retry(os.path.join(project_dir, root_file),
                               os.path.join(project_dir, filename))
        path = os.path.join(project_dir, filename)
        tree = read(path)
        rerooted = _reroot_paths(tree, top["uuid"], root_uuid)
        write(path, tree)
        sheets.append({"name": top.get("name") or filename, "filename": filename,
                       "uuid": top["uuid"], "instances_rerooted": rerooted})

    root = [Sym("kicad_sch"), [Sym("version"), Sym("20250114")],
            [Sym("generator"), "eda_agent"], [Sym("generator_version"), "10.0"],
            [Sym("uuid"), root_uuid], [Sym("paper"), "A4"], [Sym("lib_symbols")]]
    for i, s in enumerate(sheets):
        root.append(_sheet_symbol(s["name"], s["filename"], s["uuid"],
                                  25.4, 25.4 + i * _PITCH, project, root_uuid, i + 2))
    root.append([Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]])
    write(os.path.join(project_dir, root_file), root)

    with open(pro_path, encoding="utf-8") as fh:
        pro = json.load(fh)
    pro.setdefault("schematic", {})["top_level_sheets"] = [
        {"filename": root_file, "name": "Root", "uuid": root_uuid}]
    pro["sheets"] = [[root_uuid, "Root"]] + [[s["uuid"], s["name"]] for s in sheets]
    from ...atomicfile import replace_with_retry
    tmp = pro_path + ".eda-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(pro, fh, indent=2)
    replace_with_retry(tmp, pro_path)
    return {"built": True, "root": root_file, "sheets": sheets}


def globalize_labels(project_dir: str, sheet_files: list[str]) -> dict[str, Any]:
    """Labels used on more than one sheet become global labels."""
    trees = {f: read(os.path.join(project_dir, f)) for f in sheet_files}
    where: dict[str, set] = {}
    for f, tree in trees.items():
        for lb in find(tree, "label"):
            where.setdefault(lb[1], set()).add(f)
    shared = {name for name, files in where.items() if len(files) > 1}
    changed = 0
    for f, tree in trees.items():
        touched = False
        for lb in find(tree, "label"):
            if lb[1] in shared:
                lb[0] = Sym("global_label")
                lb.insert(2, [Sym("shape"), Sym("passive")])
                changed += 1
                touched = True
        if touched:
            write(os.path.join(project_dir, f), tree)
    return {"globalized": changed, "names": sorted(shared)}


def is_flat(project_dir: str, project: str) -> bool:
    pro_path = os.path.join(project_dir, project + ".kicad_pro")
    if not os.path.isfile(pro_path):
        return False
    root = os.path.join(project_dir, project + ".kicad_sch")
    has_sheets = os.path.isfile(root) and bool(find(read(root), "sheet"))
    return len(top_level_sheets(pro_path)) > 1 and not has_sheets


def child_files(project_dir: str, project: str) -> list[str]:
    root = read(os.path.join(project_dir, project + ".kicad_sch"))
    out = []
    for s in find(root, "sheet"):
        for p in find(s, "property"):
            if len(p) > 2 and p[1] == "Sheetfile":
                out.append(p[2])
    return out


__all__ = ["build_root", "child_files", "globalize_labels", "is_flat", "first"]
