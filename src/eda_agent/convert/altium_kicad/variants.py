# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium project variants as KiCad 10 design variants.

KiCad keeps the variant names in the project file (``schematic.variants``)
and on the board (``(variants ...)``), and each part's differences on its
schematic instance and its footprint, as a ``(variant (name ...) ...)``
block holding ``dnp`` and field values. A part not fitted in Altium is
``dnp`` in KiCad. Its varied parameters become field values: Altium's
Value goes to Value, and its Comment, which also names an alternate part,
goes to Value when Value is not varied itself. KiCad varies fields, not
footprints, so an alternate on another footprint is listed instead.
"""

from __future__ import annotations

import json
from typing import Any

from .tree import Sym, blocks, find, first


def plan(variants: list[dict[str, Any]]) -> dict[str, Any]:
    """The KiCad side of Altium's variants: ``names`` (name, description),
    ``parts`` (designator -> variant -> {dnp, fields}) and
    ``footprints_not_varied`` (alternates KiCad cannot swap)."""
    names, parts, not_varied = [], {}, []
    for v in variants:
        name = str(v.get("name") or v.get("description") or "").strip()
        if not name:
            continue
        desc = str(v.get("description") or "")
        names.append((name, "" if desc == name else desc))
        for row in v.get("variations") or []:
            ref = str(row.get("designator") or "")
            if not ref:
                continue
            kind = str(row.get("kind") or "").lower().replace("_", " ")
            entry = {"dnp": kind == "not fitted", "fields": {}}
            given = {str(p.get("name")): str(p.get("value") or "")
                     for p in row.get("parameters") or [] if p.get("name")}
            for pname, pvalue in given.items():
                if pname == "Comment":
                    if "Value" not in given:
                        entry["fields"]["Value"] = pvalue
                else:
                    entry["fields"][pname] = pvalue
            if kind == "alternate":
                alt = str(row.get("alternate_part") or "")
                if alt and "Value" not in given:
                    entry["fields"].setdefault("Value", alt)
                link = row.get("alternate_link") or {}
                if link.get("footprint") and link.get("same_footprint") is False:
                    not_varied.append({"variant": name, "designator": ref,
                                       "footprint": link["footprint"]})
            if entry["dnp"] or entry["fields"]:
                parts.setdefault(ref, {})[name] = entry
    return {"names": names, "parts": parts, "footprints_not_varied": not_varied}


def _yes(flag: bool) -> Sym:
    return Sym("yes" if flag else "no")


def _sch_block(name: str, entry: dict[str, Any]) -> list:
    node: list = [Sym("variant"), [Sym("name"), name]]
    if entry["dnp"]:
        node.append([Sym("dnp"), _yes(True)])
    for fname, fvalue in entry["fields"].items():
        node.append([Sym("field"), [Sym("name"), fname], [Sym("value"), fvalue]])
    return node


def apply_sheet(sheet: list, parts: dict[str, dict[str, Any]]) -> int:
    """Each placed symbol's instance gets its variant blocks; the count of
    instances changed."""
    done = 0
    for s in find(sheet, "symbol"):
        inst = first(s, "instances")
        if inst is None:
            continue
        for proj in find(inst, "project"):
            for path in find(proj, "path"):
                ref = first(path, "reference")
                mine = parts.get(ref[1]) if ref is not None else None
                if not mine:
                    continue
                path[:] = [x for x in path if not (isinstance(x, list) and x
                                                   and x[0] == "variant")]
                path.extend(_sch_block(n, e) for n, e in mine.items())
                done += 1
    return done


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_board(text: str, names: list[tuple[str, str]],
                parts: dict[str, dict[str, Any]]) -> tuple[str, int]:
    """The board's variant list after its setup, and each footprint's
    variant blocks before its end."""
    if not names:
        return text, 0
    edits = []
    for a, b in blocks(text, "footprint"):
        item = text[a:b]
        ref = _reference(item)
        mine = parts.get(ref)
        if not mine:
            continue
        body = ""
        for n, e in mine.items():
            body += f"\n\t\t(variant (name {_q(n)})"
            if e["dnp"]:
                body += " (dnp yes)"
            for fname, fvalue in e["fields"].items():
                body += f" (field (name {_q(fname)}) (value {_q(fvalue)}))"
            body += ")"
        end = b - 1
        while end > a and text[end - 1] in " \t\n":
            end -= 1
        edits.append((end, body))
    head = "\n\t(variants" + "".join(
        f"\n\t\t(variant (name {_q(n)})" + (f" (description {_q(d)})" if d else "") + ")"
        for n, d in names) + "\n\t)"
    setup = next(iter(blocks(text, "setup")), None)
    at = setup[1] if setup else text.index("\n") + 1
    edits.append((at, head))
    for pos, body in sorted(edits, reverse=True):
        text = text[:pos] + body + text[pos:]
    return text, len(edits) - 1


def _reference(item: str) -> str:
    key = '(property "Reference" "'
    i = item.find(key)
    if i < 0:
        return ""
    j = item.find('"', i + len(key))
    return item[i + len(key):j]


def apply_project(pro_path: str, names: list[tuple[str, str]]) -> None:
    """The variant names in the project file, where KiCad lists them."""
    with open(pro_path, encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for n, d in names:
        row = {"name": n}
        if d:
            row["description"] = d
        rows.append(row)
    data.setdefault("schematic", {})["variants"] = rows
    from ...atomicfile import replace_with_retry
    tmp = pro_path + ".eda-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
    replace_with_retry(tmp, pro_path)
