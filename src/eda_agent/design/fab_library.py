# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fab houses' published capabilities, their stack-ups, and laminates.

``fab_profile`` deliberately ships no capability numbers: a rule invented
here arrives looking exactly like one the fab published. This module keeps
that point while shipping data, on four rules:

- Every figure carries the fab's own words (``quote``), the page and the
  day it was read. A figure worked out from two published ones says how
  (``derived``).
- A figure the fab does not publish stays missing, with the reason, and a
  profile that needs it is REFUSED until the caller supplies it (``fill``),
  recorded as the caller's and not the fab's.
- Figures depend on the build: JLCPCB's minimum track is 4 mil on two
  layers and 3.5 mil on four, and doubles with 2 oz copper. Each figure
  says which build it is for, and the profile is built for one.
- Nothing pretends to be current: data older than a year says so in
  every result, not in a README.

Stack-ups are the fab's own ply lists (JLCPCB's parsed from its page by
scripts/fab_data_jlcpcb_stackups.py). Laminates carry each dielectric
constant with what kind it is (process, design or typical), its frequency
and its method, since a process Dk and a design Dk of one material differ
by 5 % and sizing an impedance with the wrong one is a silent error.

All lengths at the API are MILS, as everywhere else here. Pure offline.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..units import MILS_PER_MM, OZ_TO_MILS
from .fab_profile import FabProfile, Stackup, StackupLayer, dielectric_spans
from .impedance_sizing import trace_width_for_impedance

_DATA = Path(__file__).resolve().parent / "fab_data"
STALE_AFTER_DAYS = 365

#: FabProfile field -> the figure name in the vendor files.
_FIELDS = {
    "min_track_mils": "min_track",
    "min_gap_mils": "min_gap",
    "min_drill_mils": "min_drill",
    "min_annular_ring_mils": "min_annular_ring",
    "min_hole_to_hole_mils": "min_hole_to_hole",
    "min_mask_sliver_mils": "min_mask_sliver",
    "min_silk_width_mils": "min_silk_width",
}


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return json.loads((_DATA / f"{name}.json").read_text(encoding="utf-8"))


def _vendor_files() -> dict[str, str]:
    """{vendor name, lower case: file stem} for every capability file."""
    out = {}
    for p in sorted(_DATA.glob("*.json")):
        data = _load(p.stem)
        if "tiers" in data:
            out[data["vendor"].lower()] = p.stem
    return out


def _age(retrieved: str, today: Optional[_dt.date]) -> dict:
    today = today or _dt.date.today()
    days = (today - _dt.date.fromisoformat(retrieved)).days
    out = {"retrieved": retrieved, "age_days": days,
           "stale": days > STALE_AFTER_DAYS}
    if out["stale"]:
        out["stale_note"] = (f"read {days} days ago; fabs change their "
                             f"capabilities, check the page before relying on it")
    return out


def _mils(fig: dict) -> Optional[float]:
    if "mm" in fig:
        return round(fig["mm"] * MILS_PER_MM, 3)
    if "mil" in fig:
        return float(fig["mil"])
    return None


def _matches(when: dict, copper_layers: int, outer_oz: float) -> bool:
    layers = when.get("layers")
    if isinstance(layers, list):                     # [lowest, highest]
        lo, hi = layers
        if copper_layers < lo or (hi is not None and copper_layers > hi):
            return False
        layers = None
    if layers == "1" and copper_layers != 1:
        return False
    if layers == "1-2" and copper_layers > 2:
        return False
    if layers == "2+" and copper_layers < 2:
        return False
    if layers == "multi" and copper_layers <= 2:
        return False
    oz = when.get("outer_oz")
    return oz is None or math.isclose(oz, outer_oz)


