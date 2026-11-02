# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read a saved PcbDoc's layer stack from the file, without Altium.

A PcbDoc's ``Board6/Data`` stream keeps the stack twice:

- ``V9_STACK_LAYERn_*``: the AD19+ physical stack, top to bottom, every
  layer its own entry (paste, overlay, solder mask, copper, each dielectric).
  This is what the Layer Stack Manager and the fab outputs work from.
- ``LAYERn_*`` with ``PREV`` / ``NEXT``: the pre-AD19 chain of copper layers,
  each carrying one "dielectric below". ``pcb_get_layer_stackup`` and
  ``pcb_modify_layer`` read and write this view through LayerStack_V7.

On a production board (AD 25.8.1, 2026-10-27) writes through the old view
read back as intended there, while the V9 stack held two dielectrics between
L1 and L2, one of them -0.39 mil thick. Reading the V9 records and comparing
the two views is how such a write is caught.
"""
from __future__ import annotations

import base64
import re
import zlib
from typing import Any, Optional

from ..units import MILS_PER_MM

#: Dielectric type numbers in the stack records.
DIELECTRIC_TYPES = {"0": "none", "1": "core", "2": "prepreg",
                    "3": "surface_material", "4": "embedded"}

_MASK_OVERLAY_PASTE = {
    0x1030006: "overlay", 0x1030007: "overlay",
    0x1030008: "paste", 0x1030009: "paste",
    0x103000A: "solder_mask", 0x103000B: "solder_mask",
}

#: Legacy copper layer numbers: 1 top, 2..31 mid, 32 bottom, 39..54 planes.
_LEGACY_COPPER = list(range(1, 33)) + list(range(39, 55))

_TOL_MILS = 0.05

#: Resistivity Altium stores for a non-conductive layer (MaxInt).
NON_CONDUCTIVE = 1e9


def layer_kind(layer_id: int) -> str:
    """Kind of a V9 stack entry from its layer id."""
    family = layer_id >> 16
    if family in (0x100, 0x101):
        return "copper"
    if family == 0x104:
        return "dielectric"
    return _MASK_OVERLAY_PASTE.get(layer_id, "other")


def to_mils(text: str) -> Optional[float]:
    """'1.378mil', '0.035mm', '35um' or a bare number (mils) to mils."""
    if text is None:
        return None
    m = re.fullmatch(r"\s*(-?[0-9.]+(?:[eE]-?\d+)?)\s*(mil|mm|um|in)?\s*", str(text))
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2) or "mil"
    return {"mil": value, "mm": value * MILS_PER_MM, "um": value * MILS_PER_MM / 1000.0,
            "in": value * 1000.0}[unit]


def board_keys(data: bytes) -> dict[str, str]:
    """The key/value pairs of the board record that holds the stack."""
    text = data.decode("latin-1")
    records = [r for r in re.split(r"\x00[\x00-\xff]{4}(?=\|)", text) if "V9_STACK" in r]
    source = records[0] if records else text
    keys: dict[str, str] = {}
    for pair in source.split("|"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            keys.setdefault(key.strip(), value.strip("\x00").strip())
    return keys


def read_board_data(path: str) -> bytes:
    import olefile

    with olefile.OleFileIO(path) as ole:
        return ole.openstream("Board6/Data").read()


def physical_stack(keys: dict[str, str]) -> list[dict[str, Any]]:
    """V9 stack entries top to bottom."""
    indexes = sorted({int(m.group(1)) for k in keys
                      for m in [re.fullmatch(r"V9_STACK_LAYER(\d+)_NAME", k)] if m})
    layers = []
    for i in indexes:
        def get(field: str) -> str:
            return keys.get(f"V9_STACK_LAYER{i}_{field}", "")
        try:
            layer_id = int(get("LAYERID"))
        except ValueError:
            layer_id = 0
        kind = layer_kind(layer_id)
        entry: dict[str, Any] = {"order": len(layers), "name": get("NAME"),
                                 "kind": kind, "layer_id": layer_id,
                                 "id": get("ID").strip("{}").lower()}
        if kind == "copper":
            entry["thickness_mils"] = to_mils(get("COPTHICK"))
            # Absent on ordinary copper. A printed-electronics additive
            # layer carries ISADDITIVELAYER=TRUE, and a non-conductive one
            # ELECTRICALRESISTIVITY=2147483647 (AD 25.8.1, 2026-10-27).
            entry["additive"] = get("ISADDITIVELAYER").upper() == "TRUE"
            try:
                entry["resistivity"] = float(get("ELECTRICALRESISTIVITY")) if get(
                    "ELECTRICALRESISTIVITY") else None
            except ValueError:
                entry["resistivity"] = None
        elif kind in ("dielectric", "solder_mask"):
            entry["thickness_mils"] = to_mils(get("DIELHEIGHT"))
            entry["dielectric_type"] = DIELECTRIC_TYPES.get(get("DIELTYPE"), get("DIELTYPE"))
            entry["material"] = get("DIELMATERIAL")
            try:
                entry["dielectric_constant"] = float(get("DIELCONST"))
            except ValueError:
                entry["dielectric_constant"] = None
        layers.append(entry)
    return layers


def legacy_chain(keys: dict[str, str]) -> list[dict[str, Any]]:
    """The pre-AD19 copper chain from LAYER1 by NEXT, each with its dielectric below."""
    present = {n for n in _LEGACY_COPPER if f"LAYER{n}NAME" in keys}
    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    n = 1 if 1 in present else 0
    while n and n not in seen and n in present:
        seen.add(n)
        chain.append({
            "legacy_layer": n,
            "name": keys.get(f"LAYER{n}NAME", ""),
            "copper_mils": to_mils(keys.get(f"LAYER{n}COPTHICK", "")),
            "dielectric_type": DIELECTRIC_TYPES.get(keys.get(f"LAYER{n}DIELTYPE", ""), ""),
            "dielectric_mils": to_mils(keys.get(f"LAYER{n}DIELHEIGHT", "")),
        })
        try:
            n = int(keys.get(f"LAYER{n}NEXT", "0"))
        except ValueError:
            n = 0
    return chain


def check(layers: list[dict[str, Any]], chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What is wrong with a stack, most serious first."""
    findings: list[dict[str, Any]] = []
    board = [layer for layer in layers if layer["kind"] in ("copper", "dielectric")]
    for layer in board:
        t = layer.get("thickness_mils")
        if t is None or t <= 0:
            findings.append({"severity": "error", "check": "thickness",
                             "layer": layer["name"],
                             "detail": f"{layer['kind']} thickness is {t} mil"})
    for layer in board:
        if layer["kind"] != "copper":
            continue
        if layer.get("additive"):
            findings.append({"severity": "error", "check": "additive",
                             "layer": layer["name"],
                             "detail": f"{layer['name']} is a printed-electronics additive "
                                       "layer: Altium then draws the board paper-thin in 3D "
                                       "with no drilled holes"})
        if (layer.get("resistivity") or 0) >= NON_CONDUCTIVE:
            findings.append({"severity": "error", "check": "non_conductive",
                             "layer": layer["name"],
                             "detail": f"{layer['name']} has resistivity "
                                       f"{layer['resistivity']:g}, i.e. non-conductive"})
    for upper, lower in zip(board, board[1:]):
        if upper["kind"] == "copper" and lower["kind"] == "copper":
            findings.append({"severity": "error", "check": "no_dielectric",
                             "layer": upper["name"],
                             "detail": f"no dielectric between {upper['name']} and {lower['name']}"})
    copper = [layer for layer in layers if layer["kind"] == "copper"]
    if chain and [c["name"] for c in chain] != [c["name"] for c in copper]:
        findings.append({"severity": "error", "check": "views_disagree",
                         "detail": "copper order differs between the physical stack "
                                   f"{[c['name'] for c in copper]} and the legacy chain "
                                   f"{[c['name'] for c in chain]}"})
    elif chain:
        positions = [layers.index(c) for c in copper]
        for i, (top, bottom) in enumerate(zip(positions, positions[1:])):
            gap = [layers[j] for j in range(top + 1, bottom) if layers[j]["kind"] == "dielectric"]
            v9 = sum((d.get("thickness_mils") or 0.0) for d in gap)
            legacy = chain[i].get("dielectric_mils")
            if legacy is not None and abs(v9 - legacy) > _TOL_MILS:
                findings.append({"severity": "error", "check": "views_disagree",
                                 "layer": copper[i]["name"],
                                 "detail": f"below {copper[i]['name']} the physical stack has "
                                           f"{round(v9, 4)} mil of dielectric in {len(gap)} "
                                           f"layer(s); the legacy view says {legacy} mil"})
            if len(gap) > 1:
                findings.append({"severity": "info", "check": "split_dielectric",
                                 "layer": copper[i]["name"],
                                 "detail": f"{len(gap)} dielectrics below {copper[i]['name']}: "
                                           + ", ".join(f"{d['name']} {d.get('thickness_mils')} mil"
                                                       for d in gap)})
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(findings, key=lambda f: order[f["severity"]])


