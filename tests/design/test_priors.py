# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the v1 priors aggregator + apply pass.

The aggregator (``scripts/train/build_placement_priors.py``) is exercised
via its ``aggregate`` function so we don't need argv plumbing.

Apply pass (``design.priors.apply_placement_priors``) is tested with
hand-built placements + priors dicts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from eda_agent.design.layout import PlacedPart
from eda_agent.design.plan import DesignPlan
from eda_agent.design.priors import (
    _crystal_clusters,
    _infer_crystal_roles,
    _infer_decoup_roles,
    _pick_anchor,
    apply_placement_priors,
    load_priors,
    resnap_crystal_clusters,
    resnap_motif_clusters,
)

# Add the train script to sys.path so we can import its aggregate fn.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "train"))
from build_placement_priors import aggregate, _mode_or_zero  # noqa: E402


_LIB = "/fake/lib.SchLib"


def _make_plan(parts: list[dict], nets: list[dict]) -> DesignPlan:
    return DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": parts,
        "nets": nets,
    })


# ----------------------------- aggregator -----------------------------


def test_aggregate_collapses_role_pair_to_median():
    """Multiple edits for the same (part_role, anchor_role) pair should
    collapse to one entry with the MEDIAN (dx, dy) and the modal rotation."""
    rows = [
        {"part_role": "decoup_cap", "anchor_role": "ic",
         "dx_mils": 100, "dy_mils": 400, "rot_delta_deg": 0},
        {"part_role": "decoup_cap", "anchor_role": "ic",
         "dx_mils": 200, "dy_mils": 400, "rot_delta_deg": 0},
        {"part_role": "decoup_cap", "anchor_role": "ic",
         "dx_mils": 150, "dy_mils": 400, "rot_delta_deg": 90},
    ]
    payload = aggregate(rows, min_samples=2)
    assert payload["n_pairs"] == 1
    entry = payload["priors"]["decoup_cap|ic"]
    assert entry["dx"] == 150  # median of [100, 200, 150]
    assert entry["dy"] == 400
    assert entry["rotation"] == 0  # mode (2 of 3 are 0)
    assert entry["n_samples"] == 3


def test_aggregate_skips_pairs_below_min_samples():
    rows = [
        {"part_role": "loner", "anchor_role": "ic",
         "dx_mils": 100, "dy_mils": 0, "rot_delta_deg": 0},
        # Only one observation -> skipped at min_samples=2.
    ]
    payload = aggregate(rows, min_samples=2)
    assert payload["n_pairs"] == 0
    assert payload["priors"] == {}


def test_aggregate_keeps_pairs_at_min_samples_boundary():
    rows = [
        {"part_role": "r", "anchor_role": "ic",
         "dx_mils": 100, "dy_mils": 0, "rot_delta_deg": 0},
        {"part_role": "r", "anchor_role": "ic",
         "dx_mils": 100, "dy_mils": 0, "rot_delta_deg": 0},
    ]
    payload = aggregate(rows, min_samples=2)
    assert payload["n_pairs"] == 1


def test_mode_or_zero_breaks_ties_toward_zero():
    # Two values appear once each; pick the smaller-magnitude one.
    assert _mode_or_zero([0, 90]) == 0
    # Clear majority wins.
    assert _mode_or_zero([0, 0, 90, 180]) == 0
    # Equal counts for 0 and 90, pick 0 (smaller magnitude).
    assert _mode_or_zero([0, 90, 0, 90]) == 0
    # Equal-magnitude opposite signs: deterministically pick one (the
    # current impl picks -90 via lexicographic tuple comparison; either
    # answer is rotationally equivalent so we just assert non-zero).
    assert _mode_or_zero([-90, 90]) in (-90, 90)


# ------------------------------ apply pass ----------------------------


