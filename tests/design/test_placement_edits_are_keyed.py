# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""An edit that cannot be read back is not training data.

Placement priors are looked up by (part_role, anchor_role). The consumer,
``apply_placement_priors``, skips any part whose role is empty, so a row
recorded with either side blank can never be matched by anything.

MEASURED on the corpus in ~/.eda-agent: 27 recorded edits, every one with
an empty part_role, aggregating into a single '_unknown_|_unknown_'
bucket. The aggregator reported "1 role-pair prior from 27 edits" and the
pipeline could apply none of it. Every stage reported success, which is
why it sat unnoticed from May.

Two changes are held here. The learner infers roles the same way the
consumer does before deciding a row is unkeyed, and refuses the row when
it still is. The aggregator never emits an unreachable bucket, and says
how many rows it dropped instead of letting a low pair count look like a
small corpus.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
TRAINER = REPO / "scripts" / "train" / "build_placement_priors.py"

_spec = importlib.util.spec_from_file_location("build_placement_priors", TRAINER)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["build_placement_priors"] = _mod
_spec.loader.exec_module(_mod)

aggregate = _mod.aggregate
UNKNOWN = _mod.UNKNOWN


def _row(part_role="decoup", anchor_role="ic", dx=400, dy=200):
    return {
        "part_role": part_role,
        "anchor_role": anchor_role,
        "dx_mils": dx,
        "dy_mils": dy,
        "rot_delta_deg": 0,
    }


# ---------------------------------------------------------------------------
# The aggregator.
# ---------------------------------------------------------------------------

def test_a_keyed_pair_still_produces_a_prior():
    """The fix must not cost the behaviour that works."""
    out = aggregate([_row(), _row(dx=420, dy=210)], min_samples=2)
    assert out["n_pairs"] == 1
    assert "decoup|ic" in out["priors"]
    assert out["edits_unkeyed"] == 0


@pytest.mark.parametrize("part_role,anchor_role", [
    ("", "ic"),          # the part was never tagged
    ("decoup", ""),      # the anchor was never tagged
    ("", ""),            # the case actually found on disk
])
def test_an_unkeyed_pair_never_reaches_the_artifact(part_role, anchor_role):
    """apply_placement_priors skips an empty role, so this cannot match.

    Writing it anyway inflates n_pairs and ships a preference that
    nothing is able to apply.
    """
    out = aggregate([_row(part_role, anchor_role)] * 4, min_samples=2)
    assert out["priors"] == {}
    assert out["n_pairs"] == 0
    for key in out["priors"]:
        assert UNKNOWN not in key


def test_dropped_rows_are_reported_not_hidden():
    """A low pair count must not look like a small corpus.

    The old output said "1 role-pair prior from 27 edits", which reads as
    a corpus that is merely young rather than one that is entirely
    unusable.
    """
    rows = [_row()] * 3 + [_row("", "")] * 27
    out = aggregate(rows, min_samples=2)
    assert out["n_edits"] == 30
    assert out["n_pairs"] == 1
    assert out["edits_unkeyed"] == 27, (
        "the aggregator must say how many rows it could not key, or a "
        "corpus of noise is indistinguishable from a corpus of three")


def test_the_real_corpus_reports_itself_as_unusable():
    """The 27 rows actually on disk, run through the fixed aggregator."""
    rows = [_row("", "") for _ in range(27)]
    out = aggregate(rows, min_samples=2)
    assert (out["n_pairs"], out["edits_unkeyed"]) == (0, 27)


# ---------------------------------------------------------------------------
# The learner.
# ---------------------------------------------------------------------------

def test_the_learner_infers_roles_the_way_the_consumer_does():
    """Recording the raw planner role keys rows differently from reads.

    apply_placement_priors tags untagged decoupling caps and crystal
    clusters structurally before looking a prior up. A learner that skips
    that inference writes 'decoup' rows as '' and loses them.
    """
    import inspect

    from eda_agent.design import learner

    source = inspect.getsource(learner)
    assert "_infer_decoup_roles" in source
    assert "_infer_crystal_roles" in source


def test_the_learner_refuses_an_unkeyed_row():
    """Recorded noise is worse than a recorded gap: it counts."""
    import inspect

    from eda_agent.design import learner

    source = inspect.getsource(learner.learn_from_layout)
    assert "rows_skipped_unkeyed" in source, (
        "the learner must skip and COUNT a row it cannot key, so the "
        "corpus size means what it says")


# ---------------------------------------------------------------------------
# The anchor must be the one the consumer will look up.
# ---------------------------------------------------------------------------

def test_a_decoupling_cap_anchors_to_its_rail_mate_not_its_neighbour():
    """The spatial fallback fires exactly for decoupling caps.

    A part whose only nets are power and ground has no signal neighbour,
    which is the definition of a bypass cap. apply_placement_priors
    looks its prior up against the IC it SHARES A RAIL WITH, so an
    anchor chosen by distance is recorded under a key nothing reads.

    Here C1 sits beside R1 and shares VCC with U1. The rail-mate is the
    right answer even though the resistor is nearer.
    """
    from eda_agent.design.learner import _pick_anchor
    from eda_agent.design.plan import DesignPlan

    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "IC", "lib_path": "/x.SchLib"},
            {"refdes": "C1", "lib_ref": "C", "lib_path": "/x.SchLib"},
            {"refdes": "R1", "lib_ref": "R", "lib_path": "/x.SchLib"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "2"},
                {"refdes": "U1", "pin": "3"}, {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "C1", "pin": "2"}, {"refdes": "U1", "pin": "4"}]},
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "1"}, {"refdes": "R1", "pin": "2"}]},
        ],
    })
    # R1 is far nearer than U1 in the pre-edit canvas.
    pre = {
        "C1": {"x": 1000, "y": 1000},
        "R1": {"x": 1100, "y": 1000},
        "U1": {"x": 9000, "y": 9000},
    }
    assert _pick_anchor("C1", plan, pre) == "U1", (
        "a bypass cap must anchor to the IC on its rail, not to whatever "
        "happens to sit next to it")


def test_the_spatial_fallback_is_still_there_for_a_part_with_no_rail():
    """Last resort, not removed: something is better than no row."""
    from eda_agent.design.learner import _pick_anchor
    from eda_agent.design.plan import DesignPlan

    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "J1", "lib_ref": "CONN", "lib_path": "/x.SchLib"},
            {"refdes": "R9", "lib_ref": "R", "lib_path": "/x.SchLib"},
        ],
        "nets": [{"name": "GND", "is_ground": True, "pins": [
            {"refdes": "J1", "pin": "1"}, {"refdes": "R9", "pin": "1"}]}],
    })
    pre = {"J1": {"x": 0, "y": 0}, "R9": {"x": 500, "y": 0}}
    assert _pick_anchor("J1", plan, pre) == "R9"


def test_the_two_anchor_pickers_agree():
    """learner and priors each carry a copy, by design.

    priors._pick_anchor's own docstring says it is "duplicated rather
    than imported ... kept in sync by docstring + unit test parity".
    This is that test: the rail-mate preference was added to one of them
    first, and nothing would have noticed.
    """
    import inspect

    from eda_agent.design import learner, priors

    for mod, fn in ((learner, learner._pick_anchor), (priors, priors._pick_anchor)):
        source = inspect.getsource(fn)
        assert "_decoupling_rail_anchor" in source, (
            f"{mod.__name__}._pick_anchor does not prefer the rail-mate, so "
            f"the two copies disagree about what a decoupling cap anchors to")