#: The Layer Stack Manager's own copy of the stack: base64 of zlib of an XML
#: StackupDocument, per layer GUID (thickness, material, Dk, Df, weight).
MANAGER_MODEL_KEY = "V9_STACKCUSTOMDATA"


def manager_model(keys: dict[str, str]) -> list[dict[str, Any]] | None:
    """The Layer Stack Manager's layers from the file, None when it has none.

    Script writes to the stack update the V9, V8 and legacy records but not
    this XML, so the manager went on showing the old values for every layer
    a rebuild kept (AD 25.8.1, 2026-10-27).
    """
    blob = keys.get(MANAGER_MODEL_KEY)
    if not blob:
        return None
    try:
        xml = zlib.decompress(base64.b64decode(blob)).decode("utf-8", "replace")
    except Exception:                                   # noqa: BLE001
        return None
    out = []
    for m in re.finditer(r'<Layer Id="([^"]+)" TypeId="[^"]+" Name="([^"]*)"(.*?)</Layer>',
                         xml, re.S):
        body = m.group(3)

        def prop(name):
            p = re.search(rf'Name="{name}"[^>]*>([^<]*)<', body)
            return p.group(1) if p else None

        dk = prop("DielectricConstant")
        out.append({"id": m.group(1).lower(), "name": m.group(2),
                    "thickness_mils": to_mils(prop("Thickness") or ""),
                    "material": (prop("Material") or "").split(":")[-1] or None,
                    "dielectric_constant": float(dk) if dk else None})
    return out


