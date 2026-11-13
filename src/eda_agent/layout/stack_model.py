# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>
"""Every layer stack of a board, read from the Layer Stack Manager's model.

A rigid-flex board has more than one layer stack: one per kind of board
region. Each stack has its own layers, via spans, drill spans and its own
transmission line in every impedance profile. The script API reaches only
the first stack's layers and spans: a stack's ``Layers`` collection
lists every layer of the board, and a layer's id stops the script. The
manager's model (pcb.layer_stack_manager include_model="xml", the
StackupDocument XML) holds each stack whole, so the stacks are read from it.

Pure functions; nothing here talks to Altium.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

#: The manager's layer type ids, named as pcb.layer_stack_manager names them
#: (PCB.pas LsmTypeName). A flex stack's coverlay is a solder mask layer. Any
#: other id is "other", with the id itself in type_id.
LAYER_TYPES = {
    "f4eccd87-2cfb-4f37-be50-4f3a272b4d01": "copper",
    "136c62ef-1fa6-4897-ae71-7e797b632b92": "core",
    "1a79611a-039d-4d40-a204-53c26c50f8b5": "prepreg",
    "92b02d5e-8d69-48a8-880e-ac4b77db099d": "dielectric",
    "7b384237-13d8-4318-8bcb-accd8d9a51e7": "solder_mask",
    "c7ef040e-8d00-490b-b00c-a7e7823ff174": "overlay",
}

#: A length in the model: a number and a unit.
_LENGTH = re.compile(r"^\s*(-?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s*([a-zA-Zµμ]*)\s*$")
_MILS_PER = {"mil": 1.0, "mils": 1.0, "in": 1000.0, "inch": 1000.0,
             "mm": 1000.0 / 25.4, "um": 1.0 / 25.4, "µm": 1.0 / 25.4,
             "μm": 1.0 / 25.4, "m": 1e6 / 25.4, "cm": 1e4 / 25.4}


def to_mils(text: Optional[str]) -> Optional[float]:
    """A model length ("6.063mil", "0.1mm", "0m") in mils; None when not one."""
    m = _LENGTH.match(text or "")
    if not m:
        return None
    unit = m.group(2) or "mil"
    if unit not in _MILS_PER:
        return None
    return round(float(m.group(1)) * _MILS_PER[unit], 6)


def _number(text: Optional[str]) -> Optional[float]:
    """The number at the front of "50ohm", "10%", "1.58%"; None when none."""
    m = re.match(r"^\s*(-?[0-9.]+(?:[eE][-+]?[0-9]+)?)", text or "")
    return float(m.group(1)) if m else None


def _tag(e: ET.Element) -> str:
    return e.tag.split("}")[-1]


def _children(e: Optional[ET.Element], name: str) -> list[ET.Element]:
    return [] if e is None else [c for c in e if _tag(c) == name]


def _child(e: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    found = _children(e, name)
    return found[0] if found else None


def _key(guid: Optional[str]) -> str:
    """A layer / stack id compared without case or braces."""
    return (guid or "").strip("{}").lower()


def _flag(e: ET.Element, name: str, default: bool) -> bool:
    v = e.get(name)
    return default if v is None else v.strip().lower() == "true"


def _layer(e: ET.Element) -> dict[str, Any]:
    props = {p.get("Name"): (p.text or "") for p in _children(_child(e, "Properties"), "Property")}
    return {
        "name": e.get("Name", ""),
        "guid": _key(e.get("Id")),
        "type": LAYER_TYPES.get(_key(e.get("TypeId")), "other"),
        "type_id": _key(e.get("TypeId")),
        "enabled": _flag(e, "IsEnabled", True),
        "shared": _flag(e, "IsShared", False),
        "material": props.get("Material", ""),
        "thickness_mils": to_mils(props.get("Thickness")),
        "dielectric_constant": props.get("DielectricConstant", ""),
        "loss_tangent": props.get("LossTangent", ""),
        "weight": props.get("Weight", ""),
    }


def _root(model_xml: str) -> ET.Element:
    start = model_xml.find("<")
    if start < 0:
        raise ValueError("no XML in the model")
    return ET.fromstring(model_xml[start:])


def read_stack_model(model_xml: str) -> dict[str, Any]:
    """Every stack, the board's branch sections and the impedance lines.

    ``stacks``: one per stack in the model's order (the order of the board's
    substacks): name, id, flex, region (a board-region stack rather than
    the board's own), layers (name, guid, type, enabled, shared, material,
    thickness_mils, dielectric_constant, loss_tangent, weight), via_spans
    and drill_spans (name, type, start_layer, stop_layer).

    ``branches``: name and sections (name, parent, stacks by name): which
    stack each section of the board uses.

    ``impedance_lines``: per profile (name, type, impedance_ohms,
    tolerance_percent) its lines (stack, layer, line_type, top_reference,
    bottom_reference, trace_width_mils, trace_gap_mils, impedance_error,
    impedance_ohms).
    """
    root = _root(model_xml)
    stackup = root if _tag(root) == "Stackup" else next(
        (e for e in root.iter() if _tag(e) == "Stackup"), root)

    names: dict[str, str] = {}
    stack_names: dict[str, str] = {}
    raw = _children(_child(stackup, "Stacks"), "Stack")
    for st in raw:
        stack_names[_key(st.get("Id"))] = st.get("Name", "")
        for lay in _children(_child(st, "Layers"), "Layer"):
            names.setdefault(_key(lay.get("Id")), lay.get("Name", ""))

    def span(e: ET.Element) -> dict[str, Any]:
        return {"name": e.get("CustomName") or e.get("AutoName", ""),
                "type": e.get("Type", ""),
                "start_layer": names.get(_key(e.get("StartLayerId")), ""),
                "stop_layer": names.get(_key(e.get("StopLayerId")), "")}

    stacks = []
    for st in raw:
        stacks.append({
            "name": st.get("Name", ""),
            "id": _key(st.get("Id")),
            "flex": _flag(st, "IsFlex", False),
            "region": st.get("Type", "") == "BoardRegionLayerStack",
            "layers": [_layer(l) for l in _children(_child(st, "Layers"), "Layer")],
            "via_spans": [span(v) for v in _children(_child(st, "ViaSpans"), "ViaSpan")],
            "drill_spans": [span(d) for d in _children(_child(st, "DrillSpans"), "DrillSpan")],
        })

    branches = []
    for br in _children(_child(stackup, "Branches"), "Branch"):
        sections = _children(_child(br, "Sections"), "BranchSection")
        sect_names = {_key(s.get("Id")): s.get("Name", "") for s in sections}
        branches.append({
            "name": br.get("Name", ""),
            "sections": [{
                "name": s.get("Name", ""),
                "parent": sect_names.get(_key(s.get("ParentSectionId")), ""),
                "stacks": [stack_names.get(_key(b.get("LayerStackId")), "")
                           for b in _children(_child(s, "Stacks"), "BranchSectionStack")],
            } for s in sections]})

    profiles = []
    for pr in _children(_child(stackup, "ImpedanceProfiles"), "ImpedanceProfile"):
        lines = []
        for tl in _children(_child(pr, "TransmissionLines"), "TransmissionLine"):
            elec = _child(tl, "ElectricParameters")
            z = elec.get("Z") if elec is not None else None
            if z is None and elec is not None:
                z = elec.get("Zdiff") or elec.get("Zodd")
            lines.append({
                "stack": stack_names.get(_key(tl.get("StackId")), ""),
                "layer": names.get(_key(tl.get("LayerId")), ""),
                "line_type": tl.get("TLTypeName", ""),
                "top_reference": names.get(_key(tl.get("TopRefLayerId")), ""),
                "bottom_reference": names.get(_key(tl.get("BottomRefLayerId")), ""),
                "trace_width_mils": to_mils(tl.get("TraceWidth")),
                "trace_gap_mils": to_mils(tl.get("TraceGap")),
                "impedance_error": _number(tl.get("ImpedanceError")),
                "impedance_ohms": _number(z),
            })
        profiles.append({"name": pr.get("CustomName") or pr.get("AutoName", ""),
                         "type": pr.get("Type", ""),
                         "impedance_ohms": _number(pr.get("Impedance")),
                         "tolerance_percent": _number(pr.get("Tolerance")),
                         "lines": lines})

    return {"stacks": stacks, "branches": branches, "impedance_lines": profiles}


def drill_pair_stacks(pairs: list[dict[str, Any]],
                      stacks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give each drill pair the stack(s) whose via span runs between its layers.

    A pair's layers are matched to a span's start and stop layer by name,
    either way round. A pair no span covers gets an empty list. A pair the
    script could not name (the default stack's list has no pair of another
    stack) takes the name of the span that covers it.
    """
    for pair in pairs:
        ends = {pair.get("start_layer_name") or "", pair.get("stop_layer_name") or ""}
        spans = [(st["name"], sp) for st in stacks
                 for sp in st["via_spans"] + st["drill_spans"]
                 if {sp["start_layer"], sp["stop_layer"]} == ends]
        pair["stacks"] = list(dict.fromkeys(name for name, _ in spans))
        if not pair.get("name") and spans:
            pair["name"] = spans[0][1]["name"]
    return pairs


