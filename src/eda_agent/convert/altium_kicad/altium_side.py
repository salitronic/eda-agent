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


def rules_file(rules: list[dict[str, Any]], net_names: dict[str, str]) -> tuple[str, list[str]]:
    """A ``.kicad_dru`` for the scoped clearance, width and polygon-connect
    rules, lowest priority first (KiCad lets the last matching rule win,
    Altium the first); and the rules that were not ported, with why."""
    lines = ["(version 1)"]
    skipped = []
    ordered = sorted([r for r in rules or [] if r.get("enabled", True)],
                     key=lambda r: -int(r.get("priority") or 0))
    for r in ordered:
        d = str(r.get("descriptor") or "")
        name = str(r.get("name") or "rule").replace('"', "'")
        kind = ("clearance" if d.startswith("Clearance Constraint")
                else "width" if d.startswith("Width Constraint")
                else "connect" if d.startswith("Polygon Connect Rule") else None)
        if kind is None:
            continue
        if kind != "connect" and _general(r):
            continue  # in the board settings already
        cond = _condition(r, net_names)
        if cond is None:
            skipped.append(f"{name}: scope {r.get('scope_1')!r} / {r.get('scope_2')!r} "
                           "has no KiCad condition here")
            continue
        body = []
        if kind == "clearance":
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
    stack = await bridge.send_command_async("pcb.get_layer_stackup", {})
    out["stackup"] = (stack or {}).get("layers") or []
    rules = await bridge.send_command_async("pcb.get_design_rules", {})
    out["rules"] = (rules or {}).get("rules") or []
    classes = await bridge.send_command_async("pcb.get_net_classes", {},
                                              timeout=90.0)
    out["net_classes"] = (classes or {}).get("net_classes") or []
    nets = await bridge.send_command_async(
        "project.get_nets", {"project_path": project_path, "limit": "100000"}, timeout=120.0)
    out["pins"] = (nets or {}).get("pins") or []
    return out
