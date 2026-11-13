# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What the conversion reads from the Altium board, through the bridge.

The importer brings no stack-up, sets every zone's clearance to 0.5 mm and
leaves rules and net classes behind. They are read from the open board in
Altium (never from the .PcbDoc file) and turned into KiCad's terms here:
board design settings, a ``(stackup)`` block, net classes for the project
file, and a ``.kicad_dru`` for the rules whose scope translates. A rule
whose scope does not translate is listed, not guessed.

The parsing is pure (replies in, settings out) so it is tested without
Altium; ``read_board`` is the only part that talks to the bridge.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ...units import MM_PER_MIL as MIL

_ALL = {"all", ""}


def _mils(text: str, key: str) -> Optional[float]:
    m = re.search(re.escape(key) + r"\s*=\s*(-?[\d.]+)\s*mil", text, re.I)
    return float(m.group(1)) * MIL if m else None


def _general(rule: dict) -> bool:
    return (str(rule.get("scope_1", "")).strip().lower() in _ALL
            and str(rule.get("scope_2", "All")).strip().lower() in _ALL)


def board_rules(rules: list[dict[str, Any]]) -> dict[str, float]:
    """The board-wide (All, All) values KiCad keeps as design settings."""
    out: dict[str, float] = {}
    for r in rules or []:
        if not r.get("enabled", True) or not _general(r):
            continue
        d = str(r.get("descriptor") or "")
        if d.startswith("Clearance Constraint"):
            gap = r.get("gap_mm") or _mils(d, "Gap")
            if gap:
                out["clearance_mm"] = round(float(gap), 4)
        elif d.startswith("Width Constraint"):
            if _mils(d, "Min") is not None:
                out["track_min_mm"] = round(_mils(d, "Min"), 4)
            if _mils(d, "Preferred") is not None:
                out["track_mm"] = round(_mils(d, "Preferred"), 4)
        elif d.startswith("Routing Via"):
            if _mils(d, "PreferredHoleWidth") is not None:
                out["via_drill_mm"] = round(_mils(d, "PreferredHoleWidth"), 4)
            width = _mils(d, "PreferedWidth") or _mils(d, "PreferredWidth")
            if width is not None:
                out["via_diameter_mm"] = round(width, 4)
            if _mils(d, "MinHoleWidth") is not None:
                out["via_min_drill_mm"] = round(_mils(d, "MinHoleWidth"), 4)
            if _mils(d, "MinWidth") is not None:
                out["via_min_diameter_mm"] = round(_mils(d, "MinWidth"), 4)
        elif d.startswith("Hole To Hole Clearance"):
            gap = r.get("gap_mm") or _mils(d, "Gap")
            if gap:
                out["hole_to_hole_mm"] = round(float(gap), 4)
        elif "Board Outline Clearance" in d or r.get("name", "").lower().startswith("boardoutline"):
            if r.get("gap_mm"):
                out["edge_clearance_mm"] = round(float(r["gap_mm"]), 4)
        elif d.startswith("Solder Mask Expansion"):
            e = _mils(d, "Expansion ")
            if e is None:
                e = _mils(d, "Expansion")
            if e is not None:
                out["mask_expansion_mm"] = round(e, 4)
    return out


#: Altium dielectric types to KiCad's stack-up names.
_DIELECTRIC = {"core": "core", "prepreg": "prepreg"}


def copper_names(count: int) -> list[str]:
    """KiCad's copper layers, top to bottom, for ``count`` layers."""
    if count < 2:
        return ["F.Cu"]
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, count - 1)] + ["B.Cu"]