def test_apply_priors_shifts_passive_relative_to_anchor():
    """A decoup_cap with a prior of (0, 500) relative to ic should land
    500 mils above the IC, regardless of where Sugiyama put it."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "decoup_cap"},
        ],
        nets=[
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "8"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    )
    # Layout puts U1 at (4000, 5000) and C1 at (1000, 1000); prior wants
    # C1 directly above U1 by 500 mils.
    placements = [
        PlacedPart(refdes="U1", sheet="main", x_mils=4000, y_mils=5000, rotation=0),
        PlacedPart(refdes="C1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    priors = {
        "decoup_cap|ic": {
            "dx": 0, "dy": 500, "rotation": 0,
            "n_samples": 5, "part_role": "decoup_cap", "anchor_role": "ic",
        }
    }
    out = apply_placement_priors(placements, plan, priors)
    out_by_refdes = {p.refdes: p for p in out}
    # U1 (anchor, pin_count >= 4 ... wait it only has 2 pins here, so
    # the anchor_set excludes it. Let me check the algorithm again):
    # Actually U1 has 2 pins; anchor_set requires pin_count >= 4 to
    # protect it from biasing. With 2 pins it's eligible for bias too,
    # but it has no prior keyed by "ic|<anything>" so it's untouched.
    assert out_by_refdes["U1"].x_mils == 4000
    assert out_by_refdes["U1"].y_mils == 5000
    # C1 moves to U1's position + (0, 500), snapped to 100-mil grid.
    assert out_by_refdes["C1"].x_mils == 4000
    assert out_by_refdes["C1"].y_mils == 5500


def test_decoup_anchors_to_own_rail_ic_on_multi_ic_board():
    """Each decoupling cap must anchor to the IC on ITS power rail, not the
    board's biggest IC -- otherwise every decap piles onto one chip."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "U2", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "decoup_cap"},
            {"refdes": "C2", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "decoup_cap"},
        ],
        nets=[
            {"name": "VCC1", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"}]},
            {"name": "VCC2", "is_power": True, "pins": [
                {"refdes": "U2", "pin": "1"}, {"refdes": "C2", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "U2", "pin": "2"},
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"}]},
            {"name": "SIG", "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "U2", "pin": "3"}]},
            {"name": "SIG2", "pins": [    # U2 is the bigger IC (4 pins)
                {"refdes": "U2", "pin": "4"}, {"refdes": "U1", "pin": "3"}]},
        ],
    )
    placed = [
        PlacedPart(refdes="U1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
        PlacedPart(refdes="U2", sheet="main", x_mils=5000, y_mils=5000, rotation=0),
        PlacedPart(refdes="C1", sheet="main", x_mils=3000, y_mils=3000, rotation=0),
        PlacedPart(refdes="C2", sheet="main", x_mils=3000, y_mils=3000, rotation=0),
    ]
    priors = {"decoup_cap|ic": {"dx": 0, "dy": 400, "rotation": 0,
                                "n_samples": 5, "part_role": "decoup_cap",
                                "anchor_role": "ic"}}
    pos = {p.refdes: (p.x_mils, p.y_mils)
           for p in apply_placement_priors(placed, plan, priors)}
    # C1 lands on U1's rail (1000,1000)+(0,400); C2 on U2's (5000,5000)+(0,400)
    # -- NOT both on the bigger IC U2.
    assert pos["C1"] == (1000, 1400)
    assert pos["C2"] == (5000, 5400)


def test_infer_decoup_role_tags_rail_cap_not_filter_cap():
    """A 2-pin cap on (power rail, ground) with no role is structurally tagged
    decoup_cap; a cap on (signal, ground) is NOT (so its filter motif governs).
    Works whether the rail is flagged is_power or only connector-inferred."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,    # decap
             "status": "existing", "sheet": "main"},
            {"refdes": "C2", "lib_ref": "CAP", "lib_path": _LIB,    # filter cap
             "status": "existing", "sheet": "main"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        nets=[
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"},
                {"refdes": "U1", "pin": "5"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"},
                {"refdes": "C2", "pin": "2"}]},
            {"name": "SIGIN", "pins": [
                {"refdes": "R1", "pin": "1"}, {"refdes": "U1", "pin": "3"}]},
            {"name": "SIGOUT", "pins": [    # signal filter node, not a rail
                {"refdes": "R1", "pin": "2"}, {"refdes": "C2", "pin": "1"},
                {"refdes": "U1", "pin": "4"}]},
        ],
    )
    inferred = _infer_decoup_roles(plan)
    assert inferred.get("C1") == "decoup_cap"
    assert "C2" not in inferred          # filter cap on a signal node, excluded
    # An explicitly-roled cap is never overridden.
    plan.parts[1].role = "fb_top"
    assert "C1" not in _infer_decoup_roles(plan)


def test_decoup_prior_fires_without_explicit_ic_role():
    """A decoup cap whose IC the planner forgot to tag role=ic still gets the
    prior, because the rail-mate anchor is found structurally."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},   # NO role
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "decoup_cap"},
        ],
        nets=[
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"},
                {"refdes": "U1", "pin": "3"}]},   # U1 = 3 pins (an IC)
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"}]},
        ],
    )
    placed = [
        PlacedPart(refdes="U1", sheet="main", x_mils=2000, y_mils=2000, rotation=0),
        PlacedPart(refdes="C1", sheet="main", x_mils=6000, y_mils=6000, rotation=0),
    ]
    priors = {"decoup_cap|ic": {"dx": 0, "dy": 400, "rotation": 0,
                                "n_samples": 5, "part_role": "decoup_cap",
                                "anchor_role": "ic"}}
    out = {p.refdes: (p.x_mils, p.y_mils)
           for p in apply_placement_priors(placed, plan, priors)}
    assert out["C1"] == (2000, 2400)   # snapped to U1 + (0,400), not left at 6000


def test_apply_priors_no_priors_passthrough():
    """Empty / None priors should pass placements through unchanged."""
    plan = _make_plan(
        parts=[
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "r"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "c"},
        ],
        nets=[
            {"name": "N", "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "C1", "pin": "1"}]},
        ],
    )
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
        PlacedPart(refdes="C1", sheet="main", x_mils=2000, y_mils=2000, rotation=0),
    ]
    out = apply_placement_priors(placements, plan, {})
    assert out == placements


def test_apply_priors_unknown_role_pair_passthrough():
    """A part whose (part_role, anchor_role) isn't in priors stays put."""
    plan = _make_plan(
        parts=[
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "feedback_r"},
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
        ],
        nets=[
            {"name": "OUT", "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
            {"name": "FB", "pins": [
                {"refdes": "U1", "pin": "2"},
                {"refdes": "R1", "pin": "2"}]},
        ],
    )
    placements = [
        PlacedPart(refdes="U1", sheet="main", x_mils=4000, y_mils=5000, rotation=0),
        PlacedPart(refdes="R1", sheet="main", x_mils=1500, y_mils=1500, rotation=0),
    ]
    priors = {
        "decoup_cap|ic": {"dx": 0, "dy": 500, "rotation": 0,
                          "n_samples": 5, "part_role": "decoup_cap",
                          "anchor_role": "ic"},
    }
    out = apply_placement_priors(placements, plan, priors)
    # R1's pair is "feedback_r|ic", not in the priors -> unchanged.
    r1 = next(p for p in out if p.refdes == "R1")
    assert r1.x_mils == 1500 and r1.y_mils == 1500