def figure(name: str, key: str, *, copper_layers: int, outer_oz: float = 1.0,
           tier: str = "standard") -> dict:
    """One published figure for one build, in mils, with its quote, or
    why there is none. For figures a FabProfile does not carry (plated
    hole rings, inner-layer hole to copper)."""
    got = vendor(name)
    if not got["ok"]:
        return got
    figs = got["tiers"].get(tier, {})
    options = [f for f in figs.get(key, [])
               if _matches(f.get("when", {}), copper_layers, outer_oz)]
    for f in options:
        if _mils(f) is not None:
            return {"ok": True, "mils": _mils(f), "figure": f,
                    "vendor": got["vendor"], "tier": tier, "age": got["age"]}
    why = next((f["unpublished"] for f in options if "unpublished" in f),
               f"{got['vendor']} {tier} gives no {key} for {copper_layers} "
               f"layers at {outer_oz:g} oz")
    return {"ok": False, "reason": why}


def list_vendors(today: Optional[_dt.date] = None) -> list[dict]:
    """Every vendor with data: its tiers, page and how old the reading is."""
    out = []
    for stem in _vendor_files().values():
        data = _load(stem)
        out.append({"vendor": data["vendor"], "tiers": sorted(data["tiers"]),
                    "url": data["source"]["url"],
                    **_age(data["source"]["retrieved"], today),
                    "stackups": len(stackup_templates(data["vendor"]))})
    return out


def vendor(name: str, today: Optional[_dt.date] = None) -> dict:
    """A vendor's figures exactly as stored: every value with its quote."""
    stem = _vendor_files().get(name.strip().lower())
    if stem is None:
        return {"ok": False, "reason": f"no data for {name!r}",
                "vendors": [v["vendor"] for v in list_vendors(today)]}
    data = _load(stem)
    return {"ok": True, **data, "age": _age(data["source"]["retrieved"], today)}


def fab_profile(name: str, *, copper_layers: int, outer_oz: float = 1.0,
                tier: str = "standard", fill: Optional[dict] = None,
                today: Optional[_dt.date] = None) -> dict:
    """A :class:`FabProfile` for one build at one fab, each value cited.

    ``fill`` supplies figures the fab does not publish, as {FabProfile
    field: mils}; they are recorded as the caller's. Without them a
    profile that needs one is refused, naming every missing figure.
    """
    got = vendor(name, today)
    if not got["ok"]:
        return got
    if tier not in got["tiers"]:
        return {"ok": False, "reason": f"{got['vendor']} has no tier {tier!r}",
                "tiers": sorted(got["tiers"])}
    figs = got["tiers"][tier]
    fill = dict(fill or {})
    unknown = sorted(set(fill) - set(_FIELDS))
    if unknown:
        return {"ok": False, "reason": f"fill names unknown fields {unknown}",
                "fields": sorted(_FIELDS)}

    listed = figs["copper_layer_counts"][0]
    counts = listed.get("counts")
    if counts is None:
        return {"ok": False, "reason": (
            f"{got['vendor']} publishes no layer counts ({listed.get('unpublished', '')}); "
            "ask the fab, then check the board with fab 'custom' and your limits"),
            "age": got["age"]}
    values: dict[str, Any] = {"copper_layer_counts": counts}
    provenance: dict[str, dict] = {"copper_layer_counts": listed}
    missing = []
    if copper_layers not in counts:
        return {"ok": False, "reason": f"{got['vendor']} {tier} does not list "
                f"{copper_layers} copper layers", "copper_layer_counts": counts}
    for field, key in _FIELDS.items():
        if field in fill:
            values[field] = float(fill[field])
            provenance[field] = {"supplied_by": "caller", "mils": values[field]}
            continue
        options = [f for f in figs.get(key, [])
                   if _matches(f.get("when", {}), copper_layers, outer_oz)]
        published = [f for f in options if _mils(f) is not None]
        if published:
            values[field] = _mils(published[0])
            provenance[field] = published[0]
            continue
        why = next((f["unpublished"] for f in options if "unpublished" in f),
                   f"no figure for {copper_layers} layers at {outer_oz:g} oz")
        missing.append({"field": field, "reason": why})
    if missing:
        return {"ok": False, "missing": missing,
                "reason": "the fab does not publish these for this build; supply "
                          "each with fill={field: mils} once the fab has said",
                "age": got["age"]}

    stackups = [stackup_template(s["name"])["stackup"]
                for s in stackup_templates(got["vendor"], copper_layers=copper_layers)]
    profile = FabProfile(
        name=f"{got['vendor']} {tier}, {copper_layers} layers, {outer_oz:g} oz",
        source=(f"{got['source']['url']} read {got['source']['retrieved']}"
                + ("; caller supplied " + ", ".join(sorted(fill)) if fill else "")),
        stackups=stackups, **values)
    return {"ok": True, "profile": profile, "provenance": provenance,
            "age": got["age"]}


