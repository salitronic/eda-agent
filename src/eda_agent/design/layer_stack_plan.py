# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Turn a wanted layer stack into the edits that produce it on a board.

The board is changed through Altium's physical stack, the model in which a
dielectric is a layer of its own. Facts it rests on, seen on AD 25.8 and
AD 26.11:

* the older per-copper stack writes read back fine but saved a broken
  stack (AD 25.8);
* copper added through the older model lands under the dielectric of the
  layer named, and Altium puts a new dielectric under it by itself;
* on the physical stack, names, copper thickness and a dielectric's own
  fields write and read back, and a layer can be removed;
* no script call makes a dielectric (AD 25.8), so each gap between copper
  layers can only keep or lose what Altium already put there.

The edits come in two rounds. First the copper: outer layers renamed in
place, inner layers kept when the wanted stack has the same kind in the
same order (a plane keeps whatever is poured on it), the rest dropped, new
ones added from the top down. Then, once that stack is read back, the
dielectrics in each gap are trimmed to the wanted count and set. Every
rename passes through a placeholder name so swapped names never clash.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The numbers Altium gives the kinds of dielectric.
DIELECTRIC_KINDS = {"none": 0, "core": 1, "prepreg": 2, "surface_material": 3}
PLACEHOLDER = "~edastack"


class StackError(ValueError):
    """The wanted stack cannot be made on this board."""


@dataclass
class Wanted:
    """One layer of the wanted stack."""
    copper: bool
    name: str
    thickness_mils: float
    plane: bool = False
    extra: dict[str, Any] = field(default_factory=dict)   # dielectric fields

    def describe(self) -> dict[str, Any]:
        row = {"kind": "copper" if self.copper else "dielectric", "name": self.name,
               "thickness_mils": self.thickness_mils}
        if self.copper:
            row["plane"] = self.plane
        row.update(self.extra)
        return row


def _kind_number(value: Any) -> int:
    if isinstance(value, bool):
        raise StackError(f"dielectric type {value!r} is not a type")
    if isinstance(value, int):
        return value
    word = str(value).strip().lower()
    if word.isdigit():
        return int(word)
    try:
        return DIELECTRIC_KINDS[word]
    except KeyError:
        raise StackError(f"dielectric type {value!r}: core, prepreg or Altium's number") from None


def read_wanted(rows: Any) -> list[Wanted]:
    """The wanted stack, top to bottom, checked. Unnamed dielectrics get
    "Dielectric N" names."""
    if not isinstance(rows, list) or len(rows) < 3:
        raise StackError("a stack needs at least copper, dielectric and copper")
    stack: list[Wanted] = []
    names: set[str] = set()
    dielectrics = 0
    for at, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise StackError(f"row {at} is not an object")
        kind = str(row.get("kind", "")).strip().lower()
        if kind not in ("copper", "dielectric"):
            raise StackError(f"row {at}: kind is copper or dielectric, not {kind!r}")
        try:
            thick = float(row["thickness_mils"])
        except (KeyError, TypeError, ValueError):
            raise StackError(f"row {at}: give thickness_mils") from None
        if thick <= 0:
            raise StackError(f"row {at}: thickness_mils is above zero")
        name = str(row.get("name") or "").strip()
        if kind == "copper":
            if not name:
                raise StackError(f"row {at}: a copper layer needs its name")
            layer = Wanted(True, name, thick, plane=bool(row.get("plane")))
        else:
            dielectrics += 1
            extra: dict[str, Any] = {"type": _kind_number(row.get("type", "prepreg"))}
            for key in ("constant", "loss_tangent"):
                if row.get(key) is not None:
                    extra[key] = float(row[key])
            if row.get("material"):
                extra["material"] = str(row["material"])
            layer = Wanted(False, name or f"Dielectric {dielectrics}", thick, extra=extra)
        if layer.name.upper() in names:
            raise StackError(f"row {at}: the name {layer.name!r} is already used")
        if layer.name.startswith(PLACEHOLDER):
            raise StackError(f"row {at}: names starting {PLACEHOLDER} are reserved")
        names.add(layer.name.upper())
        stack.append(layer)
    if not (stack[0].copper and stack[-1].copper):
        raise StackError("the stack has copper at the top and the bottom")
    if stack[0].plane or stack[-1].plane:
        raise StackError("the outer copper layers are signal layers, not planes")
    for above, below in zip(stack, stack[1:]):
        if above.copper and below.copper:
            raise StackError(f"{above.name} and {below.name} need a dielectric between them")
    return stack