def test_apply_priors_snaps_to_grid():
    """Biased positions must land on the 100-mil grid."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "decoup_cap"},
        ],
        nets=[
            {"name": "SIG", "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "C1", "pin": "1"}]},
        ],
    )
    placements = [
        PlacedPart(refdes="U1", sheet="main", x_mils=4023, y_mils=5067, rotation=0),
        PlacedPart(refdes="C1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    priors = {
        "decoup_cap|ic": {
            "dx": 47, "dy": 213, "rotation": 0,  # non-grid offsets
            "n_samples": 3, "part_role": "decoup_cap", "anchor_role": "ic",
        }
    }
    out = apply_placement_priors(placements, plan, priors, grid_mils=100)
    c1 = next(p for p in out if p.refdes == "C1")
    # (4023 + 47) = 4070 -> snap to 4000
    # (5067 + 213) = 5280 -> snap to 5200
    assert c1.x_mils % 100 == 0
    assert c1.y_mils % 100 == 0


def test_load_priors_missing_file_falls_back_to_canonical(tmp_path: Path):
    """No on-disk learned priors -> falls back to CANONICAL_PRIORS shipped
    with the package (was: returned None). The canonical fallback ensures
    fresh installs still get professional-looking output."""
    priors = load_priors(tmp_path / "does_not_exist.json")
    assert priors is not None
    assert len(priors) > 0
    # Canonical priors should include at least the decoup-cap-near-IC pattern.
    assert "vcc_decoup|ic" in priors or "decoup_cap|ic" in priors


def test_load_priors_reads_priors_subdict(tmp_path: Path):
    priors_file = tmp_path / "priors.json"
    priors_file.write_text(json.dumps({
        "version": 1,
        "trained_at": "now",
        "n_edits": 5,
        "priors": {"r|ic": {"dx": 100, "dy": 0, "rotation": 0, "n_samples": 5}},
    }), encoding="utf-8")
    priors = load_priors(priors_file)
    assert priors is not None
    assert "r|ic" in priors


def test_priors_anchor_matches_learner_heuristic():
    """The apply-side anchor picker must match the learner's heuristic
    so prior keys look up the same role pair that produced them."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "fb_r"},
        ],
        nets=[
            {"name": "SIG", "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
        ],
    )
    placements = {
        "U1": PlacedPart(refdes="U1", sheet="main", x_mils=4000, y_mils=5000, rotation=0),
        "R1": PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    }
    anchor = _pick_anchor("R1", plan, placements)
    assert anchor == "U1"


# ----------------------------- crystal oscillator -----------------------------


def _crystal_plan():
    """MCU + crystal Y1 (XIN/XOUT) + two load caps C6/C7 to ground, plus an
    unrelated decap C1 and resistor R1 -- the structural crystal signature."""
    return _make_plan(
        parts=[
            {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
             "status": "existing", "sheet": "main"}
            for r, lr in [("U1", "MCU"), ("Y1", "XTAL"), ("C6", "CAP"),
                          ("C7", "CAP"), ("C1", "CAP"), ("R1", "RES")]
        ],
        nets=[
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "C6", "pin": "2"},
                {"refdes": "C7", "pin": "2"}, {"refdes": "C1", "pin": "2"}]},
            {"name": "XIN", "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "Y1", "pin": "1"},
                {"refdes": "C6", "pin": "1"}]},
            {"name": "XOUT", "pins": [
                {"refdes": "U1", "pin": "4"}, {"refdes": "Y1", "pin": "2"},
                {"refdes": "C7", "pin": "1"}]},
            {"name": "RST", "pins": [
                {"refdes": "U1", "pin": "5"}, {"refdes": "R1", "pin": "2"}]},
        ],
    )


def test_infer_crystal_roles_tags_crystal_and_load_caps_only():
    plan = _crystal_plan()
    clusters = _crystal_clusters(plan)
    # (crystal, cap_l on smaller net XIN, cap_r on XOUT, anchor IC)
    assert clusters == [("Y1", "C6", "C7", "U1")]
    roles = _infer_crystal_roles(plan)
    assert roles == {"Y1": "crystal", "C6": "crystal_cap_l",
                     "C7": "crystal_cap_r"}
    # The unrelated decap C1 and resistor R1 are not crystal parts.
    assert "C1" not in roles and "R1" not in roles


def test_crystal_clusters_rejects_2pin_bridge_without_common_ic():
    """A 2-pin part bridging two filtered nodes that do NOT reach a common
    multi-pin IC is not a crystal."""
    plan = _make_plan(
        parts=[
            {"refdes": r, "lib_ref": lr, "lib_path": _LIB,
             "status": "existing", "sheet": "main"}
            for r, lr in [("U1", "MCU"), ("U2", "MCU"), ("Y9", "XTAL"),
                          ("C6", "CAP"), ("C7", "CAP")]
        ],
        nets=[
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "C6", "pin": "2"}, {"refdes": "C7", "pin": "2"}]},
            # NETA reaches only U1, NETB only U2 -> no common IC.
            {"name": "NETA", "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "Y9", "pin": "1"},
                {"refdes": "C6", "pin": "1"}]},
            {"name": "NETB", "pins": [
                {"refdes": "U2", "pin": "4"}, {"refdes": "Y9", "pin": "2"},
                {"refdes": "C7", "pin": "1"}]},
        ],
    )
    assert _crystal_clusters(plan) == []