def check_manager_model(layers: list[dict[str, Any]],
                        model: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Where the Layer Stack Manager's copy disagrees with the real stack."""
    if not model:
        return []
    by_id = {m["id"]: m for m in model}
    stale = []
    for layer in layers:
        if layer["kind"] not in ("copper", "dielectric"):
            continue
        m = by_id.get(layer.get("id", ""))
        if m is None:
            continue
        t, mt = layer.get("thickness_mils"), m.get("thickness_mils")
        if t is not None and mt is not None and abs(t - mt) > _TOL_MILS:
            stale.append(f"{layer['name']}: {mt:g} mil in the manager, {t:g} mil in the stack")
    if not stale:
        return []
    return [{"severity": "warning", "check": "stack_manager_model_stale",
             "detail": ("the Layer Stack Manager's own model (" + MANAGER_MODEL_KEY
                        + ") disagrees with the stack: " + "; ".join(stale)
                        + ". Close the board in Altium and run "
                          "pcb_reset_stack_manager_model, then reopen and save"),
             "layers": stale}]


def reset_manager_model(path: str, dry_run: bool = True) -> dict[str, Any]:
    """Make Altium rebuild the Layer Stack Manager's model from the stack.

    Renames the key to X9_STACKCUSTOMDATA in place, the same length, so the
    stream keeps its size. On the next load Altium ignores it and builds
    the manager's model from the V9 records; the next save drops it. Done
    on a production board on 2026-10-27 after a scratch test. The board must
    be closed in Altium, or Altium writes its own copy back over this.
    """
    import olefile

    old = ("|" + MANAGER_MODEL_KEY + "=").encode("latin-1")
    new = ("|X" + MANAGER_MODEL_KEY[1:] + "=").encode("latin-1")
    with olefile.OleFileIO(path) as ole:
        data = ole.openstream("Board6/Data").read()
    count = data.count(old)
    out = {"path": path, "found": count, "dry_run": dry_run}
    if count == 0:
        out["note"] = "no Layer Stack Manager model in the file; nothing to do"
        return out
    if dry_run:
        return out
    with olefile.OleFileIO(path, write_mode=True) as ole:
        ole.write_stream("Board6/Data", data.replace(old, new))
    with olefile.OleFileIO(path) as ole:
        out["renamed"] = ole.openstream("Board6/Data").read().count(new)
    out["next"] = "open the board in Altium and save it"
    return out


def summarize(layers: list[dict[str, Any]]) -> dict[str, Any]:
    copper = [layer for layer in layers if layer["kind"] == "copper"]
    dielectric = [layer for layer in layers if layer["kind"] == "dielectric"]
    mask = [layer for layer in layers if layer["kind"] == "solder_mask"]

    def total(group):
        return round(sum((layer.get("thickness_mils") or 0.0) for layer in group), 4)

    return {"copper_layers": len(copper), "dielectric_layers": len(dielectric),
            "copper_mils": total(copper), "dielectric_mils": total(dielectric),
            "board_mils": total(copper + dielectric),
            "with_mask_mils": total(copper + dielectric + mask)}


def read_stack(path: str) -> dict[str, Any]:
    """The saved stack of a PcbDoc: layers, legacy chain, summary, findings."""
    keys = board_keys(read_board_data(path))
    layers = physical_stack(keys)
    chain = legacy_chain(keys)
    findings = check(layers, chain) + check_manager_model(layers, manager_model(keys))
    return {"path": path, "layers": layers, "legacy_chain": chain,
            "summary": summarize(layers), "findings": findings,
            "ok": not any(f["severity"] == "error" for f in findings)}


def compare_expected(layers: list[dict[str, Any]], expected: list[dict[str, Any]],
                     tol_mils: float = _TOL_MILS) -> list[dict[str, Any]]:
    """Differences between the copper and dielectric layers and an expected list.

    ``expected`` is top to bottom, each ``{"kind": "copper"|"dielectric",
    "name"?, "thickness_mils"?, "dielectric_type"?, "dielectric_constant"?}``.
    """
    board = [layer for layer in layers if layer["kind"] in ("copper", "dielectric")]
    out: list[dict[str, Any]] = []
    if len(board) != len(expected):
        out.append({"check": "count", "detail": f"{len(board)} copper and dielectric "
                    f"layers, expected {len(expected)}"})
    for i, (got, want) in enumerate(zip(board, expected)):
        for field in ("kind", "name", "dielectric_type"):
            if field in want and str(want[field]) != str(got.get(field)):
                out.append({"check": field, "order": i, "got": got.get(field),
                            "expected": want[field]})
        for field, tol in (("thickness_mils", tol_mils), ("dielectric_constant", 0.01)):
            if field in want:
                g = got.get(field)
                if g is None or abs(float(g) - float(want[field])) > tol:
                    out.append({"check": field, "order": i, "layer": got.get("name"),
                                "got": g, "expected": want[field]})
    return out