# --- stack-ups ----------------------------------------------------------

def _stackup_files() -> list[dict]:
    return [_load(p.stem) for p in sorted(_DATA.glob("*_stackups.json"))]


def stackup_templates(vendor_name: Optional[str] = None,
                      copper_layers: Optional[int] = None) -> list[dict]:
    """Summaries of the shipped stack-ups, optionally one vendor's or one
    copper-layer count's."""
    out = []
    for data in _stackup_files():
        if vendor_name and data["vendor"].lower() != vendor_name.strip().lower():
            continue
        for s in data["stackups"]:
            if copper_layers and s["copper_layers"] != copper_layers:
                continue
            out.append({"name": s["name"], "vendor": data["vendor"],
                        "copper_layers": s["copper_layers"],
                        "total_mm": s["total_mm"],
                        "dielectrics": [p.get("material") for p in s["plies"]
                                        if p["kind"] != "copper"]})
    return out


def stackup_template(name: str, today: Optional[_dt.date] = None) -> dict:
    """One shipped stack-up as a :class:`Stackup`, with its source."""
    for data in _stackup_files():
        for s in data["stackups"]:
            if s["name"].lower() != name.strip().lower():
                continue
            layers = []
            for i, p in enumerate(s["plies"]):
                mils = round(p["thickness_mm"] * MILS_PER_MM, 4)
                if p["kind"] == "copper":
                    layers.append(StackupLayer(name=p["name"], kind="copper",
                                               thickness_mils=mils,
                                               copper_oz=round(mils / OZ_TO_MILS, 3)))
                else:
                    layers.append(StackupLayer(name=f"{p['material']} {i}",
                                               kind=p["kind"], thickness_mils=mils,
                                               er=p.get("er")))
            return {"ok": True, "stackup": Stackup(name=s["name"], layers=layers),
                    "plies": s["plies"], "total_mm": s["total_mm"],
                    "dielectric_constants": data["dielectric_constants"],
                    "missing_er": [p["er_missing"] for p in s["plies"]
                                   if p.get("er_missing")],
                    "source": data["source"],
                    "age": _age(data["source"]["retrieved"], today)}
    return {"ok": False, "reason": f"no stack-up named {name!r}",
            "stackups": [t["name"] for t in stackup_templates()]}


# --- laminates ----------------------------------------------------------

def list_laminates() -> list[dict]:
    return [{"name": lam["name"], "maker": lam["maker"], "kind": lam["kind"],
             "design_dk": design_dk(lam["name"])["dk"]}
            for lam in _load("laminates")["laminates"]]


def laminate(name: str) -> dict:
    for lam in _load("laminates")["laminates"]:
        if lam["name"].lower().startswith(name.strip().lower()):
            return {"ok": True, **lam}
    return {"ok": False, "reason": f"no laminate {name!r}",
            "laminates": [x["name"] for x in _load("laminates")["laminates"]]}


