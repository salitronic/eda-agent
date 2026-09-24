# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""tool_guide must answer the questions that actually went wrong.

The curated table holds ten recipes against seven hundred tools, and an
empty answer from it reads as "no such capability" however carefully the
note says otherwise. That reading has been made and reported more than
once: a session concluded the API could not put a STEP model on a board
and built a throwaway library instead, and another concluded a polygon
property was not exposed.

So this does not count recipes. A count passes while the guide answers
nothing anybody asked, which is the failure mode the codebase keeps
catching in staleness checks keyed on a label.

It asks the REAL questions, in the words a stuck caller would use, and
requires the right tool to come back. Every case here is a session that
was lost to not finding it.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.guidance import derived_recipes, guidance_for


def _docs() -> dict[str, str]:
    """Docstrings off the live registry, exactly as the tool reads them."""
    import asyncio

    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_backend(reg, "both", "full")
    return {t.name: (t.description or "")
            for t in asyncio.run(reg.list_tools())}


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    d = _docs()
    # Guard the guard. With no docstrings every assertion below would
    # fail for the wrong reason, and with a handful they would pass
    # vacuously on a tiny surface.
    assert len(d) > 300, f"only {len(d)} tool docstrings; the registry did not load"
    return d


#: (what a stuck caller types, the tool that answers it)
#:
#: Each was a real session. The wording is deliberately the question
#: rather than the answer: a caller who already knew the tool name would
#: not be asking.
_QUESTIONS = [
    ("place a 3d step model on the board", "pcb_place_3d_body"),
    ("remove dead copper from a polygon", "pcb_modify_polygon"),
    ("delete a project variant", "proj_delete_variant"),
    ("repour polygons", "pcb_repour_polygons"),
]


@pytest.mark.parametrize("question,expected", _QUESTIONS)
def test_a_stuck_caller_is_pointed_at_the_right_tool(question, expected, docs):
    found = [n for r in derived_recipes(question, docs, limit=6)
             for n in r["use"]]
    assert expected in found, (
        f"asking {question!r} did not surface {expected}. It returned "
        f"{found}. A caller who cannot find the tool concludes the "
        f"capability is absent, which is how this list was written.")


def test_the_redirect_in_a_docstring_becomes_a_see_also(docs):
    """The derivation's whole claim is that docstrings already hold the
    recipes. If no redirect is ever extracted, it is just a search."""
    seen = []
    for question, _ in _QUESTIONS:
        for r in derived_recipes(question, docs, limit=6):
            seen.extend(r["see_also"])
    assert seen, (
        "no docstring redirect was extracted for any of the known "
        "questions, so the derivation is finding tools but not the "
        "'use X instead' relationships it exists to surface")


def test_a_miss_still_says_what_to_do_next(docs):
    """An empty answer must not read as an absent capability."""
    out = guidance_for("something nobody has ever asked about xyzzy",
                       docs=docs)
    assert out["ok"] is True
    assert out["note"], "a miss returned no recipes and no advice"
    assert "tool_catalog" in out["note"]


def test_a_curated_recipe_still_wins_over_a_derived_one(docs):
    """A relevant hand-written recipe comes first. It no longer hides the rest.

    A curated recipe carries the prerequisites and the thing to avoid, so
    when one genuinely answers the question it must lead the reply.

    This used to assert that derivation did not run at all whenever a
    curated recipe matched. That enforced the defect: a recipe sharing a
    single common word counted as a match, so the docstring search almost
    never ran, and the tool that answered the question was never shown.
    Measured 2026-09-23, through the real entry point: 2 of 11 questions
    that cost sessions that week were answered, and several were answered
    with the mechanical-layers recipe. Ordering is what protects the
    curated answer; suppression only protected wrong ones.
    """
    out = guidance_for("delete primitives inside a footprint", docs=docs)
    assert out["matched"] > 0, "the curated table stopped matching its own entry"
    assert out["recipes"][0]["use"] == ["lib_delete_footprint_primitives"], (
        "the curated recipe that answers this is not first in the reply")
    # The root cause, asserted directly rather than through whichever
    # question happens to depend on it. It was caught only by "delete a
    # project variant" matching the stencil recipe, and once that match
    # was fixed nothing noticed the search being switched off.
    assert out["derived"], (
        "a curated match suppressed the docstring search again, so any "
        "question a recipe half-matches never sees the tool that answers it")
    curated = {t for r in out["recipes"] for t in r["use"]}
    repeated = [d["use"] for d in out["derived"] if set(d["use"]) <= curated]
    assert not repeated, (
        f"derived results repeat tools the curated recipe already named: "
        f"{repeated}")