def test_infer_crystal_roles_respects_explicit_planner_role():
    plan = _crystal_plan()
    next(p for p in plan.parts if p.refdes == "Y1").role = "special"
    assert _crystal_clusters(plan) == []       # roled crystal is skipped


def test_resnap_crystal_clusters_pins_caps_symmetric():
    plan = _crystal_plan()
    placed = [
        PlacedPart(refdes="Y1", sheet="main", x_mils=4500, y_mils=3500,
                   rotation=0),
        PlacedPart(refdes="C6", sheet="main", x_mils=200, y_mils=9000,
                   rotation=270),   # scattered far away
        PlacedPart(refdes="C7", sheet="main", x_mils=8000, y_mils=100,
                   rotation=270),
    ]
    out = {p.refdes: p for p in resnap_crystal_clusters(plan, placed)}
    assert out["C6"].x_mils == 4100 and out["C6"].y_mils == 3500
    assert out["C7"].x_mils == 4900 and out["C7"].y_mils == 3500
    assert out["C6"].rotation == 270           # rotation preserved


def test_resnap_anchors_crystal_near_ic_and_flanks_caps():
    """With the IC present, the whole oscillator is pulled in beside it: the
    crystal lands just clear of the IC body and the caps flank it on the
    perpendicular axis (so neither sits between the crystal and the IC)."""
    import math
    plan = _crystal_plan()
    placed = [
        PlacedPart(refdes="U1", sheet="main", x_mils=2000, y_mils=2000,
                   rotation=0),
        PlacedPart(refdes="Y1", sheet="main", x_mils=9000, y_mils=2100,
                   rotation=0),       # far to the IC's right
        PlacedPart(refdes="C6", sheet="main", x_mils=200, y_mils=9000,
                   rotation=270),
        PlacedPart(refdes="C7", sheet="main", x_mils=8000, y_mils=100,
                   rotation=270),
    ]
    out = {p.refdes: p for p in resnap_crystal_clusters(plan, placed)}
    # Crystal pulled in to just clear of the IC body (was 7000 mils away).
    d = math.dist((out["Y1"].x_mils, out["Y1"].y_mils), (2000, 2000))
    assert d < 1600
    # Crystal is to the IC's right (its Sugiyama side), so caps stack vertically
    # and share the crystal's x.
    assert out["C6"].x_mils == out["Y1"].x_mils == out["C7"].x_mils
    assert {out["C6"].y_mils, out["C7"].y_mils} == {
        out["Y1"].y_mils - 400, out["Y1"].y_mils + 400}


def test_resnap_noop_without_crystal():
    plan = _make_plan(
        parts=[{"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
                "status": "existing", "sheet": "main"},
               {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
                "status": "existing", "sheet": "main"}],
        nets=[{"name": "N", "pins": [{"refdes": "R1", "pin": "1"},
                                     {"refdes": "R2", "pin": "1"}]}],
    )
    placed = [PlacedPart(refdes="R1", sheet="main", x_mils=0, y_mils=0,
                         rotation=0),
              PlacedPart(refdes="R2", sheet="main", x_mils=500, y_mils=0,
                         rotation=0)]
    assert resnap_crystal_clusters(plan, placed) == placed


