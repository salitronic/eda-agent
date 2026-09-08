# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The same plan must lay out identically in every process.

Python randomises string hashes per process, so iterating a set of refdes
strings, or of ``("C", refdes)`` tuples, yields a different order in every
run. Anything that takes the FIRST element of such an iteration, or that
relies on sort stability to break a tie, then produces a different schematic
each time the program starts.

A single pytest process cannot see any of this: it has one hash seed for its
whole lifetime. Measured, with an earlier instance of this bug put back: 1301
of 1302 tests passed at three different seeds. So these tests spawn
subprocesses; there is no in-process substitute.

The fixture is deliberately built out of the two ambiguities actually found
on public hardware, both of which need TWO interchangeable parts to appear:

  - C1 and C2 are identical caps on the crystal's XA node, so
    ``priors._load_cap`` has two equally valid answers;
  - C8 and C9 are identical bypass caps on +5V at the same IC, so the motif
    matcher produces two equally-scoring bypass_cap matches.

Both were found by running one corpus sweep twice, on identical code, and
getting 67 findings and then 74.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

_SEEDS = ("1", "2", "3")

_PLAN_LITERAL = """
    PLAN = {
        "spec": "t", "summary": "t",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [{"refdes": r, "lib_ref": k, "lib_path": "/x.SchLib"}
                  for r, k in [("U1", "MCU"), ("Y1", "XTAL"), ("C1", "C"),
                               ("C2", "C"), ("C3", "C"), ("C8", "C"),
                               ("C9", "C"), ("R1", "R")]],
        "nets": [
            {"name": "XA", "pins": [{"refdes": "Y1", "pin": "1"},
                                    {"refdes": "U1", "pin": "1"},
                                    {"refdes": "C1", "pin": "1"},
                                    {"refdes": "C2", "pin": "1"}]},
            {"name": "XB", "pins": [{"refdes": "Y1", "pin": "2"},
                                    {"refdes": "U1", "pin": "2"},
                                    {"refdes": "C3", "pin": "1"}]},
            {"name": "+5V", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "3"},
                      {"refdes": "C8", "pin": "1"},
                      {"refdes": "C9", "pin": "1"},
                      {"refdes": "R1", "pin": "1"}]},
            {"name": "SIG", "pins": [{"refdes": "U1", "pin": "5"},
                                     {"refdes": "R1", "pin": "2"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "C1", "pin": "2"},
                      {"refdes": "C2", "pin": "2"},
                      {"refdes": "C3", "pin": "2"},
                      {"refdes": "C8", "pin": "2"},
                      {"refdes": "C9", "pin": "2"},
                      {"refdes": "U1", "pin": "4"}]},
        ],
    }
"""

_SCRIPT = textwrap.dedent("""
    import hashlib
    from eda_agent.design.plan import DesignPlan
    from eda_agent.design.composer import compose_layout
    from eda_agent.design.priors import _infer_crystal_roles
""") + textwrap.dedent(_PLAN_LITERAL) + textwrap.dedent("""
    plan = DesignPlan.model_validate(PLAN)
    placements = compose_layout(plan).placements
    blob = repr(sorted((p.refdes, p.x_mils, p.y_mils, p.rotation)
                       for p in placements))
    blob += repr(sorted(_infer_crystal_roles(plan).items()))
    print(hashlib.sha1(blob.encode()).hexdigest())
""")


def _plan():
    from eda_agent.design.plan import DesignPlan

    namespace: dict = {}
    exec(textwrap.dedent(_PLAN_LITERAL), namespace)
    return DesignPlan.model_validate(namespace["PLAN"])


def _run(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    out = subprocess.run([sys.executable, "-c", _SCRIPT], env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-1500:]
    return out.stdout.strip()


def test_the_same_plan_places_identically_under_every_hash_seed():
    """The behavioural guard. Verified against both defects it exists for.

    Reverting ``priors._load_cap`` to iterate its set unsorted gives two
    distinct results over eight seeds; reverting the motif arbitration to a
    single reverse sort gives three. With both in place, one.
    """
    results = {seed: _run(seed) for seed in _SEEDS}
    assert len(set(results.values())) == 1, (
        "the same plan produced different placements under different hash "
        f"seeds: {results}")


def test_motif_arbitration_ignores_the_order_matches_arrive_in():
    """In-process companion, and the cheaper failure message.

    ``find_all_matches`` enumerates through NetworkX, over sets of
    ``("C", refdes)`` tuples, so its ORDER is not reproducible even though
    its contents are. resolve_matches must not depend on it: two matches of
    one motif on different parts score identically on every field of the
    sort key, and a stable sort then keeps whatever order it was handed.
    """
    import random

    from eda_agent.design.motifs import find_all_matches, resolve_matches

    plan = _plan()
    matches = find_all_matches(plan)
    if len(matches) < 2:
        pytest.skip("fixture no longer produces competing matches")

    def resolved(seq):
        return [(m.motif_name, tuple(sorted(m.components)))
                for m in resolve_matches(seq)]

    baseline = resolved(list(matches))
    rng = random.Random(0)
    for _ in range(12):
        shuffled = list(matches)
        rng.shuffle(shuffled)
        assert resolved(shuffled) == baseline, (
            "arbitration depends on the order the matches arrived in")


def test_the_guard_fixture_really_is_ambiguous():
    """A determinism test over an unambiguous fixture proves nothing.

    An earlier version of this guard passed against the reverted bug because
    its 8-part ring was too symmetric to have two valid answers. So assert
    the ambiguity itself: two caps ``_load_cap`` could equally return, and
    more than one competing motif match.
    """
    from eda_agent.design.motifs import find_all_matches

    plan = _plan()
    xa = {pr.refdes for n in plan.nets if n.name == "XA" for pr in n.pins}
    assert {"C1", "C2"} <= xa, (
        "the crystal node must carry two interchangeable caps, or "
        "_load_cap has only one answer and the guard cannot fail")
    assert len(find_all_matches(plan)) >= 2, (
        "the fixture must produce competing motif matches")