def stackup_block(layers: list[dict[str, Any]]) -> tuple[str, float]:
    """KiCad's ``(stackup ...)`` text from the Altium stack-up, and the
    board thickness it adds up to (copper and dielectric, mm)."""
    ordered = sorted(layers or [], key=lambda l: l.get("order", 0))
    names = copper_names(len(ordered))
    rows = ['(layer "F.SilkS" (type "Top Silk Screen"))',
            '(layer "F.Paste" (type "Top Solder Paste"))',
            '(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))']
    total = 0.0
    dielectric = 0
    for layer, name in zip(ordered, names):
        cu = round(float(layer.get("copper_thickness_mils") or 0) * MIL, 4)
        total += cu
        rows.append(f'(layer "{name}" (type "copper") (thickness {cu}))')
        kind = _DIELECTRIC.get(str(layer.get("dielectric_type") or "").strip().lower())
        height = round(float(layer.get("dielectric_height_mils") or 0) * MIL, 4)
        if kind and height > 0 and name != "B.Cu":
            dielectric += 1
            total += height
            er = layer.get("dielectric_constant") or 4.5
            rows.append(f'(layer "dielectric {dielectric}" (type "{kind}") (thickness {height}) '
                        f'(material "FR4") (epsilon_r {er}) (loss_tangent 0.02))')
    rows += ['(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))',
             '(layer "B.Paste" (type "Bottom Solder Paste"))',
             '(layer "B.SilkS" (type "Bottom Silk Screen"))',
             '(copper_finish "None")', '(dielectric_constraints no)']
    body = "\n\t\t\t".join(rows)
    return f"(stackup\n\t\t\t{body}\n\t\t)", round(total + 0.02, 4)