def test_crystal_clusters_rejects_feedback_divider():
    """A feedback divider (Rtop VOUT/FB, Rbot FB/GND, cap on VOUT) must NOT be
    mistaken for a crystal: the FB-to-ground part is a RESISTOR, not a load
    cap, so the load-cap check (which requires an actual capacitor) rejects it.
    Without that guard the divider's top resistor was tagged 'crystal' and its
    FB resistor / output cap mis-snapped as load caps."""
    plan = _make_plan(
        parts=[
            {"refdes": "U3", "lib_ref": "BUCK", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R10", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R11", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "C5", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        nets=[
            {"name": "VOUT", "is_power": True, "pins": [
                {"refdes": "U3", "pin": "4"}, {"refdes": "R10", "pin": "1"},
                {"refdes": "C5", "pin": "1"}]},
            {"name": "FB", "pins": [
                {"refdes": "U3", "pin": "3"}, {"refdes": "R10", "pin": "2"},
                {"refdes": "R11", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U3", "pin": "2"}, {"refdes": "R11", "pin": "2"},
                {"refdes": "C5", "pin": "2"}]},
        ],
    )
    assert _crystal_clusters(plan) == []
    assert _infer_crystal_roles(plan) == {}


def test_resnap_motif_clusters_retightens_pi_filter():
    """A pi filter (Cin/L/Cout) scattered by the shove is re-snapped to its
    canonical C-L-C arrangement around the cluster's current centroid."""
    import math
    plan = _make_plan(
        parts=[
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "L1", "lib_ref": "IND", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "C2", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"}],
        nets=[
            {"name": "IN", "is_power": True, "pins": [
                {"refdes": "C1", "pin": "1"}, {"refdes": "L1", "pin": "1"}]},
            {"name": "OUT", "is_power": True, "pins": [
                {"refdes": "L1", "pin": "2"}, {"refdes": "C2", "pin": "1"},
                {"refdes": "U1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                {"refdes": "U1", "pin": "2"}]}])
    # Scattered (as the overlap shove would leave them).
    scattered = [
        PlacedPart(refdes="C1", sheet="main", x_mils=1000, y_mils=5000, rotation=0),
        PlacedPart(refdes="L1", sheet="main", x_mils=4000, y_mils=2000, rotation=0),
        PlacedPart(refdes="C2", sheet="main", x_mils=6000, y_mils=6000, rotation=0),
        PlacedPart(refdes="U1", sheet="main", x_mils=8000, y_mils=3000, rotation=0)]

    out = {p.refdes: p for p in resnap_motif_clusters(plan, scattered)}

    def d(a, b):
        return math.hypot(out[a].x_mils - out[b].x_mils,
                          out[a].y_mils - out[b].y_mils)
    # Cin and Cout are pulled back to ~the canonical distance from L (1487).
    assert d("C1", "L1") == pytest.approx(1487, abs=150)
    assert d("L1", "C2") == pytest.approx(1487, abs=150)
    # U1 (not part of the motif) is untouched.
    assert out["U1"].x_mils == 8000 and out["U1"].y_mils == 3000


def test_resnap_motif_clusters_noop_without_selfcontained_motif():
    plan = _make_plan(
        parts=[{"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
                "status": "existing", "sheet": "main", "zone": "z"},
               {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
                "status": "existing", "sheet": "main", "zone": "z"}],
        nets=[{"name": "N", "pins": [{"refdes": "U1", "pin": "1"},
                                     {"refdes": "R1", "pin": "1"}]},
              {"name": "GND", "is_ground": True, "pins": [
                  {"refdes": "U1", "pin": "2"}, {"refdes": "R1", "pin": "2"}]}])
    placements = [
        PlacedPart(refdes="U1", sheet="main", x_mils=0, y_mils=0, rotation=0),
        PlacedPart(refdes="R1", sheet="main", x_mils=5000, y_mils=5000, rotation=0)]
    assert resnap_motif_clusters(plan, placements) == placements


def test_resnap_motif_clusters_retightens_ic_anchored_fb_divider():
    """A regulator's fb_divider Rtop/Rbot scattered by the shove are re-snapped
    to their canonical offsets from the (post-shove) IC position."""
    import math
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "REG", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"}],
        nets=[
            {"name": "VOUT", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "R1", "pin": "1"}]},
            {"name": "FB", "pins": [{"refdes": "R1", "pin": "2"},
                                    {"refdes": "R2", "pin": "1"},
                                    {"refdes": "U1", "pin": "5"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R2", "pin": "2"}, {"refdes": "U1", "pin": "2"}]}])
    # IC stays put; the two FB resistors are scattered far from it.
    scattered = [
        PlacedPart(refdes="U1", sheet="main", x_mils=2000, y_mils=2000, rotation=0),
        PlacedPart(refdes="R1", sheet="main", x_mils=8000, y_mils=7000, rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=500, y_mils=8000, rotation=0)]

    out = {p.refdes: p for p in resnap_motif_clusters(plan, scattered)}
    # U1 untouched (anchor); R1/R2 pulled back near it (canonical Rtop/Rbot are
    # 1500 to the side, 1000 apart -> both within ~1900 of the IC).
    assert out["U1"].x_mils == 2000 and out["U1"].y_mils == 2000

    def d(a, b):
        return math.hypot(out[a].x_mils - out[b].x_mils,
                          out[a].y_mils - out[b].y_mils)
    assert d("R1", "U1") < 1900 and d("R2", "U1") < 2100
    assert d("R1", "R2") == pytest.approx(1000, abs=150)   # canonical stack


def test_resnap_never_places_a_passive_inside_a_large_ic_body():
    """A canonical offset is a constant; an IC body is not.

    FOUND BY THE HUMAN BENCHMARK. nPM1300 draws a 2400 x 2800 body, and
    the fb_divider offset of 1300 mils sits inside it, so the resnap put
    a passive on top of the IC and scored 1000 for illegal geometry.
    This pass runs AFTER the pipeline's final overlap repair and nothing
    re-checks it, so the repair could not save it.

    The pin-count estimate the shove uses tops out at 1200 mils, which
    would call this clear. Only the real body says otherwise, so the
    real body has to be passed in.
    """
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "REG", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "zone": "z"}],
        nets=[
            {"name": "VOUT", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "3"}, {"refdes": "R1", "pin": "1"}]},
            {"name": "FB", "pins": [{"refdes": "R1", "pin": "2"},
                                    {"refdes": "R2", "pin": "1"},
                                    {"refdes": "U1", "pin": "5"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R2", "pin": "2"}, {"refdes": "U1", "pin": "2"}]}])
    scattered = [
        PlacedPart(refdes="U1", sheet="main", x_mils=2200, y_mils=2200,
                   rotation=0),
        PlacedPart(refdes="R1", sheet="main", x_mils=8000, y_mils=7000,
                   rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=500, y_mils=8000,
                   rotation=0)]

    # nPM1300's real body: 2400 x 2800 mils, so half is 1200 x 1400.
    half = {"U1": (1200, 1400), "R1": (60, 20), "R2": (60, 20)}
    out = {p.refdes: p for p in
           resnap_motif_clusters(plan, scattered, body_half=half)}

    ux, uy = out["U1"].x_mils, out["U1"].y_mils
    for ref in ("R1", "R2"):
        px, py = out[ref].x_mils, out[ref].y_mils
        inside = (abs(px - ux) < half["U1"][0] + half[ref][0]
                  and abs(py - uy) < half["U1"][1] + half[ref][1])
        assert not inside, (
            f"{ref} at ({px},{py}) sits inside U1's body at ({ux},{uy})")


def test_pushing_clear_keeps_the_side_the_motif_asked_for():
    """Sliding a part out must not move it to the opposite side.

    A cap placed below its IC belongs below it. Moving along the axis
    with the smaller correction, and keeping the sign, preserves the
    motif's shape while clearing the body.
    """
    from eda_agent.design.priors import _push_clear

    # IC at the origin, half 1200 x 1400. Target 300 right and 1300 up:
    # inside vertically, and y needs the smaller correction.
    tx, ty = _push_clear(300, 1300, 0, 0, (1200, 1400), (60, 20), 100)
    assert ty >= 1400 + 20, f"still inside vertically: {ty}"
    assert ty > 0, "must stay on the side the motif placed it"
    assert tx == 300, "the clear axis must not be disturbed"


# ---------------- resnap_decoupling_bank: the aligned column ---------------

def _bank_plan(n_caps: int):
    from eda_agent.design.plan import DesignPlan

    parts = [{"refdes": "U1", "lib_ref": "IC", "lib_path": "/x.SchLib"}]
    parts += [{"refdes": f"C{i}", "lib_ref": "C", "lib_path": "/x.SchLib"}
              for i in range(1, n_caps + 1)]
    vcc = [{"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "2"},
           {"refdes": "U1", "pin": "3"}]
    gnd = [{"refdes": "U1", "pin": "4"}]
    for i in range(1, n_caps + 1):
        vcc.append({"refdes": f"C{i}", "pin": "1"})
        gnd.append({"refdes": f"C{i}", "pin": "2"})
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": parts,
        "nets": [{"name": "VCC", "is_power": True, "pins": vcc},
                 {"name": "GND", "is_ground": True, "pins": gnd}],
    })


def _scattered(n_caps: int):
    """What the overlap shove leaves: a bank spread unevenly along y."""
    from eda_agent.design.layout import PlacedPart

    out = [PlacedPart(refdes="U1", sheet="main", x_mils=2000, y_mils=3000,
                      rotation=0)]
    ys = [3000, 4100, 2400, 5300, 1500, 6000]
    xs = [4000, 4100, 3900, 4000, 4200, 4000]
    for i in range(n_caps):
        out.append(PlacedPart(refdes=f"C{i + 1}", sheet="main",
                              x_mils=xs[i], y_mils=ys[i], rotation=270))
    return out


def test_a_scattered_bank_comes_back_as_one_aligned_column():
    """MEASURED on 154 public sheets: humans put a decoupling cap 450 mils
    from its nearest other cap and 88% share an axis. The priors pass
    reproduces that and the shove destroys it (1000 mils, 34% aligned)."""
    from eda_agent.design.priors import _BANK_PITCH_MILS, resnap_decoupling_bank

    out = resnap_decoupling_bank(_bank_plan(4), _scattered(4))
    caps = sorted((p for p in out if p.refdes.startswith("C")),
                  key=lambda p: p.y_mils)
    assert len({p.x_mils for p in caps}) == 1, "the bank does not share an x"
    gaps = [b.y_mils - a.y_mils for a, b in zip(caps, caps[1:])]
    assert gaps == [_BANK_PITCH_MILS] * 3, gaps


def test_the_bank_keeps_the_shove_ordering():
    """Re-sorting the caps would swap two and cross the wires reaching them."""
    from eda_agent.design.priors import resnap_decoupling_bank

    before = _scattered(4)
    out = resnap_decoupling_bank(_bank_plan(4), before)
    order_before = [p.refdes for p in sorted(
        (p for p in before if p.refdes.startswith("C")), key=lambda p: p.y_mils)]
    order_after = [p.refdes for p in sorted(
        (p for p in out if p.refdes.startswith("C")), key=lambda p: p.y_mils)]
    assert order_after == order_before


def test_a_pair_is_left_where_the_shove_put_it():
    """Two caps are not a column, and re-spacing a pair pulled one away from
    the pin it serves: +45% and +9% on the two 2-cap sheets of the first A/B."""
    from eda_agent.design.priors import resnap_decoupling_bank

    before = _scattered(2)
    out = resnap_decoupling_bank(_bank_plan(2), before)
    assert [(p.refdes, p.x_mils, p.y_mils) for p in out] == \
        [(p.refdes, p.x_mils, p.y_mils) for p in before]


def test_a_slot_on_top_of_another_part_is_refused():
    """The overlap the shove fixed must not come back."""
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.priors import resnap_decoupling_bank

    plan = _bank_plan(3)
    plan = plan.model_copy(deep=True)
    plan.parts.append(type(plan.parts[0])(
        refdes="R9", lib_ref="R", lib_path="/x.SchLib"))
    plan.nets[0].pins.append(type(plan.nets[0].pins[0])(refdes="R9", pin="1"))
    plan.nets[1].pins.append(type(plan.nets[0].pins[0])(refdes="R9", pin="2"))
    before = _scattered(3)
    # The column for these three lands at x=4000 (median x) with slots at
    # y=2600, 3000, 3400 about the median y. C2 (at 4100, 4100, the highest)
    # is due for the top slot. R9 sits exactly there, and on nothing else.
    slot = (4000, 3400)
    assert slot not in {(p.x_mils, p.y_mils) for p in before}
    before.append(PlacedPart(refdes="R9", sheet="main", x_mils=slot[0],
                             y_mils=slot[1], rotation=0))
    # Real body sizes, as the pipeline supplies them. Without them the
    # pin-count estimate makes a 2-pin part 900 mils wide and R9 would block
    # the neighbouring slots as well, which is a different (weaker) test.
    body_half = {"U1": (300, 300), "R9": (100, 50),
                 "C1": (80, 80), "C2": (80, 80), "C3": (80, 80)}
    out = resnap_decoupling_bank(plan, before, body_half=body_half)
    r9 = next(p for p in out if p.refdes == "R9")
    assert (r9.x_mils, r9.y_mils) == slot, "R9 must not move"
    c2 = next(p for p in out if p.refdes == "C2")
    assert (c2.x_mils, c2.y_mils) == (4100, 4100), (
        f"C2 was re-seated to {(c2.x_mils, c2.y_mils)}, onto or past R9")
    others = [p for p in out if p.refdes in ("C1", "C3")]
    assert all(p.x_mils == 4000 for p in others), "the rest still tightened"



# ------------- one oscillator per cap pair, and per IC ---------------------

def _osc_plan(extra_bridge: bool = False, second_ic: bool = False):
    """A crystal X1 with load caps C1/C2 on an MCU.

    ``extra_bridge`` adds R9 across the same two nodes: structurally it is
    also "a 2-pin part whose legs each carry a cap to ground", so it matches
    the crystal test too. ``second_ic`` gives the board a second oscillator
    on its own MCU, which must still be recognised.
    """
    from eda_agent.design.plan import DesignPlan

    parts = ["U1", "X1", "C1", "C2"]
    nets = [
        {"name": "XA", "pins": [{"refdes": "X1", "pin": "1"},
                                {"refdes": "U1", "pin": "1"},
                                {"refdes": "C1", "pin": "1"}]},
        {"name": "XB", "pins": [{"refdes": "X1", "pin": "2"},
                                {"refdes": "U1", "pin": "2"},
                                {"refdes": "C2", "pin": "1"}]},
    ]
    gnd = [{"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
           {"refdes": "U1", "pin": "3"}]
    if extra_bridge:
        parts.append("R9")
        nets[0]["pins"].append({"refdes": "R9", "pin": "1"})
        nets[1]["pins"].append({"refdes": "R9", "pin": "2"})
    if second_ic:
        parts += ["U2", "X2", "C3", "C4"]
        nets += [
            {"name": "YA", "pins": [{"refdes": "X2", "pin": "1"},
                                    {"refdes": "U2", "pin": "1"},
                                    {"refdes": "C3", "pin": "1"}]},
            {"name": "YB", "pins": [{"refdes": "X2", "pin": "2"},
                                    {"refdes": "U2", "pin": "2"},
                                    {"refdes": "C4", "pin": "1"}]},
        ]
        gnd += [{"refdes": "C3", "pin": "2"}, {"refdes": "C4", "pin": "2"},
                {"refdes": "U2", "pin": "3"}]
    nets.append({"name": "GND", "is_ground": True, "pins": gnd})
    return DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": r[0], "lib_path": "/x.SchLib"}
                  for r in parts],
        "nets": nets})


def test_a_plain_oscillator_is_still_found():
    from eda_agent.design.priors import _crystal_clusters

    clusters = _crystal_clusters(_osc_plan())
    assert len(clusters) == 1
    assert clusters[0][0] == "X1"
    assert set(clusters[0][1:3]) == {"C1", "C2"}


def test_a_part_bridging_the_same_nodes_does_not_claim_the_same_caps():
    """R9 passes the same structural test as the crystal.

    Both clusters kept means resnap_crystal_clusters places C1 and C2 twice
    and the two anchors are seated at the same point: on a PAL/NTSC decoder
    that produced six mutually contained parts, where the human sheet has
    none.
    """
    from eda_agent.design.priors import _crystal_clusters

    clusters = _crystal_clusters(_osc_plan(extra_bridge=True))
    assert len(clusters) == 1, f"two claims on one cap pair: {clusters}"
    caps = [c for entry in clusters for c in entry[1:3]]
    assert len(caps) == len(set(caps)), "a cap serves at most one oscillator"


def test_a_cap_shared_between_two_ICs_is_claimed_once():
    """The per-IC rule does NOT cover this, and that is why both exist.

    X1 and X2 sit on different ICs, so the one-per-IC rule keeps both, and
    they share C1 because it hangs off the node they have in common.
    resnap_crystal_clusters would then place C1 twice, once flanking each
    crystal. Verified by deleting the cap rule: the second cluster comes
    back and C1 appears in both.
    """
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.priors import _crystal_clusters

    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": r[0], "lib_path": "/x.SchLib"}
                  for r in ("U1", "U2", "X1", "X2", "C1", "C2", "C3")],
        "nets": [
            {"name": "XA", "pins": [{"refdes": "X1", "pin": "1"},
                                    {"refdes": "X2", "pin": "1"},
                                    {"refdes": "U1", "pin": "1"},
                                    {"refdes": "U2", "pin": "1"},
                                    {"refdes": "C1", "pin": "1"}]},
            {"name": "XB", "pins": [{"refdes": "X1", "pin": "2"},
                                    {"refdes": "U1", "pin": "2"},
                                    {"refdes": "C2", "pin": "1"}]},
            {"name": "XC", "pins": [{"refdes": "X2", "pin": "2"},
                                    {"refdes": "U2", "pin": "2"},
                                    {"refdes": "C3", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                      {"refdes": "C3", "pin": "2"}, {"refdes": "U1", "pin": "3"},
                      {"refdes": "U2", "pin": "3"}]},
        ]})
    clusters = _crystal_clusters(plan)
    caps = [c for entry in clusters for c in entry[1:3]]
    assert len(caps) == len(set(caps)), f"C1 claimed twice: {clusters}"


def test_two_oscillators_on_two_ICs_are_both_kept():
    """The limit is one per IC, not one per board."""
    from eda_agent.design.priors import _crystal_clusters

    clusters = _crystal_clusters(_osc_plan(second_ic=True))
    assert len(clusters) == 2
    assert {e[3] for e in clusters} == {"U1", "U2"}


def test_two_oscillators_claiming_one_IC_keep_only_one():
    """resnap_crystal_clusters seats an anchor from its IC alone, so two on
    one IC are seated at the same point."""
    from eda_agent.design.priors import _crystal_clusters

    plan = _osc_plan(second_ic=True)
    # Move the second oscillator onto U1 as well.
    for net in plan.nets:
        if net.name in ("YA", "YB"):
            for pin in net.pins:
                if pin.refdes == "U2":
                    pin.refdes = "U1"
    clusters = _crystal_clusters(plan)
    assert len(clusters) == 1
    assert {e[3] for e in clusters} == {"U1"}


def test_the_arbitration_does_not_depend_on_iteration_order():
    from eda_agent.design.priors import _crystal_clusters

    plan = _osc_plan(extra_bridge=True)
    first = _crystal_clusters(plan)
    reordered = plan.model_copy(deep=True)
    reordered.parts.reverse()
    reordered.nets.reverse()
    assert _crystal_clusters(reordered) == first


def test_a_self_contained_motif_is_not_reseated_inside_another_part():
    """A motif with no ic_anchor is seated on its members' own centroid.

    ``_push_clear`` only clears the motif's OWN anchor, and the
    self-contained branch does not even run it, so nothing consulted the
    rest of the sheet. On a PAL/NTSC decoder the diode bridge's centroid
    fell inside U10 and all four diodes were re-seated inside the IC's
    rectangle, on a canvas the overlap shove had left clean.
    """
    from eda_agent.design.layout import PlacedPart
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.priors import resnap_motif_clusters

    # A real diode bridge: two AC signal nodes, a power rail and a ground,
    # each diode across one AC node and one rail. Built to the catalogue's
    # own pattern, because a fixture the matcher does not recognise leaves
    # the pass with nothing to do and the test passes for that reason: an
    # earlier version of this asserted nothing, and survived deleting the
    # collision check it exists to guard.
    nets = [
        {"name": "AC1", "pins": [{"refdes": "D1", "pin": "1"},
                                 {"refdes": "D3", "pin": "1"}]},
        {"name": "AC2", "pins": [{"refdes": "D2", "pin": "1"},
                                 {"refdes": "D4", "pin": "1"}]},
        {"name": "VPLUS", "is_power": True,
         "pins": [{"refdes": "D1", "pin": "2"}, {"refdes": "D2", "pin": "2"},
                  {"refdes": "U1", "pin": "1"}]},
        {"name": "VMINUS", "is_ground": True,
         "pins": [{"refdes": "D3", "pin": "2"}, {"refdes": "D4", "pin": "2"},
                  {"refdes": "U1", "pin": "2"}]},
        {"name": "IC", "pins": [{"refdes": "U1", "pin": "3"},
                                {"refdes": "U1", "pin": "4"}]},
    ]
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": r[0], "lib_path": "/x.SchLib"}
                  for r in ("U1", "D1", "D2", "D3", "D4")],
        "nets": nets})
    before = [PlacedPart(refdes="U1", sheet="main", x_mils=5000, y_mils=5000,
                         rotation=0)]
    for i, (dx, dy) in enumerate(((-3000, 0), (3000, 0), (0, -3000),
                                  (0, 3000)), start=1):
        before.append(PlacedPart(refdes=f"D{i}", sheet="main",
                                 x_mils=5000 + dx, y_mils=5000 + dy,
                                 rotation=0))
    body_half = {"U1": (2000, 2000)}
    body_half.update({f"D{i}": (60, 60) for i in range(1, 5)})
    from eda_agent.design.motifs import recognize_motifs
    assert any(m.motif_name == "diode_bridge" for m in recognize_motifs(plan)), (
        "the fixture must actually match the motif, or this pass does "
        "nothing and the test proves nothing")

    out = {p.refdes: p for p in resnap_motif_clusters(plan, before,
                                                      body_half=body_half)}
    for i in range(1, 5):
        d = out[f"D{i}"]
        inside = (abs(d.x_mils - 5000) < 2000 + 60
                  and abs(d.y_mils - 5000) < 2000 + 60)
        assert not inside, (
            f"D{i} was re-seated at ({d.x_mils}, {d.y_mils}), inside U1")
