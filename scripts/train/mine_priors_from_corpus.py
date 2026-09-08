#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Learn placement priors from human-drawn KiCad schematics.

``build_placement_priors.py`` learns the same artifact from recorded USER
EDITS, which is the right signal but a scarce one: the corpus on disk held 27
edits and every one of them was unkeyed. A directory of human schematics is
the same measurement taken thousands of times, already made, by people who
were drawing for readability rather than for us.

WHAT IS MINEABLE, AND WHY IT IS ONLY THESE PAIRS. ``apply_placement_priors``
looks a prior up by ``"<part_role>|<anchor_role>"``, and a corpus sheet has no
planner to tag roles. Only the roles the CONSUMER infers structurally can
therefore be keyed here: decoupling caps (a two-pin cap between a rail and
ground) and crystal load caps. That is not a narrow slice in practice, because
those are the parts a board carries most of.

THE ROLES AND ANCHORS COME FROM THE CONSUMER'S OWN FUNCTIONS, deliberately.
A row keyed differently from the way it is read is a row nothing can ever
apply, which is exactly how the edit corpus became 27 unusable rows (see
tests/design/test_placement_edits_are_keyed.py). So this imports
``_infer_decoup_roles``, ``_infer_crystal_roles`` and
``_decoupling_rail_anchor`` rather than re-deriving any of them.

ROTATION IS NOT MINED. The prior's ``rotation`` is a DELTA added to whatever
the layout engine already chose, and a human sheet has no "before" state to
subtract. It would only be safe if the engine's pre-prior rotation were
always 0; measured over 60 corpus sheets it is 270 for all 307 decoupling
caps, so a mined absolute angle would double-rotate them. Rows carry
``rot_delta_deg = 0`` and the aggregate leaves rotation alone.