def design_dk(name: str, frequency_ghz: Optional[float] = None) -> dict:
    """The Dk to size an impedance with: the maker's design value if it
    publishes one, else its typical value nearest ``frequency_ghz`` (the
    highest frequency listed when none is given). Never a process value,
    which is a QC test result and reads low for design."""
    lam = laminate(name)
    if not lam["ok"]:
        return lam
    design = [d for d in lam["dk"] if d["kind"] == "design"]
    if design:
        return {"ok": True, "dk": design[0]["value"], "used": design[0]}
    typical = [d for d in lam["dk"] if d["kind"] == "typical"]
    if not typical:
        return {"ok": False, "reason": f"{lam['name']} has only a process Dk"}
    f = frequency_ghz if frequency_ghz else max(d["frequency_ghz"] for d in typical)
    pick = min(typical, key=lambda d: abs(math.log(d["frequency_ghz"] / f)))
    return {"ok": True, "dk": pick["value"], "used": pick}


# --- comparing and applying ---------------------------------------------

def _live_copper(live: dict | list) -> list[dict]:
    layers = live.get("layers", []) if isinstance(live, dict) else live
    return sorted(layers, key=lambda l: l.get("order", 0))


def compare_stackup(live: dict | list, template: str | Stackup, *,
                    thickness_tol_pct: float = 5.0, er_tol: float = 0.05) -> dict:
    """Findings between a board's stack-up (pcb_get_layer_stackup) and a
    template. The board is copper-centric, one entry per copper layer
    with the dielectric under it; the template is a ply list, so its
    plies are first collapsed to one dielectric per copper gap, the shape
    Altium keeps. A different copper count is ONE finding."""
    stk = template if isinstance(template, Stackup) else None
    if stk is None:
        got = stackup_template(template)
        if not got["ok"]:
            return got
        stk = got["stackup"]
    board = _live_copper(live)
    copper = [p for p in stk.layers if p.kind == "copper"]
    spans = dielectric_spans(stk)
    if len(board) != len(copper):
        return {"ok": True, "matches": False, "findings": [{
            "what": "copper layer count", "board": len(board),
            "template": len(copper)}], "template": stk.name}
    findings = []

    def off(a: float, b: float) -> bool:
        return abs(a - b) > thickness_tol_pct / 100.0 * max(abs(b), 1e-9)

    for i, (b, c) in enumerate(zip(board, copper)):
        if off(float(b.get("copper_thickness_mils", 0)), c.thickness_mils):
            findings.append({"layer": b.get("name"), "what": "copper thickness",
                             "board_mils": b.get("copper_thickness_mils"),
                             "template_mils": round(c.thickness_mils, 3)})
        if i >= len(spans):
            continue
        span = spans[i]
        height = float(b.get("dielectric_height_mils", 0))
        if off(height, span.height_mils):
            findings.append({"layer": b.get("name"), "what": "dielectric height below",
                             "board_mils": height,
                             "template_mils": round(span.height_mils, 3),
                             "plies": span.ply_count})
        er = float(b.get("dielectric_constant", 0))
        if span.er is not None and abs(er - span.er) > er_tol:
            findings.append({"layer": b.get("name"), "what": "dielectric constant below",
                             "board": er, "template": round(span.er, 3)})
    return {"ok": True, "matches": not findings, "findings": findings,
            "template": stk.name}