def net_classes(classes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The user net classes with their nets (Altium's built-in classes left out)."""
    return [{"name": c["name"], "nets": list(c.get("nets") or [])}
            for c in classes or [] if not c.get("super_class") and c.get("name")]


_TOKENS = re.compile(r"\s*(\(|\)|[A-Za-z_]\w*\s*\(\s*'[^']*'\s*\)|[A-Za-z_]\w*)", re.S)
_CALL = re.compile(r"^([A-Za-z_]\w*)\s*\(\s*'([^']*)'\s*\)$")
#: Altium object tests with a KiCad equivalent. Anything else (IsKeepOut,
#: a query function with no counterpart) leaves the rule unported.
_PLAIN = {"ispad": "{s}.Type == 'Pad'", "isvia": "{s}.Type == 'Via'",
          "istrack": "{s}.Type == 'Track'", "isarc": "{s}.Type == 'Arc'",
          "isregion": "{s}.Type == 'Zone'", "isfill": "{s}.Type == 'Zone'",
          "inanynet": "{s}.NetName != ''", "all": "true"}


def kicad_condition(scope: str, net_names: dict[str, str], side: str = "A") -> Optional[str]:
    """A KiCad rule condition for one Altium scope, or None when it does
    not translate. ``and`` / ``or`` / ``not`` and brackets carry over;
    InNet, InNetClass, InComponent and the object tests in ``_PLAIN`` are
    the atoms. Net names go through ``net_names`` (Altium to KiCad)."""
    text = (scope or "").strip()
    if text.lower() in _ALL:
        return ""
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKENS.match(text, pos)
        if not m:
            return None
        tokens.append(m.group(1).strip())
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    out = []
    for tok in tokens:
        low = tok.lower()
        if tok in ("(", ")"):
            out.append(tok)
        elif low == "and":
            out.append("&&")
        elif low == "or":
            out.append("||")
        elif low == "not":
            out.append("!")
        else:
            call = _CALL.match(tok)
            if call:
                fn, arg = call.group(1).lower(), call.group(2)
                if fn == "innet":
                    out.append(f"{side}.NetName == '{net_names.get(arg, arg)}'")
                elif fn == "innetclass":
                    out.append(f"{side}.hasNetclass('{arg}')")
                elif fn == "incomponent":
                    out.append(f"{side}.memberOfFootprint('{arg}')")
                else:
                    return None
            elif low in _PLAIN:
                out.append(_PLAIN[low].format(s=side))
            else:
                return None
    expr = " ".join(out).replace("( ", "(").replace(" )", ")").replace("! ", "!")
    return f"({expr})" if len(tokens) > 1 else expr


def classes_named(rules: list[dict[str, Any]]) -> set[str]:
    """Every net class a rule's scope names."""
    out = set()
    for r in rules or []:
        for key in ("scope_1", "scope_2"):
            out.update(re.findall(r"InNetClass\('([^']+)'\)", str(r.get(key) or ""), re.I))
    return out


def _condition(rule: dict, net_names: dict[str, str]) -> Optional[str]:
    a = kicad_condition(str(rule.get("scope_1", "")), net_names, "A")
    b = kicad_condition(str(rule.get("scope_2", "All")), net_names, "B")
    if a is None or b is None:
        return None
    parts = [p for p in (a, b) if p]
    return " && ".join(parts)


def _mm(v: float) -> str:
    return f"{round(v, 4)}mm"


def _rule_kind(descriptor: str) -> Optional[str]:
    """Which ported rule an Altium rule description is, or None."""
    for prefix, kind in (("Clearance Constraint", "clearance"), ("Width Constraint", "width"),
                         ("Polygon Connect Rule", "connect"), ("Differential Pairs", "diffpair"),
                         ("Solder Mask Expansion", "mask"), ("Paste Mask Expansion", "paste"),
                         ("Silk To Solder Mask", "silk"), ("Hole Size Constraint", "hole")):
        if descriptor.startswith(prefix):
            return kind
    return None


def _min_opt_max(text: str) -> str:
    """``(min ..) (opt ..) (max ..)`` from Min=, Prefered= / Preferred= and
    Max= in an Altium description, those present."""
    lo, hi = _mils(text, "Min"), _mils(text, "Max")
    opt = _mils(text, "Preferred")
    opt = _mils(text, "Prefered") if opt is None else opt
    return " ".join(f"({k} {_mm(v)})" for k, v in (("min", lo), ("opt", opt), ("max", hi))
                    if v is not None)


def rules_file(rules: list[dict[str, Any]], net_names: dict[str, str]) -> tuple[str, list[str]]:
    """A ``.kicad_dru`` for the scoped clearance, width, mask and paste
    rules, and every polygon-connect, differential-pair, silk-to-mask and
    hole-size rule, lowest priority first (KiCad lets the last matching
    rule win, Altium the first); and the rules not ported, with why."""
    lines = ["(version 1)"]
    skipped = []
    ordered = sorted([r for r in rules or [] if r.get("enabled", True)],
                     key=lambda r: -int(r.get("priority") or 0))
    for r in ordered:
        d = str(r.get("descriptor") or "")
        name = str(r.get("name") or "rule").replace('"', "'")
        kind = _rule_kind(d)
        if kind is None:
            continue
        if kind in ("clearance", "width", "mask", "paste") and _general(r):
            continue  # in the board settings already
        cond = _condition(r, net_names)
        if cond is None:
            skipped.append(f"{name}: scope {r.get('scope_1')!r} / {r.get('scope_2')!r} "
                           "has no KiCad condition here")
            continue
        body = []
        if kind == "diffpair":
            # Altium's own spelling "Prefered"; the gap values come before
            # the width values in the description.
            gap_part, _, width_part = d.partition("Width Constraints")
            for constraint, part in (("diff_pair_gap", gap_part), ("track_width", width_part)):
                vals = _min_opt_max(part)
                if vals:
                    body.append(f"(constraint {constraint} {vals})")
            if not body:
                continue
            cond = " && ".join(c for c in ("A.inDiffPair('*')", cond) if c)
        elif kind == "mask":
            e = _mils(d, "Expansion ")
            e = _mils(d, "Expansion") if e is None else e
            if e is None:
                continue
            body.append(f"(constraint solder_mask_expansion (opt {_mm(e)}))")
        elif kind == "paste":
            e = _mils(d, "Expansion ")
            e = _mils(d, "Expansion") if e is None else e
            if e is None:
                continue
            body.append(f"(constraint solder_paste_abs_margin (opt {_mm(e)}))")
        elif kind == "silk":
            gap = _mils(d, "Clearance")
            if gap is None:
                continue
            body.append(f"(constraint silk_clearance (min {_mm(gap)}))")
        elif kind == "hole":
            vals = _min_opt_max(d)
            if not vals:
                continue
            body.append(f"(constraint hole_size {vals})")
        elif kind == "clearance":
            gap = r.get("gap_mm") or _mils(d, "Gap")
            if not gap:
                continue
            body.append(f"(constraint clearance (min {_mm(float(gap))}))")
        elif kind == "width":
            lo, opt, hi = _mils(d, "Min"), _mils(d, "Preferred"), _mils(d, "Max")
            vals = " ".join(f"({k} {_mm(v)})" for k, v in (("min", lo), ("opt", opt), ("max", hi))
                            if v is not None)
            body.append(f"(constraint track_width {vals})")
        else:
            if "Direct Connect" in d:
                body.append("(constraint zone_connection solid)")
            elif "Relief Connect" in d:
                body.append("(constraint zone_connection thermal_reliefs)")
                width = _mils(d, "Conductor Width")
                gap = _mils(d, "Air Gap Width") or _mils(d, "Air Gap")
                if width:
                    body.append(f"(constraint thermal_spoke_width (min {_mm(width)}))")
                if gap:
                    body.append(f"(constraint thermal_relief_gap (min {_mm(gap)}))")
            elif "No Connect" in d:
                body.append("(constraint zone_connection none)")
            else:
                skipped.append(f"{name}: connect style not read from {d[:60]!r}")
                continue
        cond_text = f'\n\t(condition "{cond}")' if cond else ""
        lines.append(f'(rule "{name}"{cond_text}\n\t' + "\n\t".join(body) + ")")
    return "\n\n".join(lines) + "\n", skipped


def net_name_map(altium: dict[tuple[str, str], str],
                 kicad: dict[tuple[str, str], str]) -> dict[str, str]:
    """Altium net name to the KiCad name the same pins carry."""
    out: dict[str, str] = {}
    for pin, name in altium.items():
        if pin in kicad and name not in out:
            out[name] = kicad[pin]
    return out


async def read_board(bridge, board_path: str, project_path: str) -> dict[str, Any]:
    """Layer kinds, stack-up, rules, net classes and the compiled pin-to-net
    map, read from the open Altium design."""
    out: dict[str, Any] = {}
    await bridge.send_command_async("pcb.focus_board", {"board_path": board_path})
    mech = await bridge.send_command_async("pcb.get_mech_layer_names",
                                           {"count_primitives": "false"})
    out["mech_layers"] = (mech or {}).get("mechanical_layers") or []
    from ...bridge.stackup import layer_stackup
    stack = await layer_stackup(bridge)
    out["stackup"] = (stack or {}).get("layers") or []
    rules = await bridge.send_command_async("pcb.get_design_rules", {})
    out["rules"] = (rules or {}).get("rules") or []
    classes = await bridge.send_command_async("pcb.get_net_classes", {},
                                              timeout=90.0)
    out["net_classes"] = (classes or {}).get("net_classes") or []
    nets = await bridge.send_command_async(
        "project.get_nets", {"project_path": project_path, "limit": "100000"}, timeout=120.0)
    out["pins"] = (nets or {}).get("pins") or []
    params = await bridge.send_command_async("project.get_parameters",
                                             {"project_path": project_path})
    rows = params if isinstance(params, list) else (params or {}).get("result") or []
    out["project_parameters"] = {str(p.get("name")): str(p.get("value") or "")
                                 for p in rows if isinstance(p, dict) and p.get("name")}
    docs = await bridge.send_command_async("project.get_documents",
                                           {"project_path": project_path})
    docs = docs if isinstance(docs, list) else (docs or {}).get("documents") or []
    sheets, boxes = {}, {}
    for d in docs:
        path = str(d.get("file_path") or "")
        if not path.lower().endswith(".schdoc"):
            continue
        reply = await bridge.send_command_async("generic.get_sheet_parameters",
                                                {"file_path": path})
        stem = path.replace("/", "\\").rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        sheets[stem] = {str(p.get("name")): str(p.get("value") or "")
                        for p in (reply or {}).get("parameters") or [] if p.get("name")}
        tpl = await bridge.send_command_async("generic.sheet_template",
                                              {"action": "get", "boxes": "true", "file_path": path})
        boxes[stem] = (tpl or {}).get("template_boxes")
    out["sheet_parameters"] = sheets
    out["template_boxes"] = boxes
    pours = await bridge.send_command_async("generic.query_objects", {
        "scope": "active_doc", "object_type": "polygon", "filter": "",
        "properties": "Net,Layer,NeckWidthThreshold,RemoveNarrowNecks,"
                      "IslandAreaThreshold,RemoveDead,RemoveIslandsByArea"})
    out["pours"] = (pours or {}).get("objects") or []
    variants = await bridge.send_command_async("project.get_variants",
                                               {"project_path": project_path}, timeout=120.0)
    out["variants"] = (variants or {}).get("variants") or []
    return out


def pour_settings(pours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each Altium pour's fill settings in KiCad's terms: the narrowest
    copper kept (``min_width_mm``) and which islands go (``islands``:
    ``always``, ``area`` with ``min_island_mm2``, or ``never``)."""
    def num(v: Any) -> float:
        try:
            return float(str(v).replace(",", "."))
        except ValueError:
            return 0.0

    def yes(v: Any) -> bool:
        return str(v).strip().lower() == "true"

    out = []
    for p in pours:
        neck = num(p.get("NeckWidthThreshold"))
        row = {"net": str(p.get("Net") or ""), "layer": str(p.get("Layer") or ""),
               # Necks are kept when Altium does not remove them; KiCad needs
               # some width, so 1 mil stands for none.
               "min_width_mm": round((neck if yes(p.get("RemoveNarrowNecks")) and neck > 0
                                      else 1.0) * MIL, 4)}
        if yes(p.get("RemoveDead")):
            row["islands"] = "always"
        elif yes(p.get("RemoveIslandsByArea")):
            row["islands"] = "area"
            row["min_island_mm2"] = round(num(p.get("IslandAreaThreshold")) * MIL ** 2, 4)
        else:
            row["islands"] = "never"
        out.append(row)
    return out


#: KiCad title block fields from Altium's sheet parameters, first found wins.
TITLE_FIELDS = (
    ("title", ("Title", "TITLE", "ProjectName")),
    ("rev", ("Revision", "REVISION")),
    ("date", ("Date", "DATE")),
    ("company", ("CompanyName", "COMPANY", "Organization")),
    ("comment 1", ("DrawnBy", "Engineer", "ENGINEER", "Author")),
    ("comment 2", ("CheckedBy",)),
    ("comment 3", ("ApprovedBy",)),
    ("comment 4", ("DocumentNumber",)),
)


def _value(name: str, sheet: dict[str, str], project: dict[str, str]) -> str:
    """A parameter's value: the sheet's, else the project's. ``*`` is
    Altium's "not set"; ``=NAME`` refers to another parameter."""
    for source in (sheet, project):
        v = (source.get(name) or "").strip()
        if v.startswith("=") and len(v) > 1:
            ref = v[1:]
            v = (sheet.get(ref) or project.get(ref) or "").strip()
        if v and v != "*":
            return v
    return ""


def title_block(sheet: dict[str, str], project: dict[str, str]) -> dict[str, str]:
    out = {}
    for field, names in TITLE_FIELDS:
        for name in names:
            v = _value(name, sheet, project)
            if v:
                out[field] = v
                break
    return out