def between_outer_copper(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The copper and dielectric rows from the top copper to the bottom
    copper, as pcb_physical_stack lists them (masks and overlays dropped)."""
    kept = [r for r in rows if r.get("kind") in ("copper", "dielectric")]
    while kept and kept[0].get("kind") != "copper":
        kept.pop(0)
    while kept and kept[-1].get("kind") != "copper":
        kept.pop()
    return kept


def is_plane(layer_id: Any, name: str = "") -> bool:
    """Planes are ids 39-54 in the old numbering, 0x101xxxx in AD19's."""
    try:
        number = int(layer_id)
    except (TypeError, ValueError):
        return "PLANE" in name.upper()
    return 39 <= number <= 54 or (number >> 16) == 0x101


def spare_slots(copper: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Signal (MidLayerN) and plane (InternalPlaneN) slots no layer uses."""
    used_mid: set[int] = set()
    used_plane: set[int] = set()
    for row in copper:
        try:
            number = int(row.get("id", row.get("layer_id")))
            if 2 <= number <= 31:
                used_mid.add(number - 1)
            elif 39 <= number <= 54:
                used_plane.add(number - 38)
        except (TypeError, ValueError):
            pass
        found = re.fullmatch(r"(MID-?LAYER|INTERNAL ?PLANE)\s*(\d+)",
                             str(row.get("name", "")).strip().upper())
        if found:
            (used_plane if "PLANE" in found.group(1) else used_mid).add(int(found.group(2)))
    return ([f"MidLayer{n}" for n in range(1, 31) if n not in used_mid],
            [f"InternalPlane{n}" for n in range(1, 17) if n not in used_plane])


def copper_round(board_rows: list[dict[str, Any]], wanted: list[Wanted],
                 copper: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The first round of edits: the copper layers.

    ``board_rows`` are pcb_physical_stack's rows; ``copper`` is the copper
    list (id, whether anything lies on it) the board reports.
    """
    span = between_outer_copper(board_rows)
    if sum(1 for r in span if r["kind"] == "copper") < 2:
        raise StackError("the board needs a top and a bottom copper layer to start from")
    facts = {str(c.get("name", "")).upper(): c for c in copper}
    have_inner = [r["name"] for r in span if r["kind"] == "copper"][1:-1]
    want_inner = [w for w in wanted if w.copper][1:-1]

    # Keep as many board layers as possible, in order, each taken by a
    # wanted layer of the same kind (the longest common run of kinds);
    # whatever is left over is dropped.
    have_planes = [is_plane(facts.get(n.upper(), {}).get("id"), n) for n in have_inner]
    rows, cols = len(want_inner), len(have_inner)
    best = [[0] * (cols + 1) for _ in range(rows + 1)]
    for w in range(rows - 1, -1, -1):
        for h in range(cols - 1, -1, -1):
            same = want_inner[w].plane == have_planes[h]
            best[w][h] = max(best[w + 1][h], best[w][h + 1],
                             best[w + 1][h + 1] + 1 if same else 0)
    reuse: dict[int, str] = {}
    w = h = 0
    while w < rows and h < cols:
        if want_inner[w].plane == have_planes[h] and best[w][h] == best[w + 1][h + 1] + 1:
            reuse[w] = have_inner[h]
            w, h = w + 1, h + 1
        elif best[w + 1][h] >= best[w][h + 1]:
            w += 1
        else:
            h += 1
    leftover = [n for n in have_inner if n not in reuse.values()]
    occupied = [n for n in leftover if facts.get(n.upper(), {}).get("occupied", True)]
    if occupied:
        raise StackError("these layers would be dropped but objects lie on them: "
                         + ", ".join(occupied)
                         + "; plan a layer of the same kind there, or clear them")

    edits: list[dict[str, Any]] = [{"do": "drop", "layer": n} for n in leftover]
    outer = [w for w in wanted if w.copper]
    pairs = [(span[0]["name"], outer[0]), (span[-1]["name"], outer[-1])]
    pairs += [(reuse[i], want_inner[i]) for i in sorted(reuse)]
    edits += [{"do": "rename", "layer": old, "to": f"{PLACEHOLDER}{n}"}
              for n, (old, _) in enumerate(pairs)]
    edits += [{"do": "rename", "layer": f"{PLACEHOLDER}{n}", "to": layer.name,
               "thickness_mils": layer.thickness_mils} for n, (_, layer) in enumerate(pairs)]

    mids, planes = spare_slots([c for c in copper if c.get("name") not in leftover])
    under = outer[0].name
    for index, layer in enumerate(want_inner):
        if index not in reuse:
            slots = planes if layer.plane else mids
            if not slots:
                raise StackError(f"no spare {'plane' if layer.plane else 'signal'} "
                                 f"slot is left for {layer.name}")
            edits.append({"do": "add", "slot": slots.pop(0), "under": under,
                          "name": layer.name, "thickness_mils": layer.thickness_mils})
        under = layer.name
    return edits


def _per_gap(rows: list[Any], is_copper) -> list[list[Any]]:
    gaps: list[list[Any]] = []
    for row in rows:
        if is_copper(row):
            gaps.append([])
        elif gaps:
            gaps[-1].append(row)
    return gaps[:-1]


def dielectric_round(board_rows: list[dict[str, Any]], wanted: list[Wanted]
                     ) -> list[dict[str, Any]]:
    """The second round of edits: fit each gap's dielectrics to the wanted ones."""
    span = between_outer_copper(board_rows)
    have_copper = [r["name"].upper() for r in span if r["kind"] == "copper"]
    want_copper = [w.name for w in wanted if w.copper]
    if have_copper != [n.upper() for n in want_copper]:
        raise StackError(f"the copper reads {have_copper}, not {want_copper}")
    drops, holds, sets = [], [], []
    have_gaps = _per_gap(span, lambda r: r["kind"] == "copper")
    want_gaps = _per_gap(wanted, lambda w: w.copper)
    for gap, (have, want) in enumerate(zip(have_gaps, want_gaps)):
        if len(have) < len(want):
            raise StackError(f"{want_copper[gap]} to {want_copper[gap + 1]}: the board has "
                             f"{len(have)} dielectric(s), the stack wants {len(want)}, and "
                             "a script cannot add one")
        drops += [{"do": "drop", "layer": row["name"]} for row in have[len(want):]]
        for n, (row, layer) in enumerate(zip(have, want)):
            hold = f"{PLACEHOLDER}d{gap}_{n}"
            holds.append({"do": "dielectric", "layer": row["name"], "to": hold})
            sets.append({"do": "dielectric", "layer": hold, "to": layer.name,
                         "thickness_mils": layer.thickness_mils, **layer.extra})
    return drops + holds + sets


def mismatches(board_rows: list[dict[str, Any]], wanted: list[Wanted]) -> list[dict[str, Any]]:
    """How the board's stack differs from the wanted one; empty when it matches."""
    span = between_outer_copper(board_rows)
    if len(span) != len(wanted):
        return [{"what": "layer list", "board": [r.get("name") for r in span],
                 "wanted": [w.name for w in wanted]}]
    found: list[dict[str, Any]] = []
    for row, layer in zip(span, wanted):
        kind = "copper" if layer.copper else "dielectric"
        if row.get("kind") != kind or str(row.get("name", "")).upper() != layer.name.upper():
            found.append({"what": "layer", "board": [row.get("kind"), row.get("name")],
                          "wanted": [kind, layer.name]})
            continue
        thick = row.get("thickness_mils")
        if thick is None or abs(float(thick) - layer.thickness_mils) > 0.01:
            found.append({"what": "thickness_mils", "layer": layer.name, "board": thick,
                          "wanted": layer.thickness_mils})
        if layer.copper:
            if row.get("additive"):
                found.append({"what": "additive", "layer": layer.name, "board": True,
                              "wanted": False})
            continue
        board_type = row.get("dielectric_type")
        if board_type is not None and int(board_type) != layer.extra["type"]:
            found.append({"what": "type", "layer": layer.name, "board": board_type,
                          "wanted": layer.extra["type"]})
        for key, tol in (("constant", 1e-3), ("loss_tangent", 1e-5)):
            if key in layer.extra and (row.get(key) is None
                                       or abs(float(row[key]) - layer.extra[key]) > tol):
                found.append({"what": key, "layer": layer.name, "board": row.get(key),
                              "wanted": layer.extra[key]})
    return found
