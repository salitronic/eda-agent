# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A board against one fab's published limits, before its files are written.

The fab files go out with whatever the board holds. A 3 mil track sent to
a fab whose floor is 4 mil, a 0.15 mm via to one that drills 0.2 mm, or a
blind via to one that builds none, comes back as a hold, a quote for a
dearer process, or a board made out of its limits. Each published figure
is compared with what the board measures (``pcb.get_fab_stats``) or, where
nothing measures it, with the board's design rule, which DRC holds the
copper to. A figure the fab does not publish is listed as not checked,
never as passed. A board within 2 % of a figure passes it: fabs print
"4 mil (0.1 mm)" as one limit, though 0.1 mm is 3.94 mil.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from ..units import MILS_PER_MM
from . import fab_library

#: Board measurements from pcb.get_fab_stats, by fab figure.
_MEASURED = {
    "min_track": ("min_track_width_mils", "the narrowest track on the board"),
    "min_drill": ("smallest_hole_mils", "the smallest plated hole on the board"),
    "min_annular_ring": ("min_annular_ring_mils", "the smallest annular ring on the board"),
}

#: Design rules by fab figure: the descriptor that names the rule kind.
_RULES = {
    "min_gap": ("clearance constraint", "the clearance rule"),
    "min_hole_to_hole": ("hole to hole clearance", "the hole to hole rule"),
    "min_mask_sliver": ("minimum solder mask sliver", "the solder mask sliver rule"),
    "copper_to_edge": ("board outline clearance", "the board outline clearance rule"),
}

#: How far under a figure a board may be and pass: unit conversion only.
SLACK = 0.02

_GAP = re.compile(r"\((?:Gap|Minimum|Clearance)\s*=\s*([\d.]+)\s*(mil|mm)\)", re.I)


def _rule_gap_mils(rule: dict[str, Any]) -> Optional[float]:
    gap = rule.get("gap_mm")
    if isinstance(gap, (int, float)) and gap > 0:
        return float(gap) * MILS_PER_MM
    m = _GAP.search(str(rule.get("descriptor", "")))
    if not m:
        return None
    value = float(m.group(1))
    return value * MILS_PER_MM if m.group(2).lower() == "mm" else value


def _smallest_rule(rules: Iterable[dict[str, Any]], kind: str) -> Optional[tuple[float, str]]:
    """(mils, rule name) of the smallest enabled rule of ``kind``."""
    found = []
    for r in rules:
        if not isinstance(r, dict) or r.get("enabled") is False:
            continue
        if not str(r.get("descriptor", "")).lower().startswith(kind):
            continue
        gap = _rule_gap_mils(r)
        if gap is not None:
            found.append((gap, str(r.get("name", ""))))
    return min(found) if found else None


#: Figures a custom check takes, in mils (copper_layer_counts a list).
CUSTOM_FIGURES = ("copper_layer_counts", *_MEASURED, *_RULES)


def custom_vendor(limits: Any) -> dict[str, Any]:
    """Limits the user gives, shaped as a fab's data: every figure marked as
    theirs. ``{ok: False, reason}`` when they cannot be used."""
    if not isinstance(limits, dict) or not limits:
        return {"ok": False, "reason": (
            "fab 'custom' needs fab_limits: your figures in mils, any of "
            + ", ".join(CUSTOM_FIGURES) + " (copper_layer_counts as a list)")}
    unknown = sorted(set(limits) - set(CUSTOM_FIGURES))
    if unknown:
        return {"ok": False, "reason": f"fab_limits names unknown figures {unknown}; "
                                       f"use {', '.join(CUSTOM_FIGURES)}"}
    figs: dict[str, list] = {}
    for key, value in limits.items():
        if key == "copper_layer_counts":
            figs[key] = [{"counts": [int(n) for n in value],
                          "quote": "supplied by the user", "supplied_by": "user"}]
        else:
            try:
                mils = float(value)
            except (TypeError, ValueError):
                return {"ok": False, "reason": f"fab_limits.{key} is not a number of mils"}
            figs[key] = [{"mil": mils, "quote": f"supplied by the user: {mils:g} mil",
                          "supplied_by": "user"}]
    figs.setdefault("copper_layer_counts", [{"unpublished": "not given in fab_limits"}])
    return {"ok": True, "vendor": "custom", "tiers": {"standard": figs},
            "source": {"url": "", "retrieved": "", "note": "limits supplied by the user"},
            "age": {}}


