# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The server preamble must be true, and must actually reach the client.

tool_catalog and tool_guide only help a caller who thinks to call them,
and the recorded failures are the ones where nobody did: a capability
reported ABSENT four times while the tool existed under another
namespace. Server instructions are the only text a client sees before
choosing anything, which is why the pointer lives there.

That makes it a claim about the code, and claims about code need a
guard. Everything the preamble names is checked against the live
surface: the tools, the namespaces, and the mils convention. A preamble
that confidently names something gone is worse than none, because it is
read first and trusted most.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from eda_agent.server import SERVER_INSTRUCTIONS, build_server_instructions

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
        return {t.name for t in asyncio.run(registry.list_tools())}
    finally:
        set_active_backend(previous or "")


_SURFACES = {b: _surface(b) for b in _BACKENDS}


#: Every namespace the preamble is allowed to name a tool from. The
#: pattern demands a suffix, so the bare "sch_" and "obj_" the preamble
#: uses to teach the namespace split are not read as tool names.
# app is in the list because the preamble now names the interface-driving
# tools. Without it those names were never checked against the surface,
# so a renamed app_ tool would have gone on being named to every client.
_TOOL_NAME = re.compile(
    r"\b(?:tool|design|sch|obj|pcb|lib|proj|audit|part|easyeda|kicad|app)"
    r"_[a-z][a-z_]+\b")


def _tools_named(text: str) -> set[str]:
    """Every tool name in the preamble, not only the tool_ ones.

    The narrower version of this only matched tool_guide and
    tool_catalog, so the paragraph that tells every client which tool
    draws a schematic named four tools no guard checked. A renamed or
    retired design_ tool would have gone on being recommended to every
    client that connects.
    """
    return set(_TOOL_NAME.findall(text))


def test_the_preamble_is_not_empty():
    assert SERVER_INSTRUCTIONS.strip()


def test_it_actually_reaches_the_client():
    """A preamble built and never handed over teaches nobody anything."""
    from eda_agent.server import mcp

    assert mcp.instructions, (
        "FastMCP was constructed without instructions, so nothing the "
        "preamble says is ever seen")
    assert "tool_guide" in mcp.instructions


@pytest.mark.parametrize("backend", _BACKENDS)
def test_every_tool_the_preamble_names_exists(backend):
    """Each backend's own wording, against its own surface.

    The wording is not shared: design_execute_plan is Altium-only, and
    the paragraph that names it was going out to KiCad clients too,
    telling them to call five tools that backend never registers.
    """
    text = build_server_instructions("full", backend)
    for name in _tools_named(text):
        assert name in _SURFACES[backend], (
            f"the {backend} preamble tells the client to use {name!r}, "
            f"which does not exist on that backend")


@pytest.mark.parametrize("backend", _BACKENDS)
def test_a_backend_with_the_engine_says_so(backend):
    """The paragraph is dropped when the engine is absent, not faked."""
    text = build_server_instructions("full", backend)
    has_engine = "design_layout_schematic" in _SURFACES[backend]
    assert ("DO NOT DRAW A SCHEMATIC BY HAND" in text) is has_engine, (
        f"{backend} {'has' if has_engine else 'does not have'} the layout "
        f"engine, and its preamble says the opposite")


def test_it_names_at_least_the_two_it_is_for():
    named = _tools_named(SERVER_INSTRUCTIONS)
    assert {"tool_guide", "tool_catalog"} <= named


@pytest.mark.parametrize("namespace", ["lib_", "pcb_", "sch_", "obj_"])
def test_every_namespace_it_teaches_is_real(namespace):
    """The document-to-namespace split is the preamble's central claim."""
    assert namespace in SERVER_INSTRUCTIONS
    assert any(t.startswith(namespace) for t in _SURFACES["altium"]), (
        f"the preamble teaches the {namespace} namespace, which no tool "
        f"uses any more")


def test_it_names_the_tool_that_draws_a_schematic():
    """The preamble's whole point is redirecting a by-hand schematic."""
    named = _tools_named(SERVER_INSTRUCTIONS)
    assert "design_execute_plan" in named


def test_the_minimal_wording_does_not_name_an_unadvertised_tool():
    """Under minimal a client sees two tools. Telling it to call a third
    is a dead end for exactly the clients that most need the pointer."""
    advertised = _surface("altium", "minimal")
    assert "tool_guide" not in advertised, (
        "this test encodes the minimal toolset as two tools; if tool_guide "
        "is now advertised there, the full wording applies and this guard "
        "should be retired")

    text = build_server_instructions("minimal", "altium")
    assert "through tool_invoke" in text
    assert "call tool_guide" not in text
    # A named tool need not be ADVERTISED under minimal, but it must
    # exist, because tool_invoke dispatches across the whole captured
    # surface. A name that is on neither list is a dead end.
    for name in _tools_named(text):
        assert name in advertised or name in _SURFACES["altium"], (
            f"the minimal preamble names {name!r}, which a minimal client "
            f"can neither see nor reach")


def test_the_full_wording_says_call_it_directly():
    text = build_server_instructions("full", "altium")
    assert "call tool_guide" in text
    assert "through tool_invoke" not in text


def test_an_unknown_toolset_falls_back_rather_than_crashing():
    """register_backend tolerates a stray value, so this must too."""
    assert build_server_instructions("nonsense").strip()
    assert build_server_instructions("").strip()


def test_the_mils_claim_matches_the_rest_of_the_surface():
    """Stated to every client, so it has to hold on every backend."""
    assert "mils" in SERVER_INSTRUCTIONS
    for backend in _BACKENDS:
        assert any("mils" in (t or "") for t in _tool_descriptions(backend)), (
            f"the preamble promises mils everywhere but no {backend} tool "
            f"documents them")


def _tool_descriptions(backend: str):
    """Same restore discipline as _surface: this registers too."""
    from eda_agent.core.backends import _REGISTERED, set_active_backend
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    previous = _REGISTERED
    try:
        registry = ToolRegistry()
        register_backend(registry, backend, "full")
        return [t.description for t in asyncio.run(registry.list_tools())]
    finally:
        set_active_backend(previous or "")


# --------------------------------------------------------------------------
# UI automation is a last resort, and only Altium has it
# --------------------------------------------------------------------------

def test_altium_is_told_ui_automation_is_a_last_resort():
    """Three sessions in one week fell back to driving dialogs.

    Placement ended in the Place Part dialog and a crashed Altium. The
    preamble is the one place every agent reads before choosing a tool.
    """
    text = build_server_instructions("full", "altium")
    assert "last resort" in text
    assert "app_click_menu" in text, (
        "name the tools, or an agent does not recognise the ones meant")


@pytest.mark.parametrize("backend", ["kicad", "easyeda"])
def test_backends_without_ui_tools_are_not_told_about_them(backend):
    """KiCad and EasyEDA register no app_ interface tools."""
    text = build_server_instructions("full", backend)
    assert "app_click_menu" not in text
    assert "last resort" not in text


def test_the_ui_paragraph_reaches_tool_guide_the_same_way_minimal_can():
    """Under minimal, 'call tool_guide' names a tool the client cannot see."""
    text = build_server_instructions("minimal", "altium")
    assert "last resort" in text
    assert "call tool_guide" not in text
