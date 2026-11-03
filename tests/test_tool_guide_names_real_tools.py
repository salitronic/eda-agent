# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every tool tool_guide names must exist on the backend it claims.

The guide exists because capabilities were reported absent when they
were present. A guide that sends a caller to a renamed or deleted tool
reproduces that failure with more confidence behind it, which is worse
than having no guide.

So the names are checked against the live surface per backend, not
against a list written next to them. The ``avoid`` entries are checked
too: they are the half a caller reaches for first, and an ``avoid`` that
names nothing real teaches a distinction that no longer exists.
"""

from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools.guidance import (
    _NOT_POSSIBLE,
    _RECIPES,
    DOCUMENT_KINDS,
    guidance_for,
)

_BACKENDS = ("altium", "kicad", "easyeda")


def _surface(backend: str, toolset: str = "full") -> set[str]:
    """Register one backend into a throwaway registry and list it.

    RESTORES THE ACTIVE BACKEND. register_backend records which backend
    was registered in a process-global, so enumerating all three at
    import time leaves the LAST one active for every test collected
    afterwards. That is not hypothetical: it flipped the active backend
    to easyeda and made the autonomy guide's stage tools disagree with
    the state-machine playbooks, in a test file that neither imports nor
    mentions this one.
    """
    from eda_agent.core.backends import _REGISTERED, set_active_backend
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    previous = _REGISTERED
    try:
        registry = ToolRegistry()
        register_backend(registry, backend, toolset)
        # Members of a family tool count: tool_guide's reply says how to
        # reach each one (its ``reach``).
        return ({t.name for t in asyncio.run(registry.list_tools())}
                | set(getattr(registry, "hidden", {}) or {}))
    finally:
        set_active_backend(previous or "")


_SURFACES = {b: _surface(b) for b in _BACKENDS}


def _entries():
    """(label, backend, tool_name) for every tool the guide names."""
    for r in _RECIPES:
        for backend in r["backends"]:
            for name in r["use"]:
                yield f"recipe {r['task']!r} use", backend, name
            for entry in r["avoid"]:
                scope = entry[2] if len(entry) > 2 else r["backends"]
                if backend not in scope:
                    continue
                yield f"recipe {r['task']!r} avoid", backend, entry[0]
    for d in _NOT_POSSIBLE:
        for backend in d["backends"]:
            for name in d["do_instead"]:
                yield f"not_possible {d['capability']!r}", backend, name


def test_the_surface_was_actually_enumerated():
    """A registry that returned nothing would make every check vacuous."""
    for backend, tools in _SURFACES.items():
        assert len(tools) > 50, (
            f"{backend} registered only {len(tools)} tools; the enumeration "
            f"broke and this file is checking against an empty set")
        assert "tool_guide" in tools, (
            f"tool_guide is not registered on {backend}")


@pytest.mark.parametrize("label,backend,name", sorted(set(_entries())))
def test_every_named_tool_exists_on_that_backend(label, backend, name):
    assert name in _SURFACES[backend], (
        f"{label} names {name!r}, which does not exist on the {backend} "
        f"backend. Either the tool was renamed and the guide now sends "
        f"callers nowhere, or the recipe claims a backend it does not "
        f"apply to")


def test_the_guide_names_at_least_one_tool():
    """Guards the parametrization above against collapsing to nothing."""
    assert len(set(_entries())) >= 10


@pytest.mark.parametrize("recipe", _RECIPES, ids=lambda r: r["task"])
def test_every_recipe_is_well_formed(recipe):
    assert recipe["document_kind"] in DOCUMENT_KINDS
    assert recipe["use"], f"{recipe['task']} names no tool to use"
    assert recipe["note"].strip(), f"{recipe['task']} has no note"
    for backend in recipe["backends"]:
        assert backend in _BACKENDS
    # A bare tool name passed every check above: its third letter was read
    # as the backend scope, so it was skipped here and shown as {"tool": "p",
    # "why": "c"} by tool_guide.
    for entry in recipe["avoid"]:
        assert isinstance(entry, tuple) and len(entry) in (2, 3), (
            f"{recipe['task']}: avoid entry {entry!r} is not (tool, why[, backends])")
        assert entry[1].strip(), f"{recipe['task']}: avoid {entry[0]} gives no reason"
        if len(entry) == 3:
            assert isinstance(entry[2], tuple), (
                f"{recipe['task']}: avoid {entry[0]} scope is not a tuple")


def test_an_unknown_document_kind_is_refused_not_ignored():
    out = guidance_for(task="anything", document_kind="footprint")
    assert out["ok"] is False
    assert "document_kind must be one of" in out["reason"]


def test_a_miss_says_so_rather_than_looking_like_a_verdict():
    """An empty result must not read as "the server cannot do this"."""
    out = guidance_for(task="calibrate the coffee machine")
    assert out["ok"] is True
    assert out["matched"] == 0
    assert "tool_catalog" in out["note"]


def test_the_library_delete_trap_is_reachable_from_the_mistake():
    """The recorded failure was reaching for obj_delete in a library.

    Searching the words someone uses while making that mistake has to
    surface the recipe, otherwise the guide only helps a caller who
    already knows the answer.
    """
    out = guidance_for(task="delete silkscreen from a footprint",
                       document_kind="library", backend="altium")
    assert out["matched"] >= 1
    tools = [t for r in out["recipes"] for t in r["use"]]
    assert "lib_delete_footprint_primitives" in tools
    avoided = [a["tool"] for r in out["recipes"] for a in r["avoid"]]
    assert "obj_delete" in avoided


def test_backend_filtering_does_not_offer_altium_tools_on_kicad():
    out = guidance_for(task="mechanical layer", backend="kicad")
    assert all("altium" not in r["backends"] or "kicad" in r["backends"]
               for r in out["recipes"])


def test_a_recipe_never_lists_a_tool_as_both_use_and_avoid():
    """Telling a caller to use and not use the same tool answers nothing.

    This happened: one recipe covered libraries and boards together, so
    it recommended the library tool and warned against it in the same
    reply. Splitting by document kind is what fixed it, and this keeps
    the two apart.
    """
    for r in _RECIPES:
        avoided = {e[0] for e in r["avoid"]}
        overlap = avoided & set(r["use"])
        assert not overlap, (
            f"{r['task']!r} both recommends and warns against "
            f"{sorted(overlap)}")


@pytest.mark.parametrize("task,document_kind,expected_tool", [
    # The four capability questions that were answered wrongly in
    # practice. Each must reach the recipe that answers IT, not merely
    # some recipe sharing a verb.
    ("write MECHKIND on a board", "board", "pcb_set_mech_layer_kind"),
    ("rename board mechanical layers", "board", "pcb_set_mech_layers"),
    ("library scoped primitive delete", "library",
     "lib_delete_footprint_primitives"),
    ("delete a component parameter", "library", "obj_delete"),
])
def test_the_questions_that_were_answered_wrongly(task, document_kind,
                                                  expected_tool):
    """Ranking, not first-match.

    'delete a component parameter' hits the footprint-primitive recipe
    on the word 'delete' alone. Returning that one first was a confident
    wrong answer, which is worse than no answer, so the BEST match has
    to come first rather than the earliest in the table.
    """
    out = guidance_for(task=task, document_kind=document_kind,
                       backend="altium")
    assert out["recipes"], f"{task!r} matched nothing"
    assert expected_tool in out["recipes"][0]["use"], (
        f"{task!r} ranked {out['recipes'][0]['task']!r} first, which does "
        f"not name {expected_tool}")


def test_enumerating_a_backend_does_not_leave_it_active():
    """The leak this file caused, pinned.

    register_backend records the backend in a process-global. Both
    helpers here enumerate all three at import time, so without a
    restore the LAST one stays active for every test collected
    afterwards. It flipped the active backend to easyeda and broke
    tests/design/test_autonomy.py, a file that neither imports nor
    mentions this one, which is the hardest kind of failure to trace
    back.
    """
    from eda_agent.core import backends

    before = backends._REGISTERED
    for backend in _BACKENDS:
        _surface(backend)
        assert backends._REGISTERED == before, (
            f"enumerating {backend} left it active; every test that runs "
            f"after this module now resolves against the wrong backend")


@pytest.mark.parametrize("task,expected", [
    # Dead ends are ranked, not first-match. "create test points" used
    # to return the thieving entry, because both shared the word
    # "create" through a do_instead tool name and thieving sat earlier
    # in the table. Same defect as the recipes had, in the list I
    # forgot to rank.
    ("create test points", "Create test points on the board"),
    ("add teardrops", "Add or remove teardrops"),
    ("tune the length of a net", "Tune track length with a serpentine"),
    ("thieving copper", "Place thieving copper"),
    ("delete the project", "Delete a whole project"),
])
def test_the_best_dead_end_comes_first(task, expected):
    out = guidance_for(task=task, backend="easyeda")
    assert out["not_possible"], f"{task!r} matched no dead end"
    assert out["not_possible"][0]["capability"] == expected, (
        f"{task!r} ranked {out['not_possible'][0]['capability']!r} first")


def test_no_dead_end_bundles_two_unrelated_capabilities():
    """Bundling gives an entry an unearned keyword surface.

    "Place thieving copper, or tune track length" was one entry, and it
    won queries belonging to neither half. Splitting it is what let the
    test-point entry rank for its own subject.
    """
    for d in _NOT_POSSIBLE:
        cap = d["capability"]
        assert ", or " not in cap, (
            f"{cap!r} covers two capabilities; split it so each ranks on "
            f"its own words")


@pytest.mark.parametrize("entry", _NOT_POSSIBLE,
                         ids=lambda d: d["capability"])
def test_every_dead_end_says_why(entry):
    """An impossibility with no reason invites re-investigation, which
    is the whole thing this list exists to stop."""
    assert entry["why"].strip()
    assert len(entry["why"]) > 40, (
        f"{entry['capability']!r} gives too thin a reason to be trusted")