def impedance_by_stack(profiles: list[dict[str, Any]],
                       model: dict[str, Any]) -> list[dict[str, Any]]:
    """Name each pcb.get_impedance_profiles row's stack, drop layers it lacks.

    A row carries ``stack_index`` (the board's substack it was read from,
    the model's stack order). A substack answers for every copper layer of
    the board, including ones it does not have (a flex stack's outer
    layers read enabled with width 0): a row is kept only when its stack
    has that layer. A kept row gets its stack name and, where the profile
    has a line there, the line type and the reference layers (empty for a
    layer the profile is switched off on). Profiles are matched by
    position, then by name.
    """
    stacks = model["stacks"]
    layers_of = [{l["name"] for l in st["layers"]} for st in stacks]
    by_name = {p["name"]: p for p in model["impedance_lines"]}
    for i, prof in enumerate(profiles):
        lines = (model["impedance_lines"][i]["lines"]
                 if i < len(model["impedance_lines"])
                 and model["impedance_lines"][i]["name"] == prof.get("name")
                 else by_name.get(prof.get("name"), {}).get("lines", []))
        index = {(ln["stack"], ln["layer"]): ln for ln in lines}
        kept = []
        for row in prof.get("layers") or []:
            k = row.get("stack_index", 0)
            if not (isinstance(k, int) and 0 <= k < len(stacks)):
                continue
            if row.get("layer") not in layers_of[k]:
                continue
            row["stack"] = stacks[k]["name"]
            line = index.get((row["stack"], row.get("layer"))) or {}
            row["line_type"] = line.get("line_type", "")
            row["top_reference"] = line.get("top_reference", "")
            row["bottom_reference"] = line.get("bottom_reference", "")
            kept.append(row)
        prof["layers"] = kept
    return profiles