def check_board(stats: dict[str, Any], rules: list[dict[str, Any]], vendor: str, *,
                tier: str = "standard", outer_oz: float = 1.0,
                limits: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """``{ok, vendor, tier, layers, rows, failed, unchecked, reason?}``.

    ``rows`` has one entry per figure: what the board has, what the fab
    publishes (with its quote) and whether the board is inside it. Vendor
    ``custom`` checks against ``limits`` instead, each marked as the
    user's.
    """
    custom = str(vendor).strip().lower() == "custom"
    got = custom_vendor(limits) if custom else fab_library.vendor(vendor)
    if not got["ok"]:
        if custom:
            return got
        return {"ok": False, "reason": (
            f"no published limits for {vendor!r} here; the fabs with data are "
            + ", ".join(sorted(got.get("vendors") or []))
            + ", or fab 'custom' with your own fab_limits"), "vendors": got.get("vendors")}
    if tier not in got["tiers"]:
        return {"ok": False, "reason": f"{got['vendor']} has no tier {tier!r}",
                "tiers": sorted(got["tiers"])}
    layers = int(stats.get("num_copper_layers") or 0)
    rows: list[dict[str, Any]] = []
    figs = got["tiers"][tier]
    counts = (figs.get("copper_layer_counts") or [{}])[0].get("counts")
    if counts is None:
        rows.append({"figure": "copper_layer_counts", "board": layers, "fab": None,
                     "ok": None, "why": "not checked: no layer counts are published"})
    else:
        rows.append({"figure": "copper_layer_counts", "board": layers, "fab": counts,
                     "ok": layers in counts,
                     **({} if layers in counts else {
                         "why": f"{got['vendor']} {tier} does not list {layers} copper layers"})})

    def fab_value(key: str) -> dict[str, Any]:
        if custom:
            fig = (figs.get(key) or [None])[0]
            if fig is None:
                return {"ok": False, "reason": f"fab_limits gives no {key}"}
            return {"ok": True, "mils": fig["mil"], "figure": fig}
        return fab_library.figure(got["vendor"], key, copper_layers=layers,
                                  outer_oz=outer_oz, tier=tier)

    for key, (stat, what) in _MEASURED.items():
        rows.append(_row(key, fab_value(key), stats.get(stat), what, custom=custom))
    for key, (kind, what) in _RULES.items():
        rule = _smallest_rule(rules, kind)
        rows.append(_row(key, fab_value(key), rule[0] if rule else None,
                         f"{what} ({rule[1]})" if rule else what,
                         custom=custom, absent="" if rule else f"the board has no {what.removeprefix('the ')}"))
    blind = int(stats.get("vias_blind") or 0) + int(stats.get("vias_buried") or 0)
    offered = figs.get("blind_buried_vias")
    if blind and offered and offered[0].get("offered") is False:
        rows.append({"figure": "blind_buried_vias", "board": blind, "fab": "none",
                     "ok": False, "quote": offered[0].get("quote", ""),
                     "why": f"the board has {blind} blind or buried vias and "
                            f"{got['vendor']} builds none"})
    failed = [r for r in rows if r["ok"] is False]
    unchecked = [r for r in rows if r["ok"] is None]
    out: dict[str, Any] = {
        "ok": not failed, "vendor": got["vendor"], "tier": tier, "layers": layers,
        "outer_oz": outer_oz, "source": got["source"]["url"],
        "read": got["source"]["retrieved"], "age": got["age"],
        "rows": rows, "failed": len(failed), "unchecked": len(unchecked)}
    if custom:
        out["note"] = ("checked against limits supplied by the user, not a fab's "
                       "published figures")
    if failed:
        where = "your limits" if custom else f"{got['vendor']} {tier}"
        out["reason"] = (f"the board is outside {where}: " + "; ".join(
            r["why"] for r in failed))
    return out


def _row(key: str, fab: dict[str, Any], board: Any, what: str,
         absent: str = "", custom: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {"figure": key, "measured": what}
    if isinstance(board, (int, float)):
        board = round(float(board), 2)
    if not fab.get("ok"):
        return {**row, "board_mils": board, "fab": None, "ok": None,
                "why": f"not checked: {fab.get('reason')}"}
    limit = float(fab["mils"])
    row.update(fab_mils=round(limit, 2), quote=fab["figure"].get("quote", ""))
    if not isinstance(board, (int, float)) or board <= 0:
        return {**row, "board": None, "ok": None,
                "why": f"not checked: {absent or f'the board gives no value for {what}'}"}
    ok = float(board) >= limit * (1 - SLACK)
    row.update(board_mils=round(float(board), 2), ok=ok)
    if not ok:
        whose = "your" if custom else "the fab's"
        row["why"] = (f"{what} is {float(board):.2f} mil and {whose} "
                      f"{key.replace('_', ' ')} is {limit:.2f} mil")
    return row