def plan_apply(live: dict | list, template: str | Stackup) -> dict:
    """The pcb_modify_layer calls that give the board the template's
    thicknesses and dielectrics. Existing layers only: a board with a
    different copper count is refused, since adding a layer means picking
    the name Altium's own stack manager would, on a board that may have
    routing on it."""
    stk = template if isinstance(template, Stackup) else None
    if stk is None:
        got = stackup_template(template)
        if not got["ok"]:
            return got
        stk = got["stackup"]
    board = _live_copper(live)
    copper = [p for p in stk.layers if p.kind == "copper"]
    if len(board) != len(copper):
        return {"ok": False, "reason": f"the board has {len(board)} copper "
                f"layers and {stk.name} has {len(copper)}; add or remove "
                f"layers in Altium's Layer Stack Manager first"}
    spans = dielectric_spans(stk)
    calls = []
    for i, (b, c) in enumerate(zip(board, copper)):
        call: dict[str, Any] = {"layer": b["name"],
                                "copper_thickness_mils": round(c.thickness_mils, 4)}
        if i < len(spans):
            span = spans[i]
            call["dielectric_type"] = span.kind
            call["dielectric_height_mils"] = round(span.height_mils, 4)
            if span.er is not None:
                call["dielectric_constant"] = round(span.er, 4)
        calls.append(call)
    return {"ok": True, "template": stk.name, "calls": calls}


# --- impedance from a stack-up ------------------------------------------

def impedance_widths(template: str | Stackup, targets: list[dict]) -> dict:
    """Trace widths for each target on each routing layer of a stack-up.

    ``targets`` items: {"ohms": 50} single-ended, or {"ohms": 90,
    "spacing_mils": 5} differential. Outer layers are microstrip over the
    dielectric to the next copper. An inner layer is sized as stripline
    only when it sits near the middle (the closed form is symmetric);
    one much nearer one plane is reported, not sized: the symmetric form
    there is wrong by more than its own accuracy. Closed forms are
    +/-10 %; the fab's field solver has the last word, and solder mask
    over a microstrip lowers it a few ohms more."""
    stk = template if isinstance(template, Stackup) else None
    if stk is None:
        got = stackup_template(template)
        if not got["ok"]:
            return got
        stk = got["stackup"]
    copper = [p for p in stk.layers if p.kind == "copper"]
    spans = dielectric_spans(stk)
    rows = []
    for i, c in enumerate(copper):
        oz = c.copper_oz or c.thickness_mils / OZ_TO_MILS
        row: dict[str, Any] = {"layer": c.name, "results": []}
        if i == 0 or i == len(copper) - 1:
            span = spans[0] if i == 0 else spans[-1]
            geometry, h, er = "microstrip", span.height_mils, span.er
        else:
            above, below = spans[i - 1], spans[i]
            if max(above.height_mils, below.height_mils) > 1.5 * min(
                    above.height_mils, below.height_mils):
                row["not_sized"] = (f"asymmetric: {above.height_mils:.2f} mil to one "
                                    f"plane, {below.height_mils:.2f} to the other; "
                                    f"the symmetric stripline form does not hold")
                rows.append(row)
                continue
            geometry = "stripline"
            h = above.height_mils + c.thickness_mils + below.height_mils
            er = (None if above.er is None or below.er is None else
                  (above.er * above.height_mils + below.er * below.height_mils)
                  / (above.height_mils + below.height_mils))
        if er is None:
            row["not_sized"] = "a dielectric here has no published Dk"
            rows.append(row)
            continue
        row.update(geometry=geometry, height_mils=round(h, 3), er=round(er, 3))
        for t in targets:
            spacing = float(t.get("spacing_mils", 0) or 0)
            g = geometry + ("_diff" if spacing else "")
            r = trace_width_for_impedance(float(t["ohms"]), g, h,
                                          dielectric_constant=er, copper_oz=oz,
                                          spacing_mils=spacing)
            row["results"].append({"ohms": t["ohms"], "geometry": g,
                                   "spacing_mils": spacing or None,
                                   "width_mils": round(r.width_mils, 2),
                                   "feasible": r.feasible})
        rows.append(row)
    return {"ok": True, "stackup": stk.name, "layers": rows,
            "accuracy": "closed form, +/-10 %; confirm with the fab's solver"}


__all__ = [
    "STALE_AFTER_DAYS", "list_vendors", "vendor", "fab_profile", "figure",
    "stackup_templates", "stackup_template", "list_laminates", "laminate",
    "design_dk", "compare_stackup", "plan_apply", "impedance_widths",
]