Usage:
    python scripts/train/mine_priors_from_corpus.py CORPUS_DIR [-o OUT.json]
        [--min-samples N] [--max-parts N] [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from eda_agent.design.human_benchmark import _clean_refdes, plan_from_sheet
from eda_agent.design.kicad_sheet_reader import read_sheet
from eda_agent.design.layout import PlacedPart
from eda_agent.design.plan import DesignPlan
from eda_agent.design.priors import (
    _DECOUP_ROLES,
    _crystal_clusters,
    _decoupling_rail_anchor,
    _infer_crystal_roles,
    _infer_decoup_roles,
)

# Reuse the aggregator rather than writing a second one: it already drops
# unkeyable buckets, reports how many it dropped, and takes the MEDIAN, which
# is what makes a corpus with a few wild sheets usable at all.
_spec = importlib.util.spec_from_file_location(
    "build_placement_priors", Path(__file__).with_name("build_placement_priors.py"))
_bpp = importlib.util.module_from_spec(_spec)
sys.modules["build_placement_priors"] = _bpp
_spec.loader.exec_module(_bpp)
aggregate = _bpp.aggregate


def human_placements(sheet) -> dict[str, PlacedPart]:
    """The human positions, in the canvas frame the engine works in.

    KiCad sheet Y grows DOWNWARD and the canvas is Y-up, so the sign is
    flipped here exactly as ``human_benchmark`` flips it. Getting that wrong
    would mine every vertical offset backwards, and a decoupling cap would be
    learned to sit below its IC when humans put it above.
    """
    out: dict[str, PlacedPart] = {}
    for sym in sheet.symbols:
        ref = _clean_refdes(sym.reference)
        if ref is None or ref in out:
            continue  # a multi-unit part appears once per unit
        out[ref] = PlacedPart(refdes=ref, sheet="main", x_mils=int(sym.x),
                              y_mils=int(-sym.y), rotation=int(sym.rotation))
    return out


def rows_from_sheet(text: str, path: str, max_parts: int) -> list[dict[str, Any]]:
    """One row per role-bearing part whose anchor is also on the sheet."""
    sheet = read_sheet(text, path)
    payload = plan_from_sheet(sheet)
    if not payload or not payload.get("parts") or not payload.get("nets"):
        return []
    try:
        plan = DesignPlan.model_validate(payload)
    except Exception:
        return []
    if len(plan.parts) > max_parts:
        return []

    placements = human_placements(sheet)
    if not placements:
        return []

    roles = _infer_decoup_roles(plan)
    roles.update(_infer_crystal_roles(plan))
    if not roles:
        return []
    crystal_of = {cap: cluster[0]
                  for cluster in _crystal_clusters(plan)
                  for cap in cluster[1:]}

    rows: list[dict[str, Any]] = []
    for refdes, role in sorted(roles.items()):
        part = placements.get(refdes)
        if part is None:
            continue
        if role in _DECOUP_ROLES:
            anchor_refdes = _decoupling_rail_anchor(refdes, plan, placements)
            anchor_role = "ic"
        elif role in ("crystal_cap_l", "crystal_cap_r"):
            anchor_refdes = crystal_of.get(refdes)
            anchor_role = "crystal"
        else:
            continue                      # the crystal itself anchors others
        anchor = placements.get(anchor_refdes) if anchor_refdes else None
        if anchor is None:
            continue
        rows.append({
            "part_role": role,
            "anchor_role": anchor_role,
            "dx_mils": part.x_mils - anchor.x_mils,
            "dy_mils": part.y_mils - anchor.y_mils,
            "rot_delta_deg": 0,
            "source": Path(path).name,
        })
    return rows


def refuse_wide_spreads(
    rows: list[dict[str, Any]], max_iqr: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Drop every row of a role pair whose offsets are not one preference.

    A median over a pile of preferences reads as confident and is worthless.
    decoup_cap|ic came out with an interquartile range of +/-2000 mils:
    humans put those caps on all four sides of the IC, and the median of that
    is a point on the IC's centre. An offset is a preference only when its
    IQR is a small multiple of the grid, on BOTH axes. Returns the surviving
    rows and the refused pair keys.
    """
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault(f"{r['part_role']}|{r['anchor_role']}", []).append(r)
    too_wide: set[str] = set()
    for key, rs in by_pair.items():
        if len(rs) < 4:
            continue
        for axis in ("dx_mils", "dy_mils"):
            q = statistics.quantiles([r[axis] for r in rs], n=4)
            if q[2] - q[0] > max_iqr:
                too_wide.add(key)
    kept = [r for r in rows
            if f"{r['part_role']}|{r['anchor_role']}" not in too_wide]
    return kept, too_wide


def _spread(values: list[int]) -> str:
    """Median plus the interquartile range, so a wide prior looks wide.

    A median over a bimodal pile (caps above AND below their IC in equal
    numbers) reads as confident and is worthless; the IQR is what shows it.
    """
    if len(values) < 4:
        return f"n={len(values)}"
    q = statistics.quantiles(values, n=4)
    return f"median {statistics.median(values):.0f}, IQR {q[0]:.0f}..{q[2]:.0f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("corpus", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--max-parts", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-iqr", type=int, default=800,
                    help="widest interquartile range (mils) a mined offset "
                         "may have and still be written as a prior")
    args = ap.parse_args(argv)

    rows: list[dict[str, Any]] = []
    sheets = used = 0
    for sp in sorted(args.corpus.glob("*.kicad_sch")):
        sheets += 1
        try:
            got = rows_from_sheet(
                sp.read_text(encoding="utf-8", errors="replace"), str(sp),
                args.max_parts)
        except Exception:
            continue
        if got:
            used += 1
            rows.extend(got)

    n_mined = len(rows)
    rows, too_wide = refuse_wide_spreads(rows, args.max_iqr)
    out = aggregate(rows, args.min_samples)
    out["n_rows_mined"] = n_mined
    out["source"] = "human corpus"
    out["refused_too_wide"] = sorted(too_wide)
    out["n_sheets_scanned"] = sheets
    out["n_sheets_contributing"] = used

    print(f"scanned {sheets} sheets, {used} contributed, {n_mined} rows mined, "
          f"{len(rows)} kept after the spread check")
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_pair.setdefault(f"{r['part_role']}|{r['anchor_role']}", []).append(r)
    for key in sorted(by_pair):
        rs = by_pair[key]
        print(f"  {key:<26} n={len(rs):<5} "
              f"dx {_spread([r['dx_mils'] for r in rs])}   "
              f"dy {_spread([r['dy_mils'] for r in rs])}")
    print(f"kept {out['n_pairs']} pair(s); dropped {out['edits_unkeyed']} unkeyed; "
          f"refused as too wide: {sorted(too_wide) or 'none'}")

    if args.dry_run or args.out is None:
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
