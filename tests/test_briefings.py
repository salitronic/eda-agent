# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What to know before working on one kind of document: tool_guide's
briefing for a document_kind given with no task.

The server instructions keep only what applies to every document; each
kind's own rules are here, so the instructions have room under what a
client keeps.
"""

from __future__ import annotations

import pytest

from eda_agent.briefings import KINDS, briefing
from eda_agent.server import build_server_instructions
from eda_agent.tools.guidance import DOCUMENT_KINDS, guidance_for

from tests.test_server_instructions import _SURFACES, _tools_named


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_has_an_altium_briefing_and_tool_guide_takes_it(kind):
    assert briefing(kind, "altium").strip()
    assert kind in DOCUMENT_KINDS


def test_library_gives_both_libraries():
    both = briefing("library", "altium")
    assert briefing("pcblib", "altium") in both and briefing("schlib", "altium") in both


@pytest.mark.parametrize("backend", ["altium", "kicad", "easyeda"])
@pytest.mark.parametrize("kind", KINDS)
def test_every_tool_a_briefing_names_exists_on_its_backend(kind, backend):
    for name in _tools_named(briefing(kind, backend)):
        assert name in _SURFACES[backend], (
            f"the {backend} {kind} briefing names {name!r}, which that backend "
            f"does not register")


def test_a_kind_with_no_task_returns_its_briefing_and_recipe_titles():
    got = guidance_for("", "board")
    assert got["briefing"] == briefing("board", "altium")
    assert got["recipe_titles"] and all(isinstance(t, str) for t in got["recipe_titles"])
    assert "recipes" not in got


def test_a_task_still_gets_recipes_in_full():
    got = guidance_for("delete silkscreen from a footprint", "pcblib")
    assert got["recipes"] and "briefing" not in got


def test_a_library_recipe_answers_either_library():
    titles = guidance_for("", "pcblib")["recipe_titles"]
    assert "Delete primitives inside a footprint" in titles
    assert set(titles) <= set(guidance_for("", "library")["recipe_titles"])


def test_the_briefing_follows_the_backend_asked_or_the_servers():
    assert "easyeda_emit_template" in guidance_for("", "schematic", "easyeda")["briefing"]
    assert "sch_draw_template" in guidance_for(
        "", "schematic", server_backend="altium")["briefing"]
    assert guidance_for("", "board", "kicad")["briefing"] == ""


@pytest.mark.parametrize("backend", ["altium", "both", "easyeda", "kicad"])
def test_the_preamble_points_at_the_briefings_only_where_there_are_some(backend):
    text = build_server_instructions("full", backend)
    kinds = [k for k in KINDS if briefing(k, backend)]
    assert ("document_kind and no task" in text) is bool(kinds)
    for k in kinds:
        assert k in text
